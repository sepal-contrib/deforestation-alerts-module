"""Per-period masks (spec §13 "per-period masks")."""

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine import runner
from deforestation_alerts.engine.plan import plan_masks, plan_prepare
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, GridSpec, NativeGrid, Period, Project, add_mask, add_predefined_source,
    set_period_mask,
)

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=20)
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _native_raw(path):
    from pyproj import Transformer
    w, s, e, n = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True).transform_bounds(
        GRID.xmin, GRID.ymax - 200, GRID.xmin + 300, GRID.ymax)
    res = 0.00005
    width, height = int((e - w) / res) + 2, int((n - s) / res) + 2
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=2, dtype="int32", nodata=0,
                       crs="EPSG:4326", transform=Affine(res, 0, w - res, 0, -res, n + res)) as dst:
        dst.write(np.ones((height, width), "int32"), 1)
        dst.write(np.full((height, width), 2026010, "int32"), 2)
    return Path(path)


@pytest.fixture
def two_periods(tmp_path, monkeypatch, cover):
    p = Project(name="pm", created_at=datetime.now(), updated_at=datetime.now())
    box_ = (-57.01, -9.97, -56.99, -9.93)  # around the grid (UTM 21S, x 500000, northing 8900000)
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=box_)
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    cover(p, "GLAD-L")
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    # an on-grid GeoTIFF mask: the western half is "forest"
    mpath = tmp_path / "user" / "forest.tif"
    mpath.parent.mkdir()
    half = np.zeros((GRID.height, GRID.width), "uint8")
    half[:, :15] = 1
    with rasterio.open(mpath, "w", driver="GTiff", width=GRID.width, height=GRID.height, count=1, dtype="uint8",
                       crs=GRID.epsg, transform=GRID.affine) as dst:
        dst.write(half, 1)
    add_mask(p, name="forest", origin="file", ref=str(mpath), asset_type="FILE", band=1,
             native=NativeGrid(crs=GRID.epsg, scale=10.0, transform=tuple(GRID.affine)[:6]), mode="keep",
             period_id=JAN.id)
    set_period_mask(p, FEB.id, "forest", on=True, mode="keep")
    # the AOI sidecar the AOI raster is built from: the whole grid
    import geopandas as gpd
    from shapely.geometry import box
    from gui.scripts import aoi_io
    aoi_io.write_sidecar(tmp_path, gpd.GeoDataFrame(geometry=[box(*box_)], crs="EPSG:4326"))
    p.aoi = p.aoi.model_copy(update={"geometry_file": "aoi.geojson"})
    downloads = []

    def fake_export(image, key_or_native, aoi, out_path, **kw):
        downloads.append(Path(out_path).name)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return _native_raw(out_path)

    monkeypatch.setattr(runner, "export_native", fake_export)
    monkeypatch.setattr(runner.catalogue, "build_image", lambda *a, **k: "IMAGE")
    return p, tmp_path, downloads


def _run_all(p, pdir, period):
    for row in plan_masks(p, pdir, period):
        if row.needs_run:
            runner.apply_artifacts(p, runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, row.name,
                                                                          "AOI")))
    for row in plan_prepare(p, pdir, period):
        if row.needs_run:
            runner.apply_artifacts(p, runner.run_source_row(runner.SourceJob(p.model_copy(deep=True), pdir, period,
                                                                             row.source, {"bands": []}, "AOI")))


def _set(p, pdir, period):
    sid = f"{period.id}__glad-l"
    inputs = fp.set_inputs(p, period, ["GLAD-L"], AlertSetParams())
    (pdir / f"{sid}.parquet").write_bytes(b"x")
    p.alert_sets = {**p.alert_sets, sid: AlertSet(
        id=sid, period_id=period.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"{sid}.parquet", combined_rel_path="c", footprint_rel_path="f",
        fingerprint=fp.fingerprint(inputs), created_at=datetime.now())}
    return sid


