"""Tier-1 tests for attribute_source: local pass-through, description stamping,
and the config->materialize->attribute integration (no GEE)."""

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
import geopandas as gpd

from deforestation_alerts.scripts import attribute_source as asrc
from deforestation_alerts.scripts import raster_attribute as ra


def _write_raster(path, band, *, descriptions=None):
    arr = np.asarray(band, dtype="float32")
    h, w = arr.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0.0, 10.0, 1.0, 1.0),
    ) as dst:
        dst.write(arr, 1)
        if descriptions:
            dst.descriptions = tuple(descriptions)
    return str(path)


def test_materialize_local_returns_source_unchanged():
    cfg = {"source": "/data/forest.tif", "kind": "local", "bands": []}
    assert asrc.materialize_attribute_raster(cfg, region=None, recipe_folder=None) == "/data/forest.tif"


def test_set_band_descriptions_roundtrip(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[1, 0], [1, 1]])
    asrc._set_band_descriptions(path, ["forest"])
    assert ra.band_names(path) == ["forest"]


def test_attribute_alerts_from_config_local(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[1, 1], [1, 0]], descriptions=["forest"])
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    cfg = {
        "source": path, "kind": "local",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }
    out = asrc.attribute_alerts_from_config(gdf, cfg, region=None, recipe_folder=None)
    assert out["forest"].iloc[0] == 1


def test_attribute_alerts_from_config_noop_when_empty(tmp_path):
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    assert asrc.attribute_alerts_from_config(gdf, None, None, None) is gdf
    empty_cfg = {"source": "x", "kind": "local", "bands": []}
    assert asrc.attribute_alerts_from_config(gdf, empty_cfg, None, None) is gdf
