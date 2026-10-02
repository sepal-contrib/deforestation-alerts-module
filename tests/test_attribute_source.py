"""Tier-1 tests for attribute_source: local pass-through, description stamping,
and the config->materialize->attribute integration (no GEE)."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
import geopandas as gpd

from deforestation_alerts import gdal_env
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


def test_materialize_local_returns_the_users_raster_unchanged(data_root, tmp_path):
    """`kind == "local"` is a path the user already picked on the local sandbox
    disk (D14), so it is handed straight back."""
    user_tif = data_root / "forest.tif"
    user_tif.write_bytes(b"tif-bytes")
    cfg = {"source": str(user_tif), "kind": "local", "bands": []}

    local = asrc.materialize_attribute_raster(cfg, region=None, folder=tmp_path / "scratch")

    assert local == str(user_tif)
    assert Path(local).read_bytes() == b"tif-bytes"


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
    out = asrc.attribute_alerts_from_config(
        gdf, cfg, region=None, materialize=lambda c, r, folder: c["source"]
    )
    assert out["forest"].iloc[0] == 1


def test_attribute_alerts_from_config_noop_when_empty():
    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    assert asrc.attribute_alerts_from_config(gdf, None, None) is gdf
    empty_cfg = {"source": "x", "kind": "local", "bands": []}
    assert asrc.attribute_alerts_from_config(gdf, empty_cfg, None) is gdf


@pytest.mark.parametrize("band", ["forest", "nope"], ids=["ok", "failed"])
def test_attribute_alerts_from_config_removes_its_scratch_folder(tmp_path, monkeypatch, band):
    """A downloaded raster lives in a private scratch folder for the run: it is
    deleted whether the attribution pass succeeds or raises."""
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(gdal_env, "scratch_dir", lambda: scratch)
    src = _write_raster(tmp_path / "r.tif", [[1, 1], [1, 0]], descriptions=["forest"])
    seen = []

    def fake_materialize(config, region, folder):
        assert scratch in Path(folder).parents
        out = Path(folder) / "attribute_x.tif"
        out.write_bytes(Path(src).read_bytes())
        seen.append(out)
        return str(out)

    gdf = gpd.GeoDataFrame({"id": [0]}, geometry=[box(0, 8, 2, 10)], crs="EPSG:4326")
    cfg = {"source": "users/x/forest", "kind": "gee",
           "bands": [{"band": band, "stat": "majority", "column": band}]}

    if band == "nope":
        with pytest.raises(Exception):
            asrc.attribute_alerts_from_config(gdf, cfg, region=None, materialize=fake_materialize)
    else:
        assert asrc.attribute_alerts_from_config(gdf, cfg, region=None, materialize=fake_materialize)["forest"].iloc[0] == 1

    assert seen and not seen[0].exists()
    assert list(scratch.iterdir()) == []
