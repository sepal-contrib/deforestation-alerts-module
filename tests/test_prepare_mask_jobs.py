"""Mask rows in the Download & harmonize jobs (spec §7.3): Run all aligns the
period's masks first, a source Run runs its stale masks first, mask keys share
the period's Stop scope, a keep mask covering none of the AOI warns, and a mask
switched while its period's Run all runs leaves the row in flight out of date
(review focus 4).

The period's mask choices live in the project (``p.periods``, ruling C1): the
tests read and switch them there, never on the module ``Period`` constant.
"""

import threading
from datetime import date, datetime
from types import SimpleNamespace

import solara

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.engine.runner import MaskNotReady
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AoiRecord, GridSpec, NativeGrid, Period, Project, add_mask, add_predefined_source, set_period_mask,
)
from gui.i18n import t
from gui.scripts import prepare_jobs, raster_map, source_layers
from gui.store.state_manager import AppState
from gui.tile import prepare_step

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=30, height=20)
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _project(tmp_path, cover):
    p = Project(name="mj", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=(0, 0, 1, 1))
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    cover(p, "GLAD-L")
    p.periods = {FEB.id: FEB}
    p.active_period = FEB.id
    add_mask(p, name="forest", origin="gee", ref="UMD/x", asset_type="IMAGE", band="b",
             native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=FEB.id)
    return p


def _art(pdir, key, kind, inputs, stats=None):
    f = pdir / f"{key.replace(':', '_')}.tif"
    f.write_bytes(b"x")
    return fp.make_artifact(key, kind, pdir, f, inputs, stats=stats)


def test_run_all_runs_the_periods_masks_first(tmp_path, cover):
    p = _project(tmp_path, cover)
    assert prepare_step.run_all_items(p, tmp_path, p.periods[FEB.id]) == [("mask", "forest"), ("source", "GLAD-L")]
    set_period_mask(p, FEB.id, "forest", on=False)
    # an off mask is not run
    assert prepare_step.run_all_items(p, tmp_path, p.periods[FEB.id]) == [("source", "GLAD-L")]
    assert prepare_step.items_for_source(p, tmp_path, p.periods[FEB.id], "GLAD-L") == [("source", "GLAD-L")]
    set_period_mask(p, FEB.id, "forest", on=True)
    assert prepare_step.items_for_source(p, tmp_path, p.periods[FEB.id], "GLAD-L") == [
        ("mask", "forest"), ("source", "GLAD-L")]


def test_a_ready_mask_is_not_run_again(tmp_path, cover):
    p = _project(tmp_path, cover)
    raw = _art(tmp_path, fp.mask_raw_key("forest"), "raw_mask", fp.mask_raw_inputs(p, "forest"))
    p.artifacts = {raw.key: raw}
    aligned = _art(tmp_path, fp.mask_key("forest"), "mask", fp.mask_inputs(p, "forest"), stats={"on_pixels": 5})
    p.artifacts = {**p.artifacts, aligned.key: aligned}
    period = p.periods[FEB.id]
    assert prepare_step.run_all_items(p, tmp_path, period) == [("source", "GLAD-L")]
    assert prepare_step.items_for_source(p, tmp_path, period, "GLAD-L") == [("source", "GLAD-L")]


def test_mask_keys_share_the_stop_scope_of_the_period_that_started_them():
    stops = prepare_step._StopEvents()
    feb, mar = ("mj", "2026-02-01_2026-02-28"), ("mj", "2026-03-01_2026-03-31")
    event = stops.add(feb, {"prep:mj:2026-02-01_2026-02-28:GLAD-L", "prep:mj:@mask:forest"})
    stops.add(mar, {"prep:mj:2026-03-01_2026-03-31:RADD"})
    running = {"prep:mj:2026-02-01_2026-02-28:GLAD-L", "prep:mj:@mask:forest", "prep:other:@mask:x",
               "prep:mj:2026-03-01_2026-03-31:RADD"}
    assert prepare_step.mask_inflight_key("mj", "forest") == "prep:mj:@mask:forest"
    assert prepare_step.mine_keys(running, stops, *feb) == {
        "prep:mj:2026-02-01_2026-02-28:GLAD-L", "prep:mj:@mask:forest"}
    # March's Run all and Stop do not see February's mask job, which March's Stop could not stop
    assert prepare_step.mine_keys(running, stops, *mar) == {"prep:mj:2026-03-01_2026-03-31:RADD"}
    stops.discard(feb, event)
    assert prepare_step.mine_keys(running, stops, *feb) == set()