def test_a_switch_in_one_period_touches_only_that_period(two_periods):
    p, pdir, downloads = two_periods
    _run_all(p, pdir, JAN)
    _run_all(p, pdir, FEB)
    jan_set, feb_set = _set(p, pdir, JAN), _set(p, pdir, FEB)
    jan_file = pdir / p.artifacts[fp.harm_key(JAN.id, "GLAD-L")].rel_path
    jan_bytes, n_downloads = jan_file.read_bytes(), len(downloads)

    set_period_mask(p, FEB.id, "forest", mode="exclude")  # only edits the project: no job starts

    feb_row, jan_row = plan_prepare(p, pdir, FEB)[0], plan_prepare(p, pdir, JAN)[0]
    assert feb_row.raw is fp.Status.CURRENT and feb_row.harmonized is fp.Status.STALE and feb_row.needs_run
    assert jan_row.ready
    assert fp.set_status(p, pdir, p.alert_sets[feb_set]) is fp.Status.STALE
    assert fp.set_status(p, pdir, p.alert_sets[jan_set]) is fp.Status.CURRENT

    _run_all(p, pdir, FEB)
    assert len(downloads) == n_downloads  # re-harmonized with no source download
    assert plan_prepare(p, pdir, FEB)[0].ready and jan_file.read_bytes() == jan_bytes
    with rasterio.open(pdir / p.artifacts[fp.harm_key(FEB.id, "GLAD-L")].rel_path) as r:
        a = r.read(1)
    assert a[:, :15].max() == 0 and a[:, 15:].min() == 1  # exclude: the forest half is removed


def test_a_mask_on_an_old_grid_blocks_harmonize_before_any_download(two_periods):
    p, pdir, downloads = two_periods
    runner.apply_artifacts(p, runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, "forest", "AOI")))
    p.grid = GRID.model_copy(update={"resolution": 20, "width": 15, "height": 10})
    assert not fp.mask_current(p, "forest")
    with pytest.raises(runner.MaskNotReady):
        runner.run_source_row(runner.SourceJob(p.model_copy(deep=True), pdir, FEB, "GLAD-L", {"bands": []}, "AOI"))
    assert downloads == []


def test_mask_rows_and_their_on_pixels(two_periods):
    p, pdir, _ = two_periods
    row = plan_masks(p, pdir, FEB)[0]
    assert row.on and row.raw is None and row.aligned is fp.Status.MISSING and row.needs_run
    runner.apply_artifacts(p, runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, "forest", "AOI")))
    row = plan_masks(p, pdir, FEB)[0]
    assert row.ready and row.stats == {"on_pixels": 15 * GRID.height} and not row.covers_none
    set_period_mask(p, FEB.id, "forest", on=False)
    assert not plan_masks(p, pdir, FEB)[0].on


def test_a_grid_change_leaves_a_raw_mask_current_and_an_aoi_change_does_not(tmp_path):
    p = Project(name="g", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g1", bounds=(0, 0, 1, 1))
    p.grid = GRID
    p.periods = {JAN.id: JAN}
    add_mask(p, name="hansen", origin="gee", ref="UMD/x", asset_type="IMAGE", band="treecover2000",
             native=NativeGrid(crs="EPSG:4326", scale=27.83), mode="keep", period_id=JAN.id)
    f = tmp_path / "raw.tif"
    f.write_bytes(b"x")
    key = fp.mask_raw_key("hansen")
    p.artifacts = {key: fp.make_artifact(key, "raw_mask", tmp_path, f, fp.mask_raw_inputs(p, "hansen"))}
    p.grid = GRID.model_copy(update={"resolution": 30})
    assert fp.status(p, tmp_path, key, fp.mask_raw_inputs(p, "hansen")) is fp.Status.CURRENT
    p.aoi = p.aoi.model_copy(update={"digest": "g2"})
    assert fp.status(p, tmp_path, key, fp.mask_raw_inputs(p, "hansen")) is fp.Status.STALE


def _native_mask(path):
    """An Earth Engine mask download: 1-band uint8 0/1 in EPSG:4326 over the grid."""
    from pyproj import Transformer
    w, s, e, n = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True).transform_bounds(
        GRID.xmin, GRID.ymax - 200, GRID.xmin + 300, GRID.ymax)
    res = 0.00005
    width, height = int((e - w) / res) + 2, int((n - s) / res) + 2
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=width, height=height, count=1, dtype="uint8", nodata=0,
                       crs="EPSG:4326", transform=Affine(res, 0, w - res, 0, -res, n + res)) as dst:
        dst.write(np.ones((height, width), "uint8"), 1)
    return Path(path)


