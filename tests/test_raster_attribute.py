"""Tier-1 tests for per-alert raster attribution (no GEE, no UI)."""

import numpy as np
import pytest
import geopandas as gpd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from deforestation_alerts.scripts import raster_attribute as ra


def _write_raster(path, bands, *, crs="EPSG:4326", res=1.0, lon0=0.0, lat0=10.0,
                  nodata=None, descriptions=None, dtype="float32"):
    """Write a (multiband) GeoTIFF; top-left origin (lon0, lat0), square pixels."""
    bands = [np.asarray(b, dtype=dtype) for b in bands]
    h, w = bands[0].shape
    transform = from_origin(lon0, lat0, res, res)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype=dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        for i, b in enumerate(bands, start=1):
            dst.write(b, i)
        if descriptions:
            dst.descriptions = tuple(descriptions)
    return str(path)


# rasterio's own Affine products warn on every window read; the tests above
# already show that warning, so the newer tests do not repeat it.
_RASTERIO_MATMUL = pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning")


def test_safe_column():
    assert ra.safe_column("Forest/No Forest") == "forest_no_forest"
    assert ra.safe_column("band 1!!") == "band_1"
    assert ra.safe_column("") == "band"
    assert ra.safe_column(None) == "band"


def test_resolve_column_names_dedups_vs_reserved_and_each_other():
    specs = [
        {"band": "a", "stat": "min", "column": "count"},   # collides with reserved
        {"band": "b", "stat": "max", "column": "forest"},
        {"band": "c", "stat": "mean", "column": "forest"},  # collides with previous
    ]
    out = ra.resolve_column_names(specs, reserved={"count"})
    assert [s["column"] for s in out] == ["count_2", "forest", "forest_2"]


def test_band_names_reads_descriptions_with_fallback(tmp_path):
    path = _write_raster(
        tmp_path / "r.tif",
        [np.zeros((2, 2)), np.zeros((2, 2))],
        descriptions=["forest", None],
    )
    assert ra.band_names(path) == ["forest", "band_2"]


def _gdf(polys, crs="EPSG:4326"):
    return gpd.GeoDataFrame({"id": list(range(len(polys)))}, geometry=polys, crs=crs)


def test_majority_basic(tmp_path):
    # 2x2 raster over lon[0,2] lat[8,10]; three 1s and one 0 -> majority 1.
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [1, 0]]],
                         descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)])
    out = ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "majority", "column": "forest"}])
    assert out["forest"].iloc[0] == 1


def test_majority_tie_breaks_to_smallest(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 2], [2, 1]]], descriptions=["c"])
    gdf = _gdf([box(0, 8, 2, 10)])
    out = ra.attribute_alerts(gdf, path, [{"band": "c", "stat": "majority", "column": "c"}])
    assert out["c"].iloc[0] == 1


def test_mean_min_max_median(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 2], [3, 4]]], descriptions=["v"])
    gdf = _gdf([box(0, 8, 2, 10)])
    specs = [
        {"band": "v", "stat": "mean", "column": "v_mean"},
        {"band": "v", "stat": "min", "column": "v_min"},
        {"band": "v", "stat": "max", "column": "v_max"},
        {"band": "v", "stat": "median", "column": "v_med"},
    ]
    out = ra.attribute_alerts(gdf, path, specs)
    assert out["v_mean"].iloc[0] == 2.5
    assert out["v_min"].iloc[0] == 1
    assert out["v_max"].iloc[0] == 4
    assert out["v_med"].iloc[0] == 2.5


def test_nodata_excluded(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [255, 0]]],
                         nodata=255, descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)])
    out = ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "majority", "column": "forest"}])
    assert out["forest"].iloc[0] == 1  # 255 ignored: {1, 1, 0} -> 1


def test_all_nodata_polygon_is_none(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[255, 255], [255, 255]]],
                         nodata=255, descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)])
    out = ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "mean", "column": "forest"}])
    assert out["forest"].iloc[0] is None


@_RASTERIO_MATMUL
def test_an_alert_smaller_than_a_pixel_takes_the_pixels_it_touches(tmp_path):
    """An alert that covers no pixel centre (a 30 m alert on a 100 m land
    cover, say) used to get no value at all: in the live walk, 32 of 49
    alerts read None from a 110 m GeoTIFF. It now takes the pixels it touches."""
    path = _write_raster(tmp_path / "r.tif", [[[1, 2], [3, 4]]], descriptions=["v"])
    inside_one = box(0.1, 9.1, 0.3, 9.3)  # inside the top-left pixel, off its centre
    straddling = box(0.8, 9.6, 1.2, 9.8)  # across the two top pixels, no centre
    gdf = _gdf([inside_one, straddling])
    specs = [{"band": "v", "stat": "mean", "column": "v_mean"},
             {"band": "v", "stat": "majority", "column": "v_major"}]
    out = ra.attribute_alerts(gdf, path, specs)
    assert out["v_mean"].tolist() == [1, 1.5]
    assert out["v_major"].tolist() == [1, 1]