def _ctx(state, tmp_path, warnings, **overrides):
    fields = dict(
        app_state=state, layers=SimpleNamespace(is_on=lambda k: False), map_=None,
        notifier=None, name="mj", pdir=tmp_path, period=FEB, meta={}, aoi="AOI", cancel=threading.Event(),
        row_errors=None, errors_lock=threading.Lock(), on_warning=warnings.append)
    fields.update(overrides)
    return prepare_jobs.PrepareContext(**fields)


def _aligning(tmp_path, order, on_pixels):
    def mask_row(job, **kw):
        order.append(("mask", job.name))
        live = job.project
        raw = _art(tmp_path, fp.mask_raw_key(job.name), "raw_mask", fp.mask_raw_inputs(live, job.name))
        live.artifacts[raw.key] = raw
        aligned = _art(tmp_path, fp.mask_key(job.name), "mask", fp.mask_inputs(live, job.name),
                       stats={"on_pixels": on_pixels})
        return {raw.key: raw, aligned.key: aligned}

    return mask_row


def test_a_keep_mask_covering_none_of_the_aoi_warns(tmp_path, cover, monkeypatch):
    state = AppState()
    p = _project(tmp_path, cover)
    state.load_project_state(p)
    order, warnings = [], []

    monkeypatch.setattr(prepare_jobs, "run_mask_row", _aligning(tmp_path, order, on_pixels=0))
    monkeypatch.setattr(prepare_jobs, "run_source_row", lambda job, **kw: order.append(("source", job.source)) or {})
    prepare_jobs.run_rows(_ctx(state, tmp_path, warnings), [("mask", "forest"), ("source", "GLAD-L")])
    assert order == [("mask", "forest"), ("source", "GLAD-L")]
    assert warnings and "covers none of the AOI" in warnings[0]
    assert warnings == [t("steps.prepare.mask_covers_none", mask="forest")]
    # the aligned mask was recorded before the source row started
    assert fp.mask_key("forest") in state.project.value.artifacts


def test_an_exclude_mask_covering_none_does_not_warn(tmp_path, cover, monkeypatch):
    state = AppState()
    p = _project(tmp_path, cover)
    set_period_mask(p, FEB.id, "forest", mode="exclude")
    state.load_project_state(p)
    order, warnings = [], []
    monkeypatch.setattr(prepare_jobs, "run_mask_row", _aligning(tmp_path, order, on_pixels=0))
    prepare_jobs.run_rows(_ctx(state, tmp_path, warnings), [("mask", "forest")])
    assert order == [("mask", "forest")] and warnings == []


class _Notifier:
    def __init__(self):
        self.errors = []

    def error(self, message, *, timeout=None):
        self.errors.append(message)

    def track(self, title, total_steps=None):
        from pysepal.solara.notifications import NoopNotifier

        return NoopNotifier().track(title, total_steps=total_steps)


def test_a_failed_mask_row_is_keyed_by_mask_and_reads_translated(tmp_path, cover, monkeypatch):
    """A mask row's error is ``@mask:<name>`` in the row errors (the masks
    card reads it), the message translated from the engine's key (the real
    runner: a GeoTIFF mask gone from disk, an Earth Engine mask without its
    native grid); the next row still runs (spec §12) and reads its own keyed
    refusal. The refusals already name the mask, so the toast does not
    repeat it."""
    from deforestation_alerts.project.models import add_mask

    state = AppState()
    p = _project(tmp_path, cover)
    p.masks = {**p.masks, "forest": p.masks["forest"].model_copy(update={"native": None})}
    gone = str(tmp_path / "gone.tif")
    add_mask(p, name="water", origin="file", ref=gone, asset_type="FILE", band=1, native=None, mode="exclude",
             period_id=FEB.id)
    state.load_project_state(p)

    def source_row(job, **kw):
        raise MaskNotReady("raw engine text")

    monkeypatch.setattr(prepare_jobs, "run_source_row", source_row)
    errors, notifier = solara.reactive({}), _Notifier()
    prepare_jobs.run_rows(_ctx(state, tmp_path, [], row_errors=errors, notifier=notifier),
                          [("mask", "forest"), ("mask", "water"), ("source", "GLAD-L")])
    native = t("steps.prepare.mask_native_unknown", name="forest")
    missing = t("steps.prepare.mask_file_missing", name="water", path=gone)
    assert errors.value == {"@mask:forest": native, "@mask:water": missing,
                            "GLAD-L": t("steps.prepare.mask_not_ready")}
    assert notifier.errors == [native, missing, t("steps.prepare.row_failed", source="GLAD-L",
                                                  error=t("steps.prepare.mask_not_ready"))]


