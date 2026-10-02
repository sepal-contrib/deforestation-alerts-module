"""Pure-function tests for the catalogue-meta helpers, plus render-based
coverage of ``AlertsStep`` and ``SourceModal`` (fix round 1, review finding
"zero committed automated test coverage" for ``_toggle``'s is_on/inflight/
hide/show branching, ruling #46's ``plan_prepare`` readiness gate, the
``ee_aoi`` ``ValueError`` catch, and ``SourceModal``'s ESC handler /
double-submit guard).

The render tests below use ``reacton.render`` against the real components,
mirroring ``tests/test_workflow_header.py``'s pattern: presentation children
that are not under test (``ProductTable``, ``ConfirmDialog``, the custom
``SourceModal`` child of ``AlertsStep``) are swapped for recorders so the
test can call the exact callables ``AlertsStep`` wires up, while the module
under test — ``_toggle``, ``_delete``, real ``MapLayers``, a real
``Project`` — runs unmocked. ``SourceModal`` itself is rendered directly
(no recorder) and driven through real ``ipyvuetify`` widgets via
``fire_event``, the established pattern in ``tests/test_overview_export.py``
/ ``tests/test_feedback_notifications.py``.
"""

import asyncio
import threading
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.fingerprint import Status, harm_key, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AoiRecord, Artifact, Period, Project, add_predefined_source,
)
from gui.i18n import t
from gui.scripts import aoi_io as aoi_io_mod
from gui.scripts import catalogue_meta
from gui.scripts import raster_map as raster_map_mod
from gui.scripts.map_layers import MapLayers
from gui.store.state_manager import AppState
from gui.tile import alerts_step
from gui.widget.source_modal import SourceModal, available_predefined


def _p():
    return Project(name="d", created_at=datetime.now(), updated_at=datetime.now())


def test_available_predefined_hides_the_ones_already_added():
    p = _p()
    assert available_predefined(p) == ["GLAD-L", "RADD", "GLAD-S2"]
    add_predefined_source(p, "RADD")
    assert available_predefined(p) == ["GLAD-L", "GLAD-S2"]


def test_coverage_text():
    assert catalogue_meta.coverage_text("RADD", {"end": "2026-09-20"}) == "2019-01-01 → 2026-09-20"
    assert catalogue_meta.coverage_text("RADD", None) == "…"


def test_preview_window_prefers_the_active_period():
    p = _p()
    per = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods["x"] = per
    p.active_period = "x"
    assert catalogue_meta.preview_window(p, {"end": "2026-09-20"}, "RADD") == per.search_window(p.look_back_days)


def test_preview_window_without_period_is_the_whole_catalogue():
    # Ruling #15: spec §6.2 says "the whole catalogue" (not the last 90 days).
    assert catalogue_meta.preview_window(_p(), {"end": "2026-09-20"}, "RADD") == (date(2019, 1, 1), date(2026, 9, 20))


# ---------------------------------------------------------------------------
# AlertsStep: render-based coverage of _toggle and the delete flow.
# ---------------------------------------------------------------------------
class _FakeMap:
    """Stands in for pysepal's MapApp: records what AlertsStep draws."""

    def __init__(self):
        self.layers = {}
        self.removed = []
        self.ee_calls = []

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def remove_layer(self, key, none_ok=False):
        self.removed.append(key)
        self.layers.pop(key, None)

    def add_ee_layer(self, img, vis, *, name, key):
        self.ee_calls.append((name, key))
        self.layers[key] = "ee"


def _project_with_source(name="RADD"):
    p = _p()
    p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    add_predefined_source(p, name)
    return p


def _app_state(p):
    state = AppState()
    state.project.value = p
    state.catalogue_meta.value = {"GLAD-L": {"bands": ["conf25", "conf26"], "end": "2026-09-20"}}
    return state


def _sync_spawn(monkeypatch):
    """Run MapLayers builds on the calling thread (deterministic ordering)."""
    import gui.scripts.map_layers as ml

    def spawn(target, args=(), **kw):
        target(*args)
        return threading.current_thread()

    monkeypatch.setattr(ml, "spawn_in_context", spawn)


def _track_threads(monkeypatch):
    """Let MapLayers spawn real worker threads, but keep handles to join them."""
    import gui.scripts.map_layers as ml

    real = ml.spawn_in_context
    threads = []

    def spawn(target, args=(), **kw):
        thread = real(target, args, **kw)
        threads.append(thread)
        return thread

    monkeypatch.setattr(ml, "spawn_in_context", spawn)
    return threads


