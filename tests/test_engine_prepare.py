"""Download & harmonize rows, offline: native download, harmonize onto the grid, the runner.

Earth Engine is replaced by a fake ``export_native`` that writes a raster in
EPSG:4326 (the predefined sources' native CRS) over the AOI, so every
harmonized file here went through the real alignment onto the UTM grid.
"""

import math
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer

from deforestation_alerts.engine import catalogue, download, raster_io, runner
from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import harmonize as hz
from deforestation_alerts.project.models import (
    AoiRecord, GridSpec, MaskLayer, Period, Project, add_predefined_source,
)
from deforestation_alerts.scripts import gee_progress

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=20)
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
RKEY, HKEY = fp.raw_key(JAN.id, "GLAD-L"), fp.harm_key(JAN.id, "GLAD-L")
# evaluated catalogue.meta_request("GLAD-L"): UpdResult holds 2025 and 2026
BANDS = ["conf25", "alertDate25", "conf26", "alertDate26"]
META = {"end": "2026-06-01", "bands": BANDS}

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
def job_factory(tmp_path, monkeypatch, cover):
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=AOI_BOUNDS)
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    cover(p, "GLAD-L")  # the runner refuses a row without a coverage record
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


def test_missing_catalogue_metadata_is_a_clear_error(job_factory):
    p, make, calls = job_factory
    with pytest.raises(ValueError, match="metadata"):
        runner.run_source_row(make(meta=None))
    assert calls == []


@pytest.fixture
def disk_100_gb(monkeypatch):
    """The project disk reports 100 GB free, whatever disk the tests run on."""
    usage = SimpleNamespace(free=100 * 10**9)
    monkeypatch.setattr(raster_io, "shutil", SimpleNamespace(disk_usage=lambda path: usage))


def test_not_enough_space_for_the_download_fails_before_it(job_factory, disk_100_gb):
    p, make, calls = job_factory
    # 8.3 TB uncompressed at 0.00025 deg: the gate asks for 2 x 5 % of it, ~830 GB
    p.aoi = p.aoi.model_copy(update={"bounds": (-180.0, -90.0, 180.0, 90.0)})
    with pytest.raises(raster_io.NotEnoughSpaceError, match="needs 830 GB free"):
        runner.run_source_row(make())
    assert calls == []


def test_not_enough_space_for_harmonizing_fails_before_it(job_factory, tmp_path, disk_100_gb):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    # 8 TB uncompressed: the gate asks for 2 x 5 % of it, 800 GB
    p.grid = GRID.model_copy(update={"width": 10**6, "height": 10**6})
    with pytest.raises(raster_io.NotEnoughSpaceError, match="needs 800 GB free"):
        runner.run_source_row(make())
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.part"))


def test_not_enough_space_to_harmonize_a_download_fails_before_downloading(job_factory, tmp_path, disk_100_gb):
    """The raw file fits but its harmonized file would not: hours of download
    must not come first (the harmonize demand can be ~7x the raw one)."""
    p, make, calls = job_factory
    p.grid = GRID.model_copy(update={"width": 10**6, "height": 10**6})  # needs 800 GB, as above
    with pytest.raises(raster_io.NotEnoughSpaceError, match="needs 800 GB free"):
        runner.run_source_row(make())
    assert calls == []
    assert not [f for f in tmp_path.rglob("*") if f.is_file()]


def _record_space_checks(monkeypatch, *, fail_at=None):
    """Record every ``(folder, needed bytes)`` the runner checks; the check
    number ``fail_at`` (1-based) raises instead of passing."""
    seen = []

    def check(folder, needed):
        seen.append((Path(folder), needed))
        if len(seen) == fail_at:
            raise raster_io.NotEnoughSpaceError(f"needs {needed} bytes")

    monkeypatch.setattr(runner, "ensure_free_space", check)
    return seen


def test_the_row_gates_ask_for_twice_the_compressed_estimate(job_factory, tmp_path, monkeypatch):
    """User decision 2026-10-01: every gate uses the compressed estimate, 5 % of
    the uncompressed size, twice over (the .part and the file it replaces)."""
    p, make, calls = job_factory
    seen = _record_space_checks(monkeypatch)
    arts = runner.run_source_row(make())
    raw = 2 * raster_io.compressed_estimate(download.estimate_native_bytes(AOI_BOUNDS, "GLAD-L"))
    harm = 2 * raster_io.compressed_estimate(gridmod.estimate_bytes(GRID))
    raw_dir, harm_dir = (tmp_path / arts[k].rel_path for k in (RKEY, HKEY))
    # raw before the download, harmonize before the download and again before harmonizing
    assert seen == [(raw_dir.parent, raw), (harm_dir.parent, harm), (harm_dir.parent, harm)]
    assert harm == 2 * math.ceil(GRID.width * GRID.height * 2 * 4 * 0.05)  # 480 bytes for 30 x 20 px

    # a grid change re-harmonizes only: one harmonize check, the same compressed figure
    runner.apply_artifacts(p, arts)
    p.grid = GRID.model_copy(update={"resolution": 20, "width": 15, "height": 10})
    seen.clear()
    runner.run_source_row(make())
    assert seen == [(harm_dir.parent, 2 * raster_io.compressed_estimate(gridmod.estimate_bytes(p.grid)))]
    assert len(calls) == 1


