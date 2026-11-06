import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import box

from deforestation_alerts.engine import masks, raster_io
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=60, height=40)


def test_the_aoi_raster_is_one_inside_the_polygon(tmp_path):
    # the western half of the grid, as a WGS84 polygon
    t = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True)
    x0, y0, x1, y1 = 500000, 8900000 - 400, 500300, 8900000
    poly = box(*t.transform_bounds(x0, y0, x1, y1))
    side = tmp_path / "aoi.geojson"
    gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326").to_file(side, driver="GeoJSON")
    out = masks.build_aoi_raster(side, GRID, tmp_path / "masks" / "aoi.tif")
    raster_io.check_header(out, GRID)
    with rasterio.open(out) as r:
        a = r.read(1)
        assert r.dtypes == ("int32",)
    assert a[:, :28].min() == 1 and a[:, 32:].max() == 0  # a pixel of slack at the reprojected edge


def test_tile_windows_cover_the_grid_once(tmp_path):
    seen = np.zeros((GRID.height, GRID.width), int)
    for w in raster_io.tile_windows(GRID, size=16):
        seen[w.row_off:w.row_off + w.height, w.col_off:w.col_off + w.width] += 1
    assert (seen == 1).all()