def _stub_children(monkeypatch):
    """Swap AlertsStep's presentation children for recorders (mirrors
    tests/test_workflow_header.py's ``_spy_on_pipeline_header``), so the test
    calls the real callables AlertsStep wires up instead of driving raw
    ipyvuetify widgets buried inside a table/dialog those components own."""
    table_calls = []
    confirm_calls = []

    @solara.component
    def _RecorderTable(title, columns, rows, empty_text):
        table_calls.append({"rows": rows})

    @solara.component
    def _RecorderConfirm(open, on_cancel, on_confirm, title=None, message=None,
                          confirm_label=None, confirm_color="error"):
        confirm_calls.append({"open": open, "on_cancel": on_cancel, "on_confirm": on_confirm})

    @solara.component
    def _RecorderModal(open_, app_state, gee_interface, on_add_predefined, on_add_custom, on_save_custom,
                       editing=None):
        pass

    monkeypatch.setattr(alerts_step, "ProductTable", _RecorderTable)
    monkeypatch.setattr(alerts_step, "ConfirmDialog", _RecorderConfirm)
    monkeypatch.setattr(alerts_step, "SourceModal", _RecorderModal)
    return table_calls, confirm_calls


@solara.component
def _Harness(app_state, map_, gee_interface, layers):
    # AlertsStep's own use_notifications() call needs a mounted provider
    # rendered before it (pysepal 4 contract: see test_app_wiring.py's
    # test_render_fixed_tile_needs_the_provider_rendered_first).
    NotificationProvider()
    alerts_step.AlertsStep(app_state=app_state, map_=map_, gee_interface=gee_interface, layers=layers)


def _render(app_state, map_, gee_interface, layers):
    return reacton.render(
        _Harness(app_state=app_state, map_=map_, gee_interface=gee_interface, layers=layers),
        handle_error=False,
    )


def test_toggle_on_reports_the_ee_aoi_error_and_stays_off(monkeypatch, tmp_path):
    """Not-ready path (no active period) + the ee_aoi ValueError catch."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, _ = _stub_children(monkeypatch)

    p = _project_with_source()
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        bus = get_current_bus()
        toggle = table_calls[-1]["rows"][0]["actions"][0]["on_click"]

        # No AoiResult and no aoi.geojson sidecar on disk: ee_aoi raises.
        toggle()

        assert layers.on.value == frozenset()
        assert map_.ee_calls == [] and map_.layers == {}
        errors = [toast for toast in bus.toasts.value if toast.type.value == "error"]
        assert len(errors) == 1
        assert errors[0].message == t("steps.aoi.no_geometry")
    finally:
        rc.close()


def test_toggle_on_ready_source_draws_the_harmonized_raster_then_toggles_off(monkeypatch, tmp_path):
    """Ruling #46: plan_prepare(...).ready gates the harmonized preview, and
    the is_on branch hides (not rebuilds) on the next click."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, _ = _stub_children(monkeypatch)

    built = []

    def _fake_add_raster_layer(map_, path, *, key, name):
        built.append((str(path), key, name))
        map_.add_layer("raster", key=key)

    monkeypatch.setattr(raster_map_mod, "add_raster_layer", _fake_add_raster_layer)

    p = _project_with_source()
    per = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods[per.id] = per
    p.active_period = per.id
    p.artifacts[harm_key(per.id, "RADD")] = Artifact(
        key=harm_key(per.id, "RADD"), kind="harmonized", rel_path="periods/x/harmonized/radd.tif",
        fingerprint="f", inputs={}, content_id="c1", created_at=datetime.now(),
    )
    monkeypatch.setattr(
        alerts_step, "plan_prepare",
        lambda proj, pdir, period: [PrepareRow(source="RADD", raw=Status.CURRENT, harmonized=Status.CURRENT)],
    )

    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        toggle = table_calls[-1]["rows"][0]["actions"][0]["on_click"]

        toggle()  # off -> on: the ready gate takes the harmonized-raster branch
        expected_path = str(paths.resolve(paths.project_dir(p.name), "periods/x/harmonized/radd.tif"))
        assert built == [(expected_path, "src:RADD", "RADD")]
        assert layers.on.value == {"src:RADD"}

        toggle()  # on -> off: is_on branch hides, never calls the builder again
        assert layers.on.value == frozenset()
        assert built == [(expected_path, "src:RADD", "RADD")]
        assert "src:RADD" in map_.removed
    finally:
        rc.close()


