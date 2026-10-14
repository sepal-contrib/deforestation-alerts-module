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
import pytest
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.fingerprint import Status, harm_key, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, Artifact, BandMap, NativeGrid, Period, Project, RegionCoverage,
    SourceCoverage, add_custom_source, add_predefined_source,
)
from gui.i18n import t, tn
from gui.scripts import aoi_io as aoi_io_mod
from deforestation_alerts.engine.coverage import preview_window
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


def _cov(fraction=1.0, first=date(2020, 1, 1), last=date(2026, 9, 23)):
    return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction,
                          regions=[RegionCoverage(name="sa", fraction=fraction, first=first, last=last)])


def test_preview_window_prefers_the_active_period():
    p = _p()
    per = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods["x"] = per
    p.active_period = "x"
    assert preview_window(p, "RADD") == per.search_window()


def test_preview_window_without_period_is_the_covered_span():
    p = _project_with_source("RADD")
    p.coverage = {"RADD": _cov()}
    assert preview_window(p, "RADD") == (date(2020, 1, 1), date(2026, 9, 23))


def test_preview_window_of_an_undated_source_is_every_date():
    p = _project_with_source("RADD")
    p.coverage = {"RADD": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=1.0,
                                         regions=[RegionCoverage(name="mine", fraction=1.0)])}
    assert preview_window(p, "RADD") == (date(2000, 1, 1), date.today())


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


def _project_with_source(name="RADD", covered=True):
    """A project with one predefined source; ``covered`` saves a coverage
    record that covers every window (as the conftest ``cover`` fixture does),
    so its row can be shown on the map (Task 18: an unchecked row cannot)."""
    p = _p()
    p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    add_predefined_source(p, name)
    if covered:
        p.coverage = {name: _cov(first=date(2000, 1, 1), last=date(2100, 1, 1))}
    return p


def _app_state(p):
    state = AppState()
    state.project.value = p
    state.catalogue_meta.value = {"GLAD-L": {"bands": ["conf25", "conf26"]}}
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
        name = "RADD · 2026-01-01 → 2026-01-31"  # spec §4.3: the harmonized layer's name
        assert built == [(expected_path, "src:RADD", name)]
        assert layers.on.value == {"src:RADD"} and layers.content_of("src:RADD") == "c1"
        (legend,) = layers.legends.value
        assert legend.label.key == "legend.name.harmonized" and legend.spec.title.key == "legend.title.harmonized"

        toggle()  # on -> off: is_on branch hides, never calls the builder again
        assert layers.on.value == frozenset() and layers.legends.value == ()
        assert built == [(expected_path, "src:RADD", name)]
        assert "src:RADD" in map_.removed
    finally:
        rc.close()


def test_check_again_under_the_table_after_a_failed_coverage_check(monkeypatch, tmp_path, cover):
    """Spec §6.2 "Failure": with a source left without a coverage record, the
    step offers Check again (``coverage_retry``); none once every source has
    its record for the AOI, and none while nothing failed."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _stub_children(monkeypatch)
    p = _project_with_source(covered=False)
    state = _app_state(p)

    box, rc = _render(state, _FakeMap(), object(), MapLayers(_FakeMap()))
    try:
        def check_again():
            return [w for w in rc.find(v.Btn).widgets if t("coverage.check_again") in w.children]

        assert check_again() == []  # no failure
        state.coverage_error.set("quota exceeded")
        [button] = check_again()
        button.fire_event("click", None)
        assert state.coverage_retry.value == 1
        state.update_project(p.name, lambda live: cover(live, "RADD"))
        assert check_again() == []  # every source has its record
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
        assert map_.ee_calls == [("RADD · preview", "src:RADD")]
    finally:
        rc.close()


def test_a_preview_without_a_period_is_named_and_carries_its_legend(monkeypatch, tmp_path):
    """Spec §4.3, §9.4: with no period, a covered RADD row previews its Earth
    Engine alerts as "RADD · preview" with the preview legend."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "aoi")
    monkeypatch.setattr(catalogue, "build_image",
                        lambda key, a, b, aoi, meta: SimpleNamespace(select=lambda band: f"img.{band}"))
    table_calls, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        table_calls[-1]["rows"][0]["actions"][0]["on_click"]()
        assert map_.ee_calls == [("RADD · preview", "src:RADD")]
        (legend,) = layers.legends.value
        assert legend.layer_id == "src:RADD" and legend.label.key == "legend.name.preview"
        a, b = preview_window(p, "RADD")
        assert legend.spec.title.key == "legend.title.preview"
        assert dict(legend.spec.title.args) == {"source": "RADD", "start": str(a), "end": str(b)}
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
# Task 18: coverage chips, dates and reasons per row; custom rows with edit
# and delete (spec §6.2, §5.1).
# ---------------------------------------------------------------------------
def _render_step(state, layers, recorders):
    """Render AlertsStep with ProductTable/ConfirmDialog/SourceModal swapped for recorders."""
    import gui.tile.alerts_step as mod

    @reacton.component
    def Page():
        NotificationProvider()
        mod.AlertsStep(app_state=state, map_=layers.map_, gee_interface=SimpleNamespace(), layers=layers)

    return reacton.render(Page(), handle_error=False)


def _add_mine(p, **kw):
    fields = dict(name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                  date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                  native=NativeGrid(crs="EPSG:4326", scale=10.0))
    return add_custom_source(p, **{**fields, **kw})


def _used_by_a_set(p, name="mine"):
    p.alert_sets = {f"x__{name}": AlertSet(id=f"x__{name}", period_id="x", sources=[name], digit_map={3: name},
                                           params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                           footprint_rel_path="f", fingerprint="f", created_at=datetime.now())}


def _toasts(bus, kind, message):
    """How many times ``message`` was toasted: pysepal merges identical
    non-error toasts within 2 s into one, bumping its ``count`` (ruling C2)."""
    return sum(x.count for x in bus.toasts.value if x.type.value == kind and x.message == message)


def test_rows_show_kind_coverage_dates_and_the_radd_note(monkeypatch):
    import gui.tile.alerts_step as mod

    seen = {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: None)
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    p.aoi = p.aoi.model_copy(update={"digest": "d"})
    add_custom_source(p, name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="ccdc",
                      date_encoding="decimal_year", band_map=BandMap(alert="detection_count", date="first_detection_date"),
                      native=NativeGrid(crs="EPSG:4326", scale=20.0))
    p.coverage = {"RADD": _cov(), "mine": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.35,
                                                        regions=[RegionCoverage(name="mine", fraction=0.35)])}
    state = _app_state(p)
    box, rc = _render_step(state, MapLayers(_FakeMap()), {})
    try:
        rows = {r["key"]: r for r in seen["rows"]}
        assert seen["columns"] == mod.source_columns()
        assert len(rows["RADD"]["cells"]) == 2 and rows["RADD"]["cells"][1]["value"] == "covers the AOI"
        # spec D4: the kind and the dates are the row's first, muted note
        assert rows["RADD"]["notes"][0] == {"text": "predefined · 2020-01-01 → 2026-09-23", "tone": None}
        assert any("primary humid tropical forest" in n["text"] for n in rows["RADD"]["notes"][1:])
        assert rows["mine"]["notes"][0] == {"text": "custom · SEPAL CCDC alerts", "tone": None}  # no dates
        assert rows["mine"]["cells"][1]["value"] == "covers 35% of the AOI"
        assert [a["kind"] for a in rows["mine"]["actions"]] == ["map_toggle", "edit", "delete"]
        assert len(rows["mine"]["actions"]) == mod.SOURCE_ACTIONS  # the budget's count is the most a row has
        assert [a["kind"] for a in rows["RADD"]["actions"]] == ["map_toggle", "delete"]  # no edit: predefined
        assert not any(a.get("disabled") for a in rows["mine"]["actions"])
    finally:
        rc.close()


def test_show_on_map_is_disabled_with_the_reason_for_an_uncovered_source(monkeypatch):
    import gui.tile.alerts_step as mod

    seen = {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: None)
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("GLAD-S2")
    p.aoi = p.aoi.model_copy(update={"digest": "d"})
    p.coverage = {"GLAD-S2": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.0)}
    box, rc = _render_step(_app_state(p), MapLayers(_FakeMap()), {})
    try:
        row = seen["rows"][0]
        toggle = next(a for a in row["actions"] if a["kind"] == "map_toggle")
        assert toggle["disabled"] and any("does not cover this AOI" in n["text"] for n in row["notes"])
    finally:
        rc.close()


def test_show_on_map_is_disabled_until_the_coverage_is_checked_and_when_the_window_has_no_data(monkeypatch):
    """Spec §6.2: an unchecked row, and a row whose data does not reach the
    active window, read their reason and never build an Earth Engine preview."""
    import gui.tile.alerts_step as mod

    seen = {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: None)
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    monkeypatch.setattr(catalogue, "build_image", lambda *a, **k: pytest.fail("no preview for a blocked row"))
    p = _project_with_source("RADD", covered=False)
    add_predefined_source(p, "GLAD-S2")
    per = Period(id="x", start=date(2027, 1, 1), end=date(2027, 1, 31))
    p.periods = {"x": per}
    p.active_period = "x"
    p.coverage = {"GLAD-S2": _cov()}  # its data ends 2026-09-23, before the window
    state = _app_state(p)
    box, rc = _render_step(state, MapLayers(_FakeMap()), {})
    try:
        rows = {r["key"]: r for r in seen["rows"]}
        for name, reason in [("RADD", t("coverage.unchecked")), ("GLAD-S2", "2026-09-23")]:
            toggle = next(a for a in rows[name]["actions"] if a["kind"] == "map_toggle")
            assert toggle["disabled"], name
            assert any(reason in n["text"] and n["tone"] == "error" for n in rows[name]["notes"]), rows[name]["notes"]
        assert rows["RADD"]["notes"][0] == {"text": t("steps.alerts.kind_predefined"), "tone": None}  # no dates yet
    finally:
        rc.close()


def test_a_custom_earth_engine_source_previews_its_alert_band_over_the_covered_span(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "aoi")
    built = []

    def _build_custom(src, a, b, aoi):
        built.append((src.name, a, b, aoi))
        return SimpleNamespace(select=lambda band: f"img.{band}")

    monkeypatch.setattr(catalogue, "build_custom_image", _build_custom)
    table_calls, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    p.coverage = {**p.coverage, "mine": _cov(first=date(2021, 3, 1), last=date(2025, 6, 30))}
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        row = next(r for r in table_calls[-1]["rows"] if r["key"] == "mine")
        row["actions"][0]["on_click"]()
        assert built == [("mine", date(2021, 3, 1), date(2025, 6, 30), "aoi")]
        assert map_.ee_calls == [("mine · preview", "src:mine")]
    finally:
        rc.close()


def test_a_geotiff_source_has_no_preview_until_it_is_harmonized(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p, origin="file", ref="/data/mine.tif", asset_type="FILE", band_map=BandMap(alert=1, date=2))
    p.coverage = {**p.coverage, "mine": _cov()}
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        bus = get_current_bus()
        row = next(r for r in table_calls[-1]["rows"] if r["key"] == "mine")
        row["actions"][0]["on_click"]()
        assert layers.on.value == frozenset() and map_.layers == {}
        assert _toasts(bus, "info", t("steps.alerts.file_preview_after_harmonize")) == 1
    finally:
        rc.close()


def test_edit_is_refused_once_a_set_used_the_source(monkeypatch):
    import gui.tile.alerts_step as mod

    seen, modal = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    add_custom_source(p, name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                      native=NativeGrid(crs="EPSG:4326", scale=10.0))
    p.alert_sets = {"x__mine": AlertSet(id="x__mine", period_id="x", sources=["mine"], digit_map={3: "mine"},
                                        params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                        footprint_rel_path="f", fingerprint="f", created_at=datetime.now())}
    state = _app_state(p)

    async def main():
        box, rc = _render_step(state, MapLayers(_FakeMap()), {})
        try:
            bus = get_current_bus()
            row = next(r for r in seen["rows"] if r["key"] == "mine")
            next(a for a in row["actions"] if a["kind"] == "edit")["on_click"]()
            assert modal["editing"] is None  # the modal is not opened for an edit
            assert modal["open_"].value is False
            warnings = [x.message for x in bus.toasts.value if x.type.value == "warning"]
            assert any("add a new one instead" in m for m in warnings), warnings
        finally:
            rc.close()

    asyncio.run(main())


def test_edit_and_its_save_wait_for_the_projects_writers(monkeypatch):
    """Spec §5.1: editing is refused while a job writes into the project, when
    the modal would open and at Save (a job can start while it is open)."""
    import gui.tile.alerts_step as mod
    from gui.store.project_writers import writing

    seen, modal = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    add_custom_source(p, name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                      native=NativeGrid(crs="EPSG:4326", scale=10.0))
    state = _app_state(p)

    async def main():
        box, rc = _render_step(state, MapLayers(_FakeMap()), {})
        try:
            bus = get_current_bus()
            row = next(r for r in seen["rows"] if r["key"] == "mine")
            with writing(p.name):
                next(a for a in row["actions"] if a["kind"] == "edit")["on_click"]()
                assert modal["open_"].value is False  # not opened
                # the modal shows the refusal inline: a translated message, never a key (Task 17)
                with pytest.raises(ValueError, match=t("steps.alerts.edit_busy")):
                    modal["on_save_custom"]("mine", {"name": "mine", "ref": "users/x/b"}, _cov())
            assert state.project.value.sources["mine"].ref == "users/x/a"  # nothing changed
            assert _toasts(bus, "warning", t("steps.alerts.edit_busy")) == 2
        finally:
            rc.close()

    asyncio.run(main())