def test_a_mask_row_failure_toast_says_it_is_a_mask(tmp_path, cover, monkeypatch):
    """"Mask forest: ...", not "forest: ..." (a custom source could be called
    "forest" too); the row error under the mask's own row is the message."""
    state = AppState()
    state.load_project_state(_project(tmp_path, cover))

    def mask_row(job, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(prepare_jobs, "run_mask_row", mask_row)
    errors, notifier = solara.reactive({}), _Notifier()
    prepare_jobs.run_rows(_ctx(state, tmp_path, [], row_errors=errors, notifier=notifier), [("mask", "forest")])
    assert errors.value == {"@mask:forest": "disk on fire"}
    assert notifier.errors == [t("steps.prepare.mask_row_failed", mask="forest", error="disk on fire")]
    assert notifier.errors == ["Mask forest: disk on fire"]


def test_a_claim_records_its_stop_scope_before_it_is_published_and_a_refused_one_never():
    """``InflightKeys.claim(on_claim=)``: the step registers a job's Stop
    scope inside a successful claim, before the claimed keys are published,
    so no render ever lists another job's keys as this period's."""
    from gui.scripts.inflight import InflightKeys

    inflight, seen = InflightKeys(), []
    assert inflight.claim("a", "b", on_claim=lambda: seen.append(set(inflight.value)))
    assert seen == [set()] and inflight.value == {"a", "b"}
    assert not inflight.claim("b", "c", on_claim=lambda: seen.append("refused"))
    assert seen == [set()] and inflight.value == {"a", "b"}


def test_a_mask_removed_meanwhile_is_skipped(tmp_path, cover, monkeypatch):
    state = AppState()
    p = _project(tmp_path, cover)
    p.masks = {}
    state.load_project_state(p)
    def no_run(job, **kw):
        raise AssertionError("a mask removed from the project must not run")

    monkeypatch.setattr(prepare_jobs, "run_mask_row", no_run)
    errors = solara.reactive({})
    prepare_jobs.run_rows(_ctx(state, tmp_path, [], row_errors=errors), [("mask", "forest")])
    assert errors.value == {}


def test_a_shown_mask_is_redrawn_from_its_new_aligned_file(tmp_path, cover, monkeypatch):
    state = AppState()
    state.load_project_state(_project(tmp_path, cover))
    refreshed, drawn = [], []

    def refresh(key, build, content_id, legend=None):
        refreshed.append((key, content_id, legend))
        build()

    monkeypatch.setattr(prepare_jobs, "run_mask_row", _aligning(tmp_path, [], on_pixels=4))
    monkeypatch.setattr(raster_map, "add_raster_layer",
                        lambda map_, path, **kw: drawn.append((map_, path, kw)))
    layers = SimpleNamespace(is_on=lambda k: k == "mask:forest", refresh=refresh)
    prepare_jobs.run_rows(_ctx(state, tmp_path, [], layers=layers, map_="MAP"), [("mask", "forest")])
    aligned = state.project.value.artifacts[fp.mask_key("forest")]
    assert refreshed == [("mask:forest", aligned.content_id, source_layers.mask_layer_legend(state.project.value,
                                                                                              "forest"))]
    [(map_, path, kw)] = drawn
    assert map_ == "MAP" and path == paths.resolve(tmp_path, aligned.rel_path)
    assert kw["key"] == "mask:forest" and kw["name"] == "Mask forest" and kw["colormap"] is raster_map.MASK_COLORMAP
    assert (kw["vmin"], kw["vmax"], kw["nodata"], kw["opacity"]) == (1, 1, 0, raster_map.MASK_OPACITY)


def test_a_rerun_of_a_shown_mask_keeps_its_legend(tmp_path, cover, monkeypatch):
    """Spec §9.4: ``refresh`` replaces the layer and its legend, so the
    redraw of a mask on the map passes the mask's legend (the active period's
    choice), and the new file's layer lands with it."""
    import gui.scripts.map_layers as ml
    from gui.scripts.map_layers import MapLayers

    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args))
    state = AppState()
    state.load_project_state(_project(tmp_path, cover))
    drawn = []
    monkeypatch.setattr(raster_map, "add_raster_layer", lambda map_, path, **kw: drawn.append(path))
    monkeypatch.setattr(prepare_jobs, "run_mask_row", _aligning(tmp_path, [], on_pixels=4))

    class _Map:
        def remove_layer(self, key, none_ok=False):
            pass

    layers = MapLayers(_Map())
    p = state.project.value
    layers.show("mask:forest", lambda: None, content_id="old", legend=source_layers.mask_layer_legend(p, "forest"))
    set_period_mask(p, FEB.id, "forest", mode="exclude")  # the layer's legend now lags the choice
    prepare_jobs.run_rows(_ctx(state, tmp_path, [], layers=layers, map_="MAP"), [("mask", "forest")])
    aligned = state.project.value.artifacts[fp.mask_key("forest")]
    assert drawn == [paths.resolve(tmp_path, aligned.rel_path)]
    assert layers.content_of("mask:forest") == aligned.content_id
    (legend,) = layers.legends.value
    assert legend.layer_id == "mask:forest" and legend.label.key == "legend.name.mask"
    assert legend.spec.labels[0].key == "legend.mask.exclude"