def test_toggle_is_a_no_op_while_the_build_is_still_in_flight(monkeypatch, tmp_path):
    """Ruling #66/#10: a second click on a key that is already on and still
    building must not hide it or start a second build."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    threads = _track_threads(monkeypatch)
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "dummy-aoi")
    table_calls, _ = _stub_children(monkeypatch)

    started, release = threading.Event(), threading.Event()
    build_calls = []

    def _slow_build_image(key, win_start, win_end, aoi, meta):
        build_calls.append(key)
        started.set()
        assert release.wait(5)
        return SimpleNamespace(select=lambda band: "img")

    monkeypatch.setattr(catalogue, "build_image", _slow_build_image)

    p = _project_with_source()
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        toggle = table_calls[-1]["rows"][0]["actions"][0]["on_click"]

        toggle()  # starts the slow build on a real worker thread
        assert started.wait(5), "the layer build never started"
        assert layers.is_on("src:RADD") and "src:RADD" in layers.inflight.value

        toggle()  # is_on + inflight: must be a no-op, not a second build
        assert build_calls == ["RADD"]
        assert map_.ee_calls == []

        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive(), "a map-layer worker hung"

        assert layers.is_on("src:RADD")
        assert map_.ee_calls == [("RADD", "src:RADD")]
    finally:
        rc.close()


def test_delete_flow_hides_the_layer_and_removes_the_source(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, confirm_calls = _stub_children(monkeypatch)

    p = _project_with_source()
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        # As if the row was already toggled on before the user deletes it.
        layers.show("src:RADD", lambda: map_.add_layer("x", key="src:RADD"))
        assert layers.on.value == {"src:RADD"}

        delete = table_calls[-1]["rows"][0]["actions"][1]["on_click"]
        delete()
        assert confirm_calls[-1]["open"] is True

        confirm_calls[-1]["on_confirm"]()

        assert state.project.value.sources == {}
        assert layers.on.value == frozenset()
        assert "src:RADD" in map_.removed
        assert confirm_calls[-1]["open"] is False  # pending_delete reset
    finally:
        rc.close()


def test_delete_removes_the_sources_artifacts_and_their_files(monkeypatch, tmp_path):
    """Task 8: confirming a source delete drops its raw/harmonized artifact
    records (through ``cleanup.source_artifact_keys``/``drop_artifacts``) and
    deletes their files from disk, not only the source definition."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, confirm_calls = _stub_children(monkeypatch)

    p = _project_with_source()
    per = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods[per.id] = per
    p.active_period = per.id
    pdir = paths.project_dir(p.name)
    raw_rel, harm_rel = "periods/x/raw/radd.tif", "periods/x/harmonized/radd.tif"
    (pdir / raw_rel).parent.mkdir(parents=True, exist_ok=True)
    (pdir / raw_rel).write_bytes(b"x")
    (pdir / harm_rel).parent.mkdir(parents=True, exist_ok=True)
    (pdir / harm_rel).write_bytes(b"x")
    p.artifacts[raw_key(per.id, "RADD")] = Artifact(
        key=raw_key(per.id, "RADD"), kind="raw_alert", rel_path=raw_rel, fingerprint="f",
        inputs={}, content_id="c1", created_at=datetime.now(),
    )
    p.artifacts[harm_key(per.id, "RADD")] = Artifact(
        key=harm_key(per.id, "RADD"), kind="harmonized", rel_path=harm_rel, fingerprint="f",
        inputs={}, content_id="c2", created_at=datetime.now(),
    )

    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        delete = table_calls[-1]["rows"][0]["actions"][1]["on_click"]
        delete()
        confirm_calls[-1]["on_confirm"]()

        assert state.project.value.sources == {}
        assert state.project.value.artifacts == {}
        assert not (pdir / raw_rel).exists()
        assert not (pdir / harm_rel).exists()
    finally:
        rc.close()


def test_delete_is_refused_while_a_writer_runs(monkeypatch, tmp_path):
    """Ruling (c): the ``is_writing`` refusal is deliberately wider than "a
    Download & harmonize job" -- an Integrate reading the source's harmonized
    files must not lose them either while deleting it."""
    from gui.store.project_writers import writing

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, confirm_calls = _stub_children(monkeypatch)

    p = _project_with_source()
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        delete = table_calls[-1]["rows"][0]["actions"][1]["on_click"]
        delete()

        bus = get_current_bus()
        with writing(p.name):
            confirm_calls[-1]["on_confirm"]()

        assert state.project.value.sources == {"RADD": state.project.value.sources["RADD"]}
        warnings = [toast for toast in bus.toasts.value if toast.type.value == "warning"]
        assert warnings and warnings[-1].message == t("steps.alerts.delete_busy")
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# SourceModal: the ESC handler and the double-submit guard (ruling #65).
# The custom branch is covered in tests/test_source_modal.py.
# ---------------------------------------------------------------------------
def _modal(open_, added):
    state = AppState()
    state.project.value = _p()
    return SourceModal(open_=open_, app_state=state, gee_interface=object(), on_add_predefined=added.append,
                       on_add_custom=lambda fields, cov: added.append(fields),
                       on_save_custom=lambda name, changes, cov: added.append(changes))


def test_esc_closes_the_dialog_without_adding():
    open_ = solara.reactive(True)
    added = []

    async def main():  # the form's band-list task runs on this loop
        box, rc = reacton.render(_modal(open_, added), handle_error=False)
        try:
            dialog = rc.find(v.Dialog).widgets[0]
            dialog.fire_event("keydown.esc", None)
            assert open_.value is False
            assert added == []
        finally:
            rc.close()

    asyncio.run(main())


def test_a_fast_double_click_add_only_adds_the_source_once():
    open_ = solara.reactive(True)
    added = []

    async def main():
        box, rc = reacton.render(_modal(open_, added), handle_error=False)
        try:
            # the TaskButtonComponent's button, by its label
            [add_btn] = [b for b in rc.find(v.Btn).widgets if t("steps.alerts.add") in b.children]
            add_btn.fire_event("click", None)
            try:
                add_btn.fire_event("click", None)
            except ValueError:
                pass  # the form unmounted with the dialog: the second click reaches no handler
            assert added == ["GLAD-L"]
            assert open_.value is False
        finally:
            rc.close()

    asyncio.run(main())
