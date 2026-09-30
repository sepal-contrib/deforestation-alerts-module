"""Download & harmonize rows, offline: native download, harmonize onto the grid, the runner.

Earth Engine is replaced by a fake ``export_native`` that writes a raster in
EPSG:4326 (the predefined sources' native CRS) over the AOI, so every
harmonized file here went through the real alignment onto the UTM grid.
"""

import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer

from deforestation_alerts.engine import catalogue, download, raster_io, runner
from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import harmonize as hz
from deforestation_alerts.project.models import (
    AoiRecord, GridSpec, Period, Project, add_predefined_source,
)
from deforestation_alerts.scripts import gee_progress

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=20)
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
RKEY, HKEY = fp.raw_key(JAN.id, "GLAD-L"), fp.harm_key(JAN.id, "GLAD-L")
# evaluated catalogue.meta_request("GLAD-L"): UpdResult holds 2025 and 2026
BANDS = ["conf25", "alertDate25", "conf26", "alertDate26"]
META = {"end": "2026-06-01", "bands": BANDS}
BEFORE_WINDOW = {"end": "2025-06-01", "bands": BANDS}  # the window starts 2025-10-03

# The grid's footprint in WGS84, padded: the AOI the fake download covers.
_W, _S, _E, _N = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True).transform_bounds(
    GRID.xmin, GRID.ymax - GRID.height * GRID.resolution, GRID.xmin + GRID.width * GRID.resolution, GRID.ymax
)
AOI_BOUNDS = (_W - 0.001, _S - 0.001, _E + 0.001, _N + 0.001)
MID_LAT = (_S + _N) / 2
NATIVE_RES = 0.00005  # degrees, ~5.5 m


def _write(path, alert, date_band=None, count=2):
    """An on-grid raster (masks, and raws that already sit on the grid)."""
    with raster_io.write_grid_raster(path, GRID, count=count) as dst:
        dst.write(alert.astype("int32"), 1)
        if count == 2:
            dst.write((np.zeros_like(alert) if date_band is None else date_band).astype("int32"), 2)
    return path


def _write_native(path, crs="EPSG:4326", count=2, dtype="int32"):
    """A raw download as Earth Engine returns it: EPSG:4326 over the AOI, alerts
    (code 1, dated 2026010) north of the grid's middle latitude only."""
    minx, miny, maxx, maxy = AOI_BOUNDS
    width, height = math.ceil((maxx - minx) / NATIVE_RES), math.ceil((maxy - miny) / NATIVE_RES)
    lat = maxy - (np.arange(height) + 0.5) * NATIVE_RES
    alert = np.repeat((lat > MID_LAT).astype(dtype)[:, None], width, axis=1)
    profile = dict(driver="GTiff", dtype=dtype, nodata=0, count=count, width=width, height=height,
                   crs=crs, transform=Affine(NATIVE_RES, 0, minx, 0, -NATIVE_RES, maxy))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(alert, 1)
        if count == 2:
            dst.write(np.where(alert > 0, 2026010, 0).astype(dtype), 2)
    return Path(path)


@pytest.fixture
def job_factory(tmp_path, monkeypatch):
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=AOI_BOUNDS)
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    p.periods[JAN.id] = JAN
    calls = []

    def fake_export(image, key_or_native, aoi, out_path, *, on_tiles=None, on_wait=None):
        calls.append((image, key_or_native, aoi, Path(out_path)))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        return _write_native(out_path)

    monkeypatch.setattr(runner, "export_native", fake_export)
    monkeypatch.setattr(runner.catalogue, "build_image", lambda *a, **k: "IMAGE")

    def make(force=False, meta=META):
        return runner.SourceJob(project=p.model_copy(deep=True), pdir=tmp_path, period=JAN,
                                source="GLAD-L", meta=meta, aoi="AOI", force=force)

    return p, make, calls


def test_run_writes_raw_and_harmonized(job_factory, tmp_path):
    p, make, calls = job_factory
    arts = runner.run_source_row(make())
    assert set(arts) == {RKEY, HKEY}
    assert calls == [("IMAGE", "GLAD-L", "AOI", tmp_path / arts[RKEY].rel_path)]
    runner.apply_artifacts(p, arts)
    harm = tmp_path / arts[HKEY].rel_path
    raster_io.check_header(harm, GRID)
    with rasterio.open(harm) as src:
        alert, dates = src.read(1), src.read(2)
    assert alert[2, 2] == 1 and dates[2, 2] == 2026010  # north of the middle latitude
    assert alert[17, 2] == 0 and dates[17, 2] == 0  # south of it
    rows = runner.plan_prepare(p, tmp_path, JAN)
    assert rows[0].ready


