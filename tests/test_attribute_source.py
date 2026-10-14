"""Tier-1 tests for attribute_source: local pass-through, the per-band Earth
Engine download (faked), description stamping, the download estimate, and the
config->materialize->attribute integration (no GEE)."""

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


# rasterio's ``from_origin`` still multiplies Affines with ``*`` (affine 3 deprecates it)
_AFFINE_MUL = pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning")


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


@_AFFINE_MUL
def test_materialize_local_returns_the_users_raster_with_all_its_bands(tmp_path):
    path = _write_raster(tmp_path / "forest.tif", [[1, 0], [1, 1]], descriptions=["forest"])
    cfg = {"source": path, "kind": "local", "bands": [{"band": "forest", "stat": "majority", "column": "f"}]}
    assert asrc.materialize_attribute_bands(cfg, None, tmp_path) == [(path, cfg["bands"])]


@_AFFINE_MUL
def test_materialize_gee_downloads_each_band_alone_in_its_own_projection(tmp_path, monkeypatch):
    calls = []

    def fake_download(img, filename, region=None, crs=None, scale=None, **kw):
        calls.append((img, crs, scale))
        _write_raster(filename, [[1, 1], [1, 1]])

    class FakeImage:
        def __init__(self, ref, bands=None):
            self.ref, self.bands = ref, bands

        def select(self, bands):
            return FakeImage(self.ref, tuple(bands))

    monkeypatch.setattr(asrc, "download_ee_image", fake_download)
    monkeypatch.setattr(asrc.ee, "Image", FakeImage)
    cfg = {"source": "COPERNICUS/S2_SR/x", "kind": "gee",
           "bands": [{"band": "B4", "stat": "mean", "column": "b4"},
                     {"band": "B11", "stat": "mean", "column": "b11"}]}
    out = asrc.materialize_attribute_bands(cfg, "REGION", tmp_path)
    assert [(c[0].bands, c[1], c[2]) for c in calls] == [(("B4",), None, None), (("B11",), None, None)]
    assert [specs for _, specs in out] == [[cfg["bands"][0]], [cfg["bands"][1]]]
    assert [ra.band_names(p) for p, _ in out] == [["B4"], ["B11"]]


@_AFFINE_MUL
def test_a_band_with_two_statistics_downloads_once(tmp_path, monkeypatch):
    """Review A M4: one download per band, carrying all its statistics."""
    calls = []

    def fake_download(img, filename, region=None, crs=None, scale=None, **kw):
        calls.append(img.bands)
        _write_raster(filename, [[1, 1], [1, 1]])

    class FakeImage:
        def __init__(self, ref, bands=None):
            self.ref, self.bands = ref, bands

        def select(self, bands):
            return FakeImage(self.ref, tuple(bands))

    monkeypatch.setattr(asrc, "download_ee_image", fake_download)
    monkeypatch.setattr(asrc.ee, "Image", FakeImage)
    mean = {"band": "B4", "stat": "mean", "column": "b4_mean"}
    top = {"band": "B4", "stat": "max", "column": "b4_max"}
    b11 = {"band": "B11", "stat": "mean", "column": "b11"}
    cfg = {"source": "COPERNICUS/S2_SR/x", "kind": "gee", "bands": [mean, b11, top]}
    out = asrc.materialize_attribute_bands(cfg, "REGION", tmp_path)
    assert calls == [("B4",), ("B11",)]
    assert [specs for _, specs in out] == [[mean, top], [b11]]
    one = {"kind": "gee", "bands": [dict(mean, crs="EPSG:4326", scale=10.0)]}
    two = {"kind": "gee", "bands": [dict(mean, crs="EPSG:4326", scale=10.0), dict(top, crs="EPSG:4326", scale=10.0)]}
    assert asrc.download_estimate(two, (0.0, 0.0, 0.01, 0.01)) == asrc.download_estimate(one, (0.0, 0.0, 0.01, 0.01))


def test_download_estimate():
    from deforestation_alerts.engine.download import estimate_native_bytes

    bounds = (0.0, 0.0, 0.01, 0.01)
    cfg = {"kind": "gee", "bands": [{"band": "B4", "crs": "EPSG:4326", "scale": 10.0},
                                    {"band": "B11", "crs": "EPSG:4326", "scale": 20.0}]}
    expected = sum(estimate_native_bytes(bounds, ("EPSG:4326", s)) // 2 for s in (10.0, 20.0))
    assert asrc.download_estimate(cfg, bounds) == expected > 0
    assert asrc.download_estimate({"kind": "local", "bands": [{"band": "x"}]}, (0, 0, 1, 1)) == 0


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
        gdf, cfg, region=None, materialize=lambda c, r, d: [(c["source"], c["bands"])]
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
        return [(str(out), config["bands"])]

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


@_AFFINE_MUL
def test_a_saved_config_naming_a_store_column_never_overwrites_it(tmp_path):
    # A layer saved before alert_id was reserved still carries "alert_id":
    # attribution writes it as alert_id_2 and the ids stay as they were.
    path = _write_raster(tmp_path / "forest.tif", [[5, 5], [5, 5]], descriptions=["forest"])
    gdf = gpd.GeoDataFrame({"alert_id": [7]}, geometry=[box(0.1, 8.1, 1.9, 9.9)], crs="EPSG:4326")
    cfg = {"source": path, "kind": "local", "bands": [{"band": "forest", "stat": "mean", "column": "alert_id"}]}
    out = asrc.attribute_alerts_from_config(gdf, cfg, None)
    assert list(out["alert_id"]) == [7]
    assert list(out["alert_id_2"]) == [5]