@_RASTERIO_MATMUL
def test_pixel_centres_still_decide_when_the_alert_covers_any(tmp_path):
    """The fallback is only for alerts that cover no centre: an alert over the
    top-left centre that also touches the other three pixels reads only 1."""
    path = _write_raster(tmp_path / "r.tif", [[[1, 2], [3, 4]]], descriptions=["v"])
    gdf = _gdf([box(0.2, 8.8, 1.2, 9.8)])
    out = ra.attribute_alerts(gdf, path, [{"band": "v", "stat": "mean", "column": "v"}])
    assert out["v"].iloc[0] == 1


@_RASTERIO_MATMUL
def test_a_sub_pixel_alert_over_nodata_is_none(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[255, 2], [3, 4]]], nodata=255, descriptions=["v"])
    gdf = _gdf([box(0.1, 9.1, 0.3, 9.3)])
    out = ra.attribute_alerts(gdf, path, [{"band": "v", "stat": "mean", "column": "v"}])
    assert out["v"].iloc[0] is None


def test_no_overlap_polygon_is_none(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [1, 1]]], descriptions=["forest"])
    gdf = _gdf([box(100, 100, 101, 101)])  # far outside the raster
    out = ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "majority", "column": "forest"}])
    assert out["forest"].iloc[0] is None


def test_reprojects_polygons_to_raster_crs(tmp_path):
    # Raster in 4326; pass the SAME polygon but in EPSG:3857. Result must match.
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [1, 0]]], descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)]).to_crs("EPSG:3857")
    out = ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "majority", "column": "forest"}])
    assert out["forest"].iloc[0] == 1
    assert out.crs == gdf.crs  # original CRS preserved on the returned gdf


def test_missing_band_raises(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [1, 1]]], descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)])
    with pytest.raises(ValueError):
        ra.attribute_alerts(gdf, path, [{"band": "nope", "stat": "min", "column": "x"}])


def test_raster_without_crs_raises(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[[1, 1], [1, 1]]], crs=None, descriptions=["forest"])
    gdf = _gdf([box(0, 8, 2, 10)])
    with pytest.raises(ValueError):
        ra.attribute_alerts(gdf, path, [{"band": "forest", "stat": "min", "column": "x"}])


def test_build_attribute_config_includes_only_checked_and_dedups():
    rows = [
        {"include": True, "band": "forest", "stat": "majority", "column": ""},
        {"include": False, "band": "skip", "stat": "mean", "column": "skip"},
        {"include": True, "band": "count", "stat": "min", "column": "Count"},  # -> safe + dedup
    ]
    cfg = ra.build_attribute_config("/data/x.tif", "local", rows)
    assert cfg["source"] == "/data/x.tif"
    assert cfg["kind"] == "local"
    assert cfg["bands"] == [
        {"band": "forest", "stat": "majority", "column": "forest"},
        {"band": "count", "stat": "min", "column": "count_2"},  # collides with reserved 'count'
    ]


def test_build_attribute_config_empty_when_nothing_included():
    rows = [{"include": False, "band": "a", "stat": "mean", "column": "a"}]
    cfg = ra.build_attribute_config("/x.tif", "local", rows)
    assert cfg["bands"] == []


def test_display_column_is_safe_column_before_the_ends_are_trimmed():
    assert ra.display_column(" Land--cover ") == "_land_cover_"
    assert ra.safe_column(" Land--cover ") == "land_cover"
    assert ra.display_column(None) == "" and ra.safe_column(None) == "band"


def test_build_attribute_config_keeps_a_bands_projection_only_when_known():
    rows = [{"include": True, "band": "B4", "stat": "mean", "column": "b4", "crs": "EPSG:32721", "scale": 10.0},
            {"include": True, "band": "lc", "stat": "majority", "column": "lc", "crs": None, "scale": None}]
    assert ra.build_attribute_config("x", "gee", rows)["bands"] == [
        {"band": "B4", "stat": "mean", "column": "b4", "crs": "EPSG:32721", "scale": 10.0},
        {"band": "lc", "stat": "majority", "column": "lc"},
    ]


def test_reserved_for_adds_every_other_layers_columns():
    """Review focus 3: a layer's columns never take an alert column or
    another layer's; the layer being edited does not reserve its own."""
    a = {"source": "a.tif", "kind": "local", "bands": [{"band": "b1", "stat": "mean", "column": "b1"}]}
    b = {"source": "users/me/b", "kind": "gee", "bands": [{"band": "b1", "stat": "max", "column": "b1_2"},
                                                           {"band": "s", "stat": "min", "column": "slope"}]}
    assert ra.reserved_for([]) == ra.ALERT_RESERVED_COLUMNS
    assert ra.reserved_for([a, b]) == ra.ALERT_RESERVED_COLUMNS | {"b1", "b1_2", "slope"}
    assert ra.reserved_for([a, b], skip=1) == ra.ALERT_RESERVED_COLUMNS | {"b1"}
    assert ra.reserved_for([a, b], skip=0, reserved={"count"}) == {"count", "b1_2", "slope"}
    assert "b1" not in ra.ALERT_RESERVED_COLUMNS  # the shared set is never mutated