def test_second_run_is_a_no_op_unless_forced(job_factory):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    assert runner.run_source_row(make()) == {}  # make() copies p, which now has the artifacts
    forced = runner.run_source_row(make(force=True))
    assert forced[RKEY].content_id != p.artifacts[RKEY].content_id
    assert len(calls) == 2


def test_grid_change_reharmonizes_without_downloading(job_factory, tmp_path):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    raw_before = p.artifacts[RKEY]
    p.grid = GRID.model_copy(update={"resolution": 20, "width": 15, "height": 10})
    arts = runner.run_source_row(make())
    assert set(arts) == {HKEY} and len(calls) == 1
    runner.apply_artifacts(p, arts)
    assert p.artifacts[RKEY] == raw_before
    raster_io.check_header(tmp_path / arts[HKEY].rel_path, p.grid)
    assert runner.plan_prepare(p, tmp_path, JAN)[0].ready


def test_uncovered_window_records_no_data_without_download(job_factory):
    p, make, calls = job_factory
    arts = runner.run_source_row(make(meta=BEFORE_WINDOW))
    art = arts[RKEY]
    assert art.no_data and art.rel_path == "" and calls == []


def test_no_data_deletes_the_superseded_files(job_factory, tmp_path):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    old = [tmp_path / p.artifacts[k].rel_path for k in (RKEY, HKEY)]
    assert all(f.exists() for f in old)
    arts = runner.run_source_row(make(force=True, meta=BEFORE_WINDOW))
    assert set(arts) == {RKEY} and arts[RKEY].no_data
    assert not any(f.exists() for f in old)
    runner.apply_artifacts(p, arts)
    row = runner.plan_prepare(p, tmp_path, JAN)[0]
    assert row.raw is fp.Status.NO_DATA and not row.needs_run and not row.ready


def test_missing_catalogue_metadata_is_a_clear_error(job_factory):
    p, make, calls = job_factory
    with pytest.raises(ValueError, match="metadata"):
        runner.run_source_row(make(meta=None))
    assert calls == []


def test_not_enough_space_for_the_download_fails_before_it(job_factory):
    p, make, calls = job_factory
    p.aoi = p.aoi.model_copy(update={"bounds": (-180.0, -90.0, 180.0, 90.0)})  # ~17 TB at 0.00025 deg
    with pytest.raises(raster_io.NotEnoughSpaceError):
        runner.run_source_row(make())
    assert calls == []


def test_not_enough_space_for_harmonizing_fails_before_it(job_factory, tmp_path):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    p.grid = GRID.model_copy(update={"width": 10**6, "height": 10**6})  # 8 TB uncompressed
    with pytest.raises(raster_io.NotEnoughSpaceError):
        runner.run_source_row(make())
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.part"))


def test_harmonize_keep_and_exclude_masks(tmp_path):
    a = np.zeros((GRID.height, GRID.width), dtype="int64")
    a[0:10, :] = 1
    raw = _write(tmp_path / "raw.tif", a, np.where(a > 0, 2026001, 0))
    keep = np.zeros_like(a)
    keep[:, 0:15] = 1  # forest: keep only the left half
    excl = np.zeros_like(a)
    excl[0:5, :] = 1  # plantations: drop the top 5 rows
    km = _write(tmp_path / "keep.tif", keep, count=1)
    em = _write(tmp_path / "excl.tif", excl, count=1)
    out = hz.harmonize(raw, [(km, "keep"), (em, "exclude")], tmp_path / "h.tif", GRID)
    with rasterio.open(out) as src:
        alert, dates = src.read(1), src.read(2)
    assert alert[7, 3] == 1 and dates[7, 3] == 2026001  # inside keep, outside exclude
    assert alert[7, 20] == 0 and dates[7, 20] == 0  # outside keep
    assert alert[2, 3] == 0 and dates[2, 3] == 0  # excluded


