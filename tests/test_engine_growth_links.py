"""Integrate's growth links (spec 2026-10-20 §4): the touch mask and the vector-side links."""

from datetime import date

import geopandas as gpd
from shapely.geometry import box

from deforestation_alerts.engine.growth_links import burn_touch_mask, link_parents
from deforestation_alerts.project.growth import Candidate, Node
from deforestation_alerts.project.store_schema import GROWS_FROM_AUTO

from tests._integrate_helpers import GRID

RES = GRID.resolution


def _px(row, col, rows=1, cols=1):
    """A pixel-edged rectangle of the grid, in the grid's CRS."""
    x0, y0 = GRID.xmin + col * RES, GRID.ymax - row * RES
    return box(x0, y0 - rows * RES, x0 + cols * RES, y0)


def _candidate(shape_grid, set_id="dec", alert_id=0, run_id="r1"):
    shape = gpd.GeoSeries([shape_grid], crs=GRID.epsg).to_crs(epsg=4326).iloc[0]  # stored as EPSG:4326
    node = Node(set_id=set_id, alert_id=alert_id, period_id="p", period_start=date(2025, 12, 1),
                period_end=date(2025, 12, 31), status="Confirmed", area_ha=1.0, date_min=None, date_max=None,
                shape=shape, links=())
    return Candidate(node, run_id)


def _clusters(shapes, below_min):
    return gpd.GeoDataFrame({"below_min": below_min, "count": range(len(shapes))}, geometry=shapes, crs=GRID.epsg)


def test_link_parents_finds_edge_and_corner_neighbours():
    parent = _candidate(_px(10, 10, 2, 2))
    vgdf = _clusters([_px(12, 12), _px(10, 12), _px(10, 13)], [False, False, False])  # corner, edge, one px away
    out = link_parents(vgdf, [parent], GRID)
    assert list(out[GROWS_FROM_AUTO]) == [["dec#0@r1"], ["dec#0@r1"], []]


def test_link_parents_links_overlapping_parents():
    shape = _px(10, 10, 2, 2)
    cands = [_candidate(shape, "set_b", 3, "rb"), _candidate(shape, "set_a", 1, "ra")]
    out = link_parents(_clusters([_px(12, 10)], [True]), cands, GRID)
    assert list(out[GROWS_FROM_AUTO]) == [["set_a#1@ra", "set_b#3@rb"]]


def test_link_parents_drops_unlinked_small():
    parent = _candidate(_px(10, 10))
    vgdf = _clusters([_px(30, 30, 5, 5), _px(20, 20), _px(11, 11)], [False, True, True])
    out = link_parents(vgdf, [parent], GRID)
    assert list(out["count"]) == [0, 2] and list(out.index) == [0, 1]
    assert list(out[GROWS_FROM_AUTO]) == [[], ["dec#0@r1"]]


def test_link_parents_without_candidates_keeps_the_big_ones():
    out = link_parents(_clusters([_px(0, 0, 5, 5), _px(20, 20)], [False, True]), [], GRID)
    assert list(out["count"]) == [0] and list(out[GROWS_FROM_AUTO]) == [[]]


def test_burn_touch_mask_none_without_candidates(tmp_path):
    assert burn_touch_mask([], GRID, tmp_path / "touch.tif") is None
    assert not (tmp_path / "touch.tif").exists()


def test_burn_touch_mask_covers_the_ring_around_the_parent(tmp_path):
    import rasterio

    out = burn_touch_mask([_candidate(_px(10, 10, 2, 2))], GRID, tmp_path / "touch.tif")
    with rasterio.open(out) as src:
        m = src.read(1)
        assert str(src.dtypes[0]) == "int32"
    assert m[9:13, 9:13].all()  # the parent and its 8-neighbour ring
    assert int(m.sum()) == 16
