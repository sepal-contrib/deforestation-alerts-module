"""Tier-1 tests for attribute_source: local pass-through, description stamping,
and the config->materialize->attribute integration (no GEE)."""

from pathlib import Path

import numpy as np
import pytest
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


def test_materialize_local_returns_the_users_raster_unchanged(ws, tmp_path):
    """`kind == "local"` is a path the user already picked on the local sandbox
    disk (D14), so it is handed straight back -- there is no remote copy to
    stage down any more."""
    remote = ws.results_dir("forest.tif")
    ws.write_bytes(remote, b"tif-bytes")
    cfg = {"source": remote, "kind": "local", "bands": []}

    local = asrc.materialize_attribute_raster(cfg, region=None, workspace=ws)

    assert local == remote
    assert Path(local).read_bytes() == b"tif-bytes"
    assert Path(local).is_relative_to(tmp_path)


def test_set_band_descriptions_roundtrip(tmp_path):
    path = _write_raster(tmp_path / "r.tif", [[1, 0], [1, 1]])
    asrc._set_band_descriptions(path, ["forest"])
    assert ra.band_names(path) == ["forest"]


def test_attribute_alerts_from_config_local(tmp_path, ws):
    path = _write_raster(tmp_path / "r.tif", [[1, 1], [1, 0]], descriptions=["forest"])
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    cfg = {
        "source": path, "kind": "local",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }
    out = asrc.attribute_alerts_from_config(
        gdf, cfg, region=None, workspace=ws, materialize=lambda c, r, w: c["source"]
    )
    assert out["forest"].iloc[0] == 1


def test_attribute_alerts_from_config_noop_when_empty(ws):
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    assert asrc.attribute_alerts_from_config(gdf, None, None, ws) is gdf
    empty_cfg = {"source": "x", "kind": "local", "bands": []}
    assert asrc.attribute_alerts_from_config(gdf, empty_cfg, None, ws) is gdf


def test_attribute_alerts_from_config_releases_the_staged_raster(tmp_path, ws):
    """The staged raster is discarded whether the attribution pass succeeds or
    raises — it is a full copy of the user's raster in the container's tmp."""
    path = _write_raster(tmp_path / "r.tif", [[1, 1], [1, 0]], descriptions=["forest"])
    remote = ws.results_dir("forest.tif")
    ws.write_bytes(remote, Path(path).read_bytes())
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    cfg = {
        "source": remote, "kind": "local",
        "bands": [{"band": "nope", "stat": "majority", "column": "nope"}],
    }

    with pytest.raises(Exception):
        asrc.attribute_alerts_from_config(gdf, cfg, region=None, workspace=ws)

    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []
