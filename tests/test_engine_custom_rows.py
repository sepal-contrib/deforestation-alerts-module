"""Download & harmonize rows for coverage and custom sources, offline."""

from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from shapely.geometry import box

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import runner
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AoiRecord, BandMap, GridSpec, NativeGrid, Period, Project, SourceCoverage, add_custom_source,
    add_predefined_source,
)

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=20)
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
_W, _S, _E, _N = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True).transform_bounds(
    GRID.xmin, GRID.ymax - 200, GRID.xmin + 300, GRID.ymax)
BOUNDS = (_W - 0.001, _S - 0.001, _E + 0.001, _N + 0.001)


def _project(tmp_path):
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    gdf = gpd.GeoDataFrame(geometry=[box(_W, _S, (_W + _E) / 2, _N)], crs="EPSG:4326")  # western half only
    from gui.scripts import aoi_io
    aoi_io.write_sidecar(tmp_path, gdf)
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=BOUNDS, geometry_file=paths.AOI_SIDECAR)
    p.grid = GRID
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    return p


def _file_source(p, tmp_path):
    t = Affine(10, 0, 499900, 0, -10, 8900100)  # the grid's CRS, offset by 10 px
    a = np.full((40, 50), 3, "int16")
    d = np.full((40, 50), 26010, "int16")
    path = tmp_path / "user" / "radd.tif"
    path.parent.mkdir()
    with rasterio.open(path, "w", driver="GTiff", width=50, height=40, count=2, dtype="int16", crs=GRID.epsg,
                       transform=t) as dst:
        dst.write(a, 1)
        dst.write(d, 2)
    return add_custom_source(p, name="tif", origin="file", ref=str(path), asset_type="FILE", format="radd",
                             date_encoding="yyddd", band_map=BandMap(alert=1, date=2),
                             native=NativeGrid(crs=GRID.epsg, scale=10.0, transform=tuple(t)[:6],
                                               bounds=BOUNDS))


def _job(p, tmp_path, source, meta=None, force=False):
    return runner.SourceJob(project=p.model_copy(deep=True), pdir=tmp_path, period=JAN, source=source,
                            meta=meta, aoi="AOI", force=force)


@pytest.fixture
def no_ee(monkeypatch):
    """Records any Earth Engine use; a download fails the test."""
    calls = []

    def export(*a, **k):
        calls.append(a)
        raise AssertionError("Earth Engine was called")

    def build(*a, **k):
        calls.append(a)
        return "IMAGE"

    monkeypatch.setattr(runner, "export_native", export)
    monkeypatch.setattr(runner.catalogue, "build_image", build)
    monkeypatch.setattr(runner.catalogue, "build_custom_image", build)
    return calls


@pytest.mark.parametrize("record, state", [
    (None, "unchecked"),
    (SourceCoverage(aoi_digest="g", checked_on=date(2026, 10, 1), fraction=0.0), "not_covered"),
])
def test_the_runner_refuses_an_uncovered_row_without_earth_engine(tmp_path, no_ee, record, state):
    p = _project(tmp_path)
    add_predefined_source(p, "GLAD-S2")
    if record is not None:
        p.coverage = {"GLAD-S2": record}
    with pytest.raises(runner.CoverageRefused) as e:
        runner.run_source_row(_job(p, tmp_path, "GLAD-S2"))
    assert e.value.state == state and no_ee == [] and not list(tmp_path.rglob("*.tif*"))


def test_the_runner_refuses_a_no_data_window(tmp_path, no_ee):
    from deforestation_alerts.project.models import RegionCoverage
    p = _project(tmp_path)
    add_predefined_source(p, "RADD")
    p.coverage = {"RADD": SourceCoverage(aoi_digest="g", checked_on=date(2026, 10, 1), fraction=1.0,
                                         regions=[RegionCoverage(name="sa", fraction=1.0, first=date(2027, 1, 1),
                                                                 last=date(2027, 6, 1))])}
    with pytest.raises(runner.CoverageRefused) as e:
        runner.run_source_row(_job(p, tmp_path, "RADD"))
    assert e.value.state == "no_data" and no_ee == []


def test_plan_rows_carry_the_coverage_state_and_skip_blocked_rows(tmp_path, cover):
    p = _project(tmp_path)
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "GLAD-S2")
    cover(p, "GLAD-L")
    p.coverage = {**p.coverage, "GLAD-S2": SourceCoverage(aoi_digest="g", checked_on=date(2026, 10, 1),
                                                          fraction=0.0)}
    rows = {r.source: r for r in plan_prepare(p, tmp_path, JAN)}
    assert rows["GLAD-L"].coverage == "covered" and rows["GLAD-L"].needs_run
    assert rows["GLAD-S2"].coverage == "not_covered" and rows["GLAD-S2"].blocked and not rows["GLAD-S2"].needs_run
    assert rows["GLAD-S2"].notes[0].key == "coverage.not_covered_glad_s2"


def test_a_geotiff_source_runs_locally_and_is_clipped_to_the_aoi_polygon(tmp_path, cover, no_ee):
    p = _project(tmp_path)
    _file_source(p, tmp_path)
    cover(p, "tif")
    arts = runner.run_source_row(_job(p, tmp_path, "tif"))
    assert no_ee == [] and set(arts) == {fp.AOI_KEY, fp.raw_key(JAN.id, "tif"), fp.harm_key(JAN.id, "tif")}
    runner.apply_artifacts(p, arts)
    harm = arts[fp.harm_key(JAN.id, "tif")]
    with rasterio.open(tmp_path / harm.rel_path) as r:
        a = r.read(1)
    assert a[:, :10].min() == 1 and a[:, 20:].max() == 0  # the AOI polygon is the western half
    assert harm.stats["alert_pixels"] == int((a > 0).sum()) and harm.stats["date_min"] == 2026010
    row = plan_prepare(p, tmp_path, JAN)[0]
    assert row.ready and row.stats == harm.stats
    assert runner.run_source_row(_job(p, tmp_path, "tif")) == {}  # current: nothing to do


def test_a_geotiff_row_waits_for_the_aoi_geometry(tmp_path, cover, no_ee):
    p = _project(tmp_path)
    _file_source(p, tmp_path)
    cover(p, "tif")
    (tmp_path / paths.AOI_SIDECAR).unlink()
    with pytest.raises(runner.AoiGeometryMissing):
        runner.run_source_row(_job(p, tmp_path, "tif"))
    assert not list(tmp_path.glob("periods/**/*.tif"))


def test_a_moved_user_file_reads_missing_not_stale(tmp_path, cover, no_ee):
    p = _project(tmp_path)
    src = _file_source(p, tmp_path)
    cover(p, "tif")
    runner.apply_artifacts(p, runner.run_source_row(_job(p, tmp_path, "tif")))
    Path(src.ref).rename(tmp_path / "elsewhere.tif")
    row = plan_prepare(p, tmp_path, JAN)[0]
    assert row.raw is fp.Status.MISSING and row.file_missing == src.ref and not row.ready and not row.needs_run
    with pytest.raises(FileNotFoundError, match="file not found"):
        runner.run_source_row(_job(p, tmp_path, "tif"))


