"""The shared banded burn and vector alert shapes onto the grid (spec §5.4)."""

import geopandas as gpd
import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import MergeAlg
from rasterio.windows import Window
from shapely.geometry import Point, box

from deforestation_alerts.engine import raster_io, rasterize
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=40)


def _centre(row, col):
    """The grid-CRS centre of pixel (row, col)."""
    return GRID.xmin + GRID.resolution * (col + 0.5), GRID.ymax - GRID.resolution * (row + 0.5)


def _frame(geoms, dates):
    """Geometries built in the grid's CRS, as the raw GeoParquet holds them (EPSG:4326)."""
    return gpd.GeoDataFrame({"date": dates}, geometry=list(geoms), crs=GRID.epsg).to_crs("EPSG:4326")


def _burn(tmp_path, gdf, side_m, name="out.tif"):
    raw = tmp_path / "raw.parquet"
    gdf.to_parquet(raw)
    out = rasterize.burn_vector_raw(raw, GRID, tmp_path / name, side_m=side_m)
    with rasterio.open(out) as src:
        return src.read(1), src.read(2)


def test_a_30_m_square_burns_3x3_pixels_centred_on_the_point(tmp_path):
    alert, date = _burn(tmp_path, _frame([Point(*_centre(10, 10))], [2026010]), 30)
    want = np.zeros((40, 40), bool)
    want[9:12, 9:12] = True
    assert np.array_equal(alert == 1, want) and alert[~want].sum() == 0
    assert (date[want] == 2026010).all() and date[~want].sum() == 0
    assert alert.dtype == np.int32 and date.dtype == np.int32


def test_a_sub_pixel_side_burns_the_containing_pixel(tmp_path):
    alert, date = _burn(tmp_path, _frame([Point(*_centre(10, 10))], [2026010]), 5)
    assert alert.sum() == 1 and alert[10, 10] == 1 and date[10, 10] == 2026010


def test_overlapping_squares_keep_the_earliest_date(tmp_path):
    a, b = Point(*_centre(10, 10)), Point(*_centre(10, 12))  # 20 m apart, 50 m squares overlap on cols 10-12
    for i, order in enumerate([([a, b], [2026010, 2026005]), ([b, a], [2026005, 2026010])]):
        alert, date = _burn(tmp_path, _frame(*order), 50, name=f"o{i}.tif")
        assert (date[8:13, 10:13] == 2026005).all()
        assert (date[8:13, 8:10] == 2026010).all() and (date[8:13, 13:15] == 2026005).all()
        assert alert.sum() == 5 * 7


def test_polygons_use_pixel_centres_and_slivers_survive(tmp_path):
    x0, y0 = GRID.xmin, GRID.ymax
    square = box(x0 + 100, y0 - 120, x0 + 120, y0 - 100)    # 4 pixel centres
    sliver = box(x0 + 301, y0 - 350, x0 + 303, y0 - 50)     # between centre columns 29.5 and 30.5
    tiny = box(x0 + 200.5, y0 - 304.5, x0 + 204.5, y0 - 300.5)  # 4 x 4 m, no centre
    gdf = _frame([square, sliver, tiny], [2026001, 2026002, 2026003])
    shapes = rasterize.burn_shapes(gdf, GRID, None)
    assert list(shapes.geom_type) == ["Point", "Point", "Polygon"]  # date descending: tiny, sliver, square
    alert, date = _burn(tmp_path, gdf, None)
    assert (date[10:12, 10:12] == 2026001).all() and (date == 2026001).sum() == 4
    assert (date == 2026002).sum() == 1 and np.argwhere(date == 2026002)[0][1] == 30
    assert (date == 2026003).sum() == 1 and date[30, 20] == 2026003
    assert alert.sum() == 6


def test_valued_burn_seams_match_a_whole_grid_burn(tmp_path, monkeypatch):
    pts = [Point(*_centre(r, c)) for r, c in [(7, 5), (8, 7), (15, 20), (16, 22), (16, 5)]]
    gdf = _frame(pts, [2026030, 2026010, 2026020, 2026005, 2026040])
    monkeypatch.setattr(rasterize, "tile_windows", lambda grid: raster_io.row_windows(grid, 8))
    _, banded = _burn(tmp_path, gdf, 50)
    shapes = rasterize.burn_shapes(gdf, GRID, 50)
    whole = features.rasterize(zip(shapes.geometry, shapes["date"]), out_shape=(40, 40), transform=GRID.affine,
                               fill=0, merge_alg=MergeAlg.replace, dtype="int32")
    assert np.array_equal(banded, whole) and (whole > 0).sum() > 0
    assert (banded[7:10] > 0).any() and (banded[15:18] > 0).any()


def test_burn_window_without_values_is_unchanged():
    geoms = gpd.GeoSeries([box(500050, 8899700, 500170, 8899880), box(500200, 8899640, 500310, 8899990)],
                          crs=GRID.epsg)
    w = Window(0, 0, 40, 40)
    for touched in (False, True):
        got = raster_io.burn(geoms, geoms.sindex, GRID, w, all_touched=touched)
        want = features.rasterize(((g, 1) for g in geoms), out_shape=(40, 40), transform=GRID.affine, fill=0,
                                  all_touched=touched, dtype="uint8").astype(bool)
        assert got.dtype == bool and np.array_equal(got, want)
    assert not raster_io.burn(geoms, None, GRID, w).any()


def test_an_empty_frame_writes_an_empty_raster(tmp_path):
    empty = gpd.GeoDataFrame({"date": np.array([], dtype="int64")}, geometry=[], crs="EPSG:4326")
    shapes = rasterize.burn_shapes(empty, GRID, 30)
    assert len(shapes) == 0 and shapes.crs == GRID.epsg and list(shapes.columns) == ["geometry", "date"]
    alert, date = _burn(tmp_path, empty, 30)
    assert not alert.any() and not date.any()
    raster_io.check_header(tmp_path / "out.tif", GRID)