def test_mask_rows_ask_twice_the_compressed_estimate_before_each_write(two_periods, monkeypatch):
    """Spec §13 "free-space gates ask for exactly twice the compressed
    estimate" (user decision 2026-10-01), for mask downloads and alignment."""
    from deforestation_alerts.engine.grid import estimate_bytes
    from deforestation_alerts.engine.masks import mask_raw_estimate
    from deforestation_alerts.engine.raster_io import compressed_estimate

    p, pdir, _ = two_periods
    seen = []
    monkeypatch.setattr(runner, "ensure_free_space", lambda folder, needed: seen.append(needed))
    one_band = 2 * compressed_estimate(estimate_bytes(GRID, bands=1))
    runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, "forest", "AOI"))  # a GeoTIFF mask
    assert seen == [one_band, one_band]  # the AOI raster, then the alignment

    add_mask(p, name="hansen", origin="gee", ref="UMD/x", asset_type="IMAGE", band="treecover2000",
             native=NativeGrid(crs="EPSG:4326", scale=27.83), mode="keep", period_id=FEB.id)
    monkeypatch.setattr(runner, "build_mask_image", lambda mask, aoi: "IMAGE")
    monkeypatch.setattr(runner, "export_mask_native",
                        lambda image, native, aoi, out_path, **kw: _native_mask(Path(out_path)))
    seen.clear()
    runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, "hansen", "AOI"))
    raw = 2 * compressed_estimate(mask_raw_estimate(p.masks["hansen"], p.aoi.bounds))
    assert seen == [raw, one_band, one_band]  # the download and the alignment before it, then the alignment


def test_a_mask_switch_turns_the_prepare_badge_down(two_periods, monkeypatch):
    """Spec §13 "registry: gates, badges … including stale masks"."""
    from gui.store import workflow_steps as ws

    p, pdir, _ = two_periods
    monkeypatch.setattr(ws.paths, "project_dir", lambda name, root=None: pdir)
    p.active_period = FEB.id
    _run_all(p, pdir, FEB)
    prepare = ws.STEPS[ws.step_index("prepare")]
    assert prepare.count(p) == 1
    set_period_mask(p, FEB.id, "forest", mode="exclude")
    assert prepare.count(p) == 0


def test_a_deleted_aligned_mask_blocks_harmonize_before_any_download(two_periods):
    """The fingerprint alone says current; the file is gone from disk. The mask
    row offers a re-run and the source row is refused before the download."""
    p, pdir, downloads = two_periods
    runner.apply_artifacts(p, runner.run_mask_row(runner.MaskJob(p.model_copy(deep=True), pdir, "forest", "AOI")))
    (pdir / p.artifacts[fp.mask_key("forest")].rel_path).unlink()
    assert fp.mask_current(p, "forest")  # fingerprint only, no disk access
    row = plan_masks(p, pdir, FEB)[0]
    assert row.aligned is fp.Status.MISSING and row.needs_run
    with pytest.raises(runner.MaskNotReady):
        runner.run_source_row(runner.SourceJob(p.model_copy(deep=True), pdir, FEB, "GLAD-L", {"bands": []}, "AOI"))
    assert downloads == []