def test_a_custom_earth_engine_source_downloads_at_its_native_grid(tmp_path, cover, monkeypatch):
    p = _project(tmp_path)
    add_custom_source(p, name="ee radd", origin="gee", ref="users/x/radd", asset_type="IMAGE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                      native=NativeGrid(crs="EPSG:4326", scale=10.0))
    cover(p, "ee radd")
    calls = []

    def fake_export(image, key_or_native, aoi, out_path, **kw):
        calls.append((image, key_or_native))
        raise RuntimeError("stop here")

    monkeypatch.setattr(runner, "export_native", fake_export)
    monkeypatch.setattr(runner.catalogue, "build_custom_image", lambda src, a, b, aoi: f"IMG:{src.name}:{a}:{b}")
    with pytest.raises(RuntimeError, match="stop here"):
        runner.run_source_row(_job(p, tmp_path, "ee radd"))
    assert calls == [("IMG:ee radd:2025-10-03:2026-01-31", ("EPSG:4326", 10.0))]


def test_a_superseded_raw_file_is_deleted(tmp_path, cover, no_ee):
    p = _project(tmp_path)
    _file_source(p, tmp_path)
    cover(p, "tif")
    runner.apply_artifacts(p, runner.run_source_row(_job(p, tmp_path, "tif")))
    old = tmp_path / p.artifacts[fp.raw_key(JAN.id, "tif")].rel_path
    p.look_back_days = 10  # a new window, a new file name
    runner.apply_artifacts(p, runner.run_source_row(_job(p, tmp_path, "tif")))
    new = tmp_path / p.artifacts[fp.raw_key(JAN.id, "tif")].rel_path
    assert new != old and new.exists() and not old.exists()


def test_an_aoi_change_rebuilds_the_aoi_raster_before_a_geotiff_row(tmp_path, cover, no_ee):
    """Spec §13: after an AOI change the AOI raster is rebuilt before any
    GeoTIFF row harmonizes (the sidecar itself is fetched again by the
    loader, Task 16)."""
    from gui.scripts import aoi_io

    p = _project(tmp_path)
    _file_source(p, tmp_path)
    cover(p, "tif")
    runner.apply_artifacts(p, runner.run_source_row(_job(p, tmp_path, "tif")))
    old_aoi = p.artifacts[fp.AOI_KEY].content_id
    # a new AOI, the eastern half, its sidecar in place as the loader stages it
    aoi_io.write_sidecar(tmp_path, gpd.GeoDataFrame(geometry=[box((_W + _E) / 2, _S, _E, _N)], crs="EPSG:4326"))
    p.aoi = p.aoi.model_copy(update={"digest": "g2"})
    cover(p, "tif")
    assert plan_prepare(p, tmp_path, JAN)[0].harmonized is fp.Status.STALE  # its AOI raster is the old AOI's
    arts = runner.run_source_row(_job(p, tmp_path, "tif"))
    assert arts[fp.AOI_KEY].content_id != old_aoi
    runner.apply_artifacts(p, arts)
    with rasterio.open(tmp_path / arts[fp.harm_key(JAN.id, "tif")].rel_path) as r:
        a = r.read(1)
    assert a[:, :10].max() == 0 and a[:, 20:].min() == 1  # clipped to the new, eastern half


def test_a_geotiff_row_asks_twice_the_compressed_estimate_before_each_write(tmp_path, cover, no_ee, monkeypatch):
    """Spec §13 "free-space gates ask for exactly twice the compressed
    estimate" (user decision 2026-10-01): the AOI raster, the normalized raw,
    then the harmonized file (checked before the raw is written and again
    before the harmonize)."""
    from deforestation_alerts.engine import grid as gridmod
    from deforestation_alerts.engine import normalize
    from deforestation_alerts.engine.raster_io import compressed_estimate

    p = _project(tmp_path)
    src = _file_source(p, tmp_path)
    cover(p, "tif")
    seen = []
    monkeypatch.setattr(runner, "ensure_free_space", lambda folder, needed: seen.append(needed))
    runner.run_source_row(_job(p, tmp_path, "tif"))
    aoi_raster = 2 * compressed_estimate(gridmod.estimate_bytes(GRID, bands=1))
    raw = 2 * compressed_estimate(normalize.estimate_bytes(src, BOUNDS))
    harm = runner._harmonize_space(p)
    assert seen == [aoi_raster, raw, harm, harm]


def test_a_source_not_covered_with_current_files_does_not_unlock_integrate(tmp_path, cover, no_ee, monkeypatch):
    """Spec §13 "registry: gates … including coverage": files built before a
    re-check found the source outside the AOI do not count as ready."""
    from gui.store import workflow_steps as ws

    p = _project(tmp_path)
    _file_source(p, tmp_path)
    cover(p, "tif")
    runner.apply_artifacts(p, runner.run_source_row(_job(p, tmp_path, "tif")))
    monkeypatch.setattr(ws.paths, "project_dir", lambda name, root=None: tmp_path)
    integrate_step = ws.STEPS[ws.step_index("integrate")]
    assert integrate_step.unlocked(p)
    p.coverage = {"tif": SourceCoverage(aoi_digest="g", checked_on=date(2026, 10, 1), fraction=0.0)}
    assert plan_prepare(p, tmp_path, JAN)[0].raw is fp.Status.CURRENT
    assert not integrate_step.unlocked(p)