# Paraguay, the spec §14 gate run: GAUL-2015 ADM0 194's bounds (read from Earth
# Engine) and the final review's 10 m grid in EPSG:32721 (those bounds give
# 88,194 x 93,168 px).
PARAGUAY_BOUNDS = (-62.6452, -27.5876, -54.2593, -19.2871)
PARAGUAY_10M = GridSpec(epsg="EPSG:32721", resolution=10, xmin=200000, ymax=7870000, width=88_236, height=93_387)


@pytest.mark.parametrize("source", ["GLAD-L", "RADD"])
def test_a_national_10m_row_needs_under_7_gb(job_factory, tmp_path, monkeypatch, cover, source):
    """Twice the uncompressed estimate asked for ~132 GB to harmonize Paraguay
    at 10 m; the compressed estimate asks for ~6.6 GB, and RADD's 10 m download
    for ~6.9 GB. The harmonize gate fails here, so nothing is downloaded."""
    p, make, calls = job_factory
    p.aoi = p.aoi.model_copy(update={"bounds": PARAGUAY_BOUNDS})
    p.grid = PARAGUAY_10M
    add_predefined_source(p, "RADD")
    cover(p, source)
    seen = _record_space_checks(monkeypatch, fail_at=2)
    job = runner.SourceJob(project=p.model_copy(deep=True), pdir=tmp_path, period=JAN,
                           source=source, meta=META, aoi="AOI")
    with pytest.raises(raster_io.NotEnoughSpaceError):
        runner.run_source_row(job)
    assert calls == []
    (_, raw), (_, harm) = seen
    assert raw == 2 * raster_io.compressed_estimate(download.estimate_native_bytes(PARAGUAY_BOUNDS, source))
    assert raw < 7e9  # was 17.8 GB (GLAD-L) and 138 GB (RADD)
    assert harm == 2 * raster_io.compressed_estimate(88_236 * 93_387 * 2 * 4)
    assert 6.5e9 < harm < 7e9  # was 131.8 GB


def test_a_missing_mask_fails_before_downloading(job_factory):
    p, make, calls = job_factory
    p.masks["forest"] = MaskLayer(name="forest", origin="gee", ref="users/x/forest")
    with pytest.raises(RuntimeError, match="mask"):
        runner.run_source_row(make())
    assert calls == []


def _harmonize_raises(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk I/O error")

    monkeypatch.setattr(runner, "harmonize", boom)


def _space_gone_after_download(monkeypatch):
    """The disk fills up while the download runs: the post-download check fails."""
    real, seen = runner.ensure_free_space, []

    def check(folder, needed):
        seen.append(folder)
        if len(seen) > 2:  # the two pre-download checks passed
            raise raster_io.NotEnoughSpaceError("needs 1 GB free")
        return real(folder, needed)

    monkeypatch.setattr(runner, "ensure_free_space", check)


@pytest.mark.parametrize("fail", [_harmonize_raises, _space_gone_after_download],
                         ids=["harmonize-raises", "space-gone"])
@pytest.mark.parametrize("force", [False, True], ids=["first-run", "forced-refresh"])
def test_a_harmonize_failing_after_a_download_keeps_the_raw_record(job_factory, tmp_path, monkeypatch, fail, force):
    """The raw file is on disk: the failure carries its record, so the next run
    re-harmonizes without downloading, and a forced refresh's new bytes are
    recorded under a new content id instead of the old one (spec §5.3)."""
    p, make, calls = job_factory
    if force:
        runner.apply_artifacts(p, runner.run_source_row(make()))
    old_raw = p.artifacts.get(RKEY)
    n_calls = len(calls)
    with monkeypatch.context() as m:
        fail(m)
        with pytest.raises(runner.HarmonizeFailed) as info:
            runner.run_source_row(make(force=force))
    assert len(calls) == n_calls + 1
    assert set(info.value.artifacts) == {RKEY} and info.value.__cause__ is not None
    raw = info.value.artifacts[RKEY]
    assert (tmp_path / raw.rel_path).exists()
    assert old_raw is None or raw.content_id != old_raw.content_id
    assert not list(tmp_path.rglob("*.part"))

    runner.apply_artifacts(p, info.value.artifacts)
    row = runner.plan_prepare(p, tmp_path, JAN)[0]
    assert row.raw is fp.Status.CURRENT and row.needs_run and not row.ready
    arts = runner.run_source_row(make())
    assert set(arts) == {HKEY} and len(calls) == n_calls + 1  # no second download
    runner.apply_artifacts(p, arts)
    assert runner.plan_prepare(p, tmp_path, JAN)[0].ready


def test_a_harmonize_failing_without_a_download_raises_as_is(job_factory, monkeypatch):
    p, make, calls = job_factory
    runner.apply_artifacts(p, runner.run_source_row(make()))
    p.grid = GRID.model_copy(update={"resolution": 20, "width": 15, "height": 10})
    _harmonize_raises(monkeypatch)
    with pytest.raises(OSError, match="disk I/O error") as info:
        runner.run_source_row(make())
    assert not isinstance(info.value, runner.HarmonizeFailed)


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
    hz.harmonize(raw, [(km, "keep"), (em, "exclude")], tmp_path / "h.tif", GRID)
    out = tmp_path / "h.tif"
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
    hz.harmonize(raw, [], tmp_path / "h.tif", grid)
    out = tmp_path / "h.tif"
    assert not (tmp_path / "h.tif.part").exists()
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