def test_save_applies_the_edit_and_its_coverage_and_drops_the_old_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    import gui.tile.alerts_step as mod

    seen, modal = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    _add_mine(p)
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    async def main():
        box, rc = _render_step(state, layers, {})
        try:
            row = next(r for r in seen["rows"] if r["key"] == "mine")
            next(a for a in row["actions"] if a["kind"] == "edit")["on_click"]()
            assert modal["editing"] == "mine" and modal["open_"].value is True
            layers.show("src:mine", lambda: map_.add_layer("x", key="src:mine"))  # the old asset's preview
            cov = _cov(fraction=0.5)
            modal["on_save_custom"]("mine", {"name": "mine", "ref": "users/x/b"}, cov)
            live = state.project.value
            assert live.sources["mine"].ref == "users/x/b" and live.sources["mine"].digit == 3
            assert live.coverage["mine"] == cov
            assert layers.on.value == frozenset() and "src:mine" in map_.removed
        finally:
            rc.close()

    asyncio.run(main())


def test_save_is_refused_once_a_set_used_the_source_meanwhile(monkeypatch):
    import gui.tile.alerts_step as mod

    seen, modal = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    _add_mine(p)
    state = _app_state(p)

    async def main():
        box, rc = _render_step(state, MapLayers(_FakeMap()), {})
        try:
            row = next(r for r in seen["rows"] if r["key"] == "mine")
            next(a for a in row["actions"] if a["kind"] == "edit")["on_click"]()
            state.update_project(p.name, _used_by_a_set)  # an Integrate ran while the modal was open
            with pytest.raises(ValueError, match=t("steps.alerts.edit_used")):
                modal["on_save_custom"]("mine", {"name": "mine", "ref": "users/x/b"}, _cov())
            assert state.project.value.sources["mine"].ref == "users/x/a"
        finally:
            rc.close()

    asyncio.run(main())


def test_add_custom_saves_the_source_with_its_coverage_and_raises_translated_errors(monkeypatch):
    import gui.tile.alerts_step as mod

    seen, modal = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: None)
    p = _project_with_source("RADD")
    state = _app_state(p)
    fields = dict(name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                  date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                  native=NativeGrid(crs="EPSG:4326", scale=10.0))

    async def main():
        box, rc = _render_step(state, MapLayers(_FakeMap()), {})
        try:
            cov = _cov(fraction=0.4)
            modal["on_add_custom"](fields, cov)
            live = state.project.value
            assert live.sources["mine"].digit == 3 and live.coverage["mine"] == cov
            # the name was taken meanwhile: the modal shows the translated text inline
            with pytest.raises(ValueError, match=t("steps.alerts.name_duplicate")):
                modal["on_add_custom"](fields, cov)
            for i in range(5):
                modal["on_add_custom"]({**fields, "name": f"m{i}"}, cov)
            with pytest.raises(ValueError, match=t("steps.alerts.cap_reached")):
                modal["on_add_custom"]({**fields, "name": "m9"}, cov)
        finally:
            rc.close()

    asyncio.run(main())


def test_delete_confirmation_names_the_sets_that_used_the_source(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    import gui.tile.alerts_step as mod

    seen, confirm = {}, {}
    monkeypatch.setattr(mod, "ProductTable", lambda **kw: seen.update(kw))
    monkeypatch.setattr(mod, "SourceModal", lambda **kw: None)
    monkeypatch.setattr(mod, "ConfirmDialog", lambda **kw: confirm.update(kw))
    p = _project_with_source("RADD")
    _add_mine(p)
    _used_by_a_set(p)
    p.coverage = {**p.coverage, "mine": _cov()}
    state = _app_state(p)
    box, rc = _render_step(state, MapLayers(_FakeMap()), {})
    try:
        row = next(r for r in seen["rows"] if r["key"] == "mine")
        next(a for a in row["actions"] if a["kind"] == "delete")["on_click"]()
        assert confirm["open"] is True and confirm["message"] == tn("steps.alerts.delete_used", 1)
        confirm["on_confirm"]()
        live = state.project.value
        assert "mine" not in live.sources and "mine" not in live.coverage  # a custom source's coverage goes too
        assert "RADD" in live.coverage
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