def test_harmonized_file_meets_the_raster_contract(tmp_path):
    grid = GRID.model_copy(update={"width": 600, "height": 520})  # large enough for an overview
    rng = np.random.default_rng(0)
    alert = rng.integers(0, 3, size=(grid.height, grid.width))
    dates = np.where(alert > 0, rng.integers(2026001, 2026032, size=alert.shape), 0)
    raw = tmp_path / "raw.tif"
    with raster_io.write_grid_raster(raw, grid, count=2) as dst:
        dst.write(alert.astype("int32"), 1)
        dst.write(dates.astype("int32"), 2)
    out = hz.harmonize(raw, [], tmp_path / "h.tif", grid)
    assert out == tmp_path / "h.tif" and not (tmp_path / "h.tif.part").exists()
    raster_io.check_header(out, grid)
    with rasterio.open(out) as src:
        assert src.dtypes == ("int32", "int32") and src.nodata == 0
        assert src.block_shapes == [(512, 512), (512, 512)]
        assert src.compression.value == "DEFLATE"
        assert src.overviews(1) == [2] and src.tags(ns="rio_overview").get("resampling") == "nearest"
        np.testing.assert_array_equal(src.read(1), alert)
        np.testing.assert_array_equal(src.read(2), dates)
        ovr = src.read(1, out_shape=(grid.height // 2, grid.width // 2))
    assert set(np.unique(ovr)) <= {0, 1, 2}  # nearest: no averaged codes


def test_failed_harmonize_leaves_nothing(tmp_path):
    a = np.ones((GRID.height, GRID.width), dtype="int64")
    raw = _write(tmp_path / "raw.tif", a, a * 2026001)
    off_grid = tmp_path / "mask.tif"
    with raster_io.write_grid_raster(off_grid, GRID.model_copy(update={"xmin": 500010}), count=1):
        pass
    with pytest.raises(raster_io.GridMismatchError):
        hz.harmonize(raw, [(off_grid, "keep")], tmp_path / "h.tif", GRID)
    assert not (tmp_path / "h.tif").exists() and not (tmp_path / "h.tif.part").exists()


class _Img:
    """Stands in for an ee.Image: export_native only calls select/toInt32."""

    def select(self, bands):
        return self

    def toInt32(self):
        return self


def _fake_download(seen=None, **native_kw):
    def fake(image, filename, **kw):
        if seen is not None:
            seen.update(kw, filename=filename)
        _write_native(filename, **native_kw)

    return fake


def test_export_native_downloads_in_the_native_projection(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(download, "download_ee_image", _fake_download(seen))
    out = download.export_native(_Img(), "GLAD-L", "AOI", tmp_path / "raw" / "r.tif")
    assert out == tmp_path / "raw" / "r.tif" and out.exists()
    assert not (tmp_path / "raw" / "r.tif.part").exists()
    assert seen.pop("filename") == str(out) + ".part"
    assert seen == {"region": "AOI", "crs": "EPSG:4326", "scale": catalogue.NATIVE["GLAD-L"][1],
                    "dtype": "int32", "unmask_value": 0, "nodata_value": 0}


def test_export_native_takes_an_explicit_native_projection(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(download, "download_ee_image", _fake_download(seen))
    download.export_native(_Img(), ("EPSG:4326", 30.0), "AOI", tmp_path / "r.tif")
    assert (seen["crs"], seen["scale"]) == ("EPSG:4326", 30.0)


@pytest.mark.parametrize("wrong", [{"crs": "EPSG:32721"}, {"count": 1}, {"dtype": "int16"}],
                         ids=["crs", "bands", "dtype"])
def test_export_native_rejects_an_unexpected_file(tmp_path, monkeypatch, wrong):
    monkeypatch.setattr(download, "download_ee_image", _fake_download(**wrong))
    with pytest.raises(download.RawRasterError):
        download.export_native(_Img(), "GLAD-L", "AOI", tmp_path / "r.tif")
    assert not (tmp_path / "r.tif").exists() and not (tmp_path / "r.tif.part").exists()


def test_export_native_reports_a_queued_download(tmp_path, monkeypatch):
    def queued(image, filename, **kw):
        gee_progress.notify_download_wait()  # what download_ee_image does when the lock is taken
        _write_native(filename)

    waits = []
    monkeypatch.setattr(download, "download_ee_image", queued)
    download.export_native(_Img(), "GLAD-L", "AOI", tmp_path / "r.tif", on_wait=lambda: waits.append(1))
    assert waits == [1]


def test_native_size_estimate():
    assert download.native_pixel_size(catalogue.NATIVE["GLAD-L"]) == pytest.approx(0.00025)
    assert download.native_pixel_size(catalogue.NATIVE["GLAD-S2"]) == pytest.approx(0.0001)
    # a 0.1 degree square at GLAD-L's 0.00025 degrees: 400 x 400 px, two Int32 bands
    assert download.estimate_native_bytes((-55.1, -10.1, -55.0, -10.0), "GLAD-L") == pytest.approx(
        400 * 400 * 8, rel=0.01
    )