def test_the_mask_palette_comes_from_the_palette_table():
    from gui.scripts import legend_specs

    assert raster_map.MASK_COLOR == legend_specs.MASK_COLOR
    assert raster_map.MASK_OPACITY == legend_specs.MASK_OPACITY
    assert set(raster_map.MASK_COLORMAP.colors) == {legend_specs.MASK_COLOR}
    assert raster_map.MASK_EE_VIS == legend_specs.mask_ee_vis()


def test_localtileserver_accepts_the_mask_colormap(tmp_path):
    """The slice-4 walk: showing an aligned mask failed with matplotlib's "data
    mapping points must start with x=0": localtileserver turns a
    ListedColormap into a 256-step LinearSegmentedColormap, which needs two
    colours or more. The real tile URL is built, in the mask grey only."""
    import numpy as np
    import rasterio
    from affine import Affine
    from localtileserver import TileClient
    from matplotlib.colors import LinearSegmentedColormap, to_rgba

    from gui.scripts import legend_specs

    path = tmp_path / "mask.tif"
    with rasterio.open(path, "w", driver="GTiff", width=8, height=8, count=1, dtype="int32", crs="EPSG:32721",
                       transform=Affine(10, 0, 500000, 0, -10, 8900000), nodata=0) as dst:
        dst.write(np.ones((1, 8, 8), "int32"))
    client = TileClient(str(path))
    try:
        assert "{z}" in client.get_tile_url(indexes=[1], colormap=raster_map.MASK_COLORMAP, vmin=1, vmax=1, nodata=0)
    finally:
        client.shutdown(force=True)
    steps = LinearSegmentedColormap.from_list("", raster_map.MASK_COLORMAP.colors, N=256)(range(256))
    assert {tuple(c) for c in steps} == {to_rgba(legend_specs.MASK_COLOR)}


def test_a_switch_during_run_all_leaves_the_row_in_flight_out_of_date(tmp_path, cover, monkeypatch):
    state = AppState()
    p = _project(tmp_path, cover)
    raw = _art(tmp_path, fp.mask_raw_key("forest"), "raw_mask", fp.mask_raw_inputs(p, "forest"))
    p.artifacts = {raw.key: raw}
    aligned = _art(tmp_path, fp.mask_key("forest"), "mask", fp.mask_inputs(p, "forest"), stats={"on_pixels": 5})
    p.artifacts = {**p.artifacts, aligned.key: aligned}
    state.load_project_state(p)

    def source_row(job, **kw):
        live = job.project  # the deep copy the row started with: the mask is "keep"
        period = live.periods[FEB.id]
        rkey, hkey = fp.raw_key(FEB.id, "GLAD-L"), fp.harm_key(FEB.id, "GLAD-L")
        r = _art(tmp_path, rkey, "raw_alert", fp.raw_alert_inputs(live, "GLAD-L", period))
        live.artifacts[rkey] = r
        h = _art(tmp_path, hkey, "harmonized", fp.harmonized_inputs(live, "GLAD-L", period),
                 stats={"alert_pixels": 3, "alert_pixels_unmasked": 3})
        assert h.inputs["masks"][0][:2] == ["forest", "keep"]
        # the user switches the mask to exclude while this row runs
        state.update_project("mj", lambda q: set_period_mask(q, FEB.id, "forest", mode="exclude"))
        return {rkey: r, hkey: h}

    monkeypatch.setattr(prepare_jobs, "run_source_row", source_row)
    prepare_jobs.run_rows(_ctx(state, tmp_path, []), [("source", "GLAD-L")])
    live = state.project.value
    row = plan_prepare(live, tmp_path, live.periods[FEB.id])[0]
    assert row.raw is fp.Status.CURRENT and row.harmonized is fp.Status.STALE and row.needs_run
    # the next Run all re-harmonizes it without a download (the raw file is current)
    assert prepare_step.run_all_items(live, tmp_path, live.periods[FEB.id]) == [("source", "GLAD-L")]
