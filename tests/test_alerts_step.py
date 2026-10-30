"""Alerts step (spec §2): the active period's sources, their AOI and dates
chips, their download, preview, details and removal from the period; plus
``SourceModal``'s ESC handler and double-submit guard.

The render tests use ``reacton.render`` against the real components,
mirroring ``tests/test_workflow_header.py``'s pattern: presentation children
that are not under test (``ProductTable``, ``ConfirmDialog``,
``DetailsDialog``, ``SourceModal``) are swapped for recorders so the test can
call the exact callables ``AlertsStep`` wires up, while the module under
test, real ``MapLayers`` and a real ``Project`` run unmocked. The download
tests (``dl`` fixture) keep the real dialogs and queue the jobs, as
tests/test_prepare_step.py's ``step`` fixture does.
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
from pysepal.solara.notifications.notifier import Notifier

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import Note, preview_window
from deforestation_alerts.engine.fingerprint import Status, harm_key, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, Artifact, BandMap, NativeGrid, Period, Project, RegionCoverage,
    SourceCoverage, add_custom_source, add_predefined_source, set_period_source,
)
from gui.i18n import t, tn
from gui.scripts import aoi_io as aoi_io_mod
from gui.scripts import prepare_scope
from gui.scripts import raster_map as raster_map_mod
from gui.scripts.coverage_ui import coverage_chips
from gui.scripts.map_layers import MapLayers
from gui.scripts.prepare_view import source_label
from gui.store.state_manager import AppState
from gui.tile import alerts_step
from gui.widget import source_modal
from gui.widget.source_modal import SourceModal
from tests.test_prepare_step import (
    JAN, PARAGUAY, _arts, _button, _click, _closed, _confirm, _named, _project, _state, _stop_shown, _texts,
    _with_mask, _with_tif,
)

PER = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))


def _p():
    return Project(name="d", created_at=datetime.now(), updated_at=datetime.now())


def _cov(fraction=1.0, first=date(2020, 1, 1), last=date(2026, 9, 23)):
    return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction,
                          regions=[RegionCoverage(name="sa", fraction=fraction, first=first, last=last)])


def test_the_add_list_offers_the_sources_not_in_the_active_period():
    """Spec §2: the predefined sources and the project's sources not in this
    period; without a period, every project source is in use."""
    p = _p()
    assert source_modal.available_sources(p) == ["GLAD-L", "RADD", "GLAD-S2"]
    add_predefined_source(p, "RADD")
    assert source_modal.available_sources(p) == ["GLAD-L", "GLAD-S2"]
    _add_mine(p)
    add_predefined_source(p, "GLAD-L")
    p.periods = {PER.id: PER.model_copy(update={"sources": ["GLAD-L"]})}
    p.active_period = PER.id
    assert source_modal.available_sources(p) == ["RADD", "GLAD-S2", "mine"]


def test_preview_window_prefers_the_active_period():
    p = _p()
    per = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods["x"] = per
    p.active_period = "x"
    assert preview_window(p, "RADD") == per.search_window()


def test_preview_window_without_period_is_the_covered_span():
    p = _project_with_source("RADD", period=False)
    p.coverage = {"RADD": _cov()}
    assert preview_window(p, "RADD") == (date(2020, 1, 1), date(2026, 9, 23))


def test_preview_window_of_an_undated_source_is_every_date():
    p = _project_with_source("RADD", period=False)
    p.coverage = {"RADD": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=1.0,
                                         regions=[RegionCoverage(name="mine", fraction=1.0)])}
    assert preview_window(p, "RADD") == (date(2000, 1, 1), date.today())


# ---------------------------------------------------------------------------
# The chips (spec §2): AOI share and the period's dates
# ---------------------------------------------------------------------------
def test_coverage_chips_name_aoi_and_dates():
    p = _project_with_source("RADD", period=False)
    sep = Period(id="s", start=date(2026, 9, 1), end=date(2026, 10, 31))
    p.coverage = {"RADD": _cov(1.0, last=date(2026, 8, 31))}  # its data ends before the window
    chips = coverage_chips(p, "RADD", sep)
    assert chips[0] == {"label": t("steps.alerts.chip_aoi", pct="100"), "tone": "ok"}
    assert chips[1] == {"label": t("steps.alerts.chip_dates_none", last="2026-08-31"), "tone": "error"}
    p.coverage = {"RADD": _cov(0.62, last=date(2026, 9, 30))}  # part of the window
    chips = coverage_chips(p, "RADD", sep)
    assert chips[0] == {"label": t("steps.alerts.chip_aoi", pct="62"), "tone": "warning"}
    assert chips[1] == {"label": t("steps.alerts.chip_dates_part", last="2026-09-30"), "tone": "warning"}
    p.coverage = {"RADD": _cov(1.0, last=date(2026, 11, 15))}
    assert coverage_chips(p, "RADD", sep)[1] == {"label": t("steps.alerts.chip_dates_ok", last="2026-11-15"),
                                                 "tone": "ok"}


def test_coverage_chips_without_a_record_or_dates():
    p = _project_with_source("RADD", covered=False, period=False)
    assert coverage_chips(p, "RADD", PER) == [{"label": t("coverage.unchecked"), "tone": "error"}]
    assert coverage_chips(p, "RADD", PER, pending=True) == [{"label": t("coverage.chip_checking"), "tone": None}]
    _add_mine(p)  # a custom source has no catalogue dates: no Dates chip
    p.coverage = {"mine": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.5,
                                         regions=[RegionCoverage(name="mine", fraction=0.5)])}
    assert coverage_chips(p, "mine", PER) == [{"label": t("steps.alerts.chip_aoi", pct="50"), "tone": "warning"}]
    p.coverage = {"RADD": _cov(0.0)}  # covers none of the AOI: its dates do not matter
    assert coverage_chips(p, "RADD", PER) == [{"label": t("steps.alerts.chip_aoi", pct="0"), "tone": "error"}]


# ---------------------------------------------------------------------------
# AlertsStep: render-based coverage of _toggle, the rows and the remove flow.
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


def _project_with_source(name="RADD", covered=True, period=True):
    """A project with one predefined source; ``covered`` saves a coverage
    record that covers every window (as the conftest ``cover`` fixture does),
    so its row can be shown on the map (Task 18: an unchecked row cannot).
    ``period``: January 2026 is the active period and uses the source."""
    p = _p()
    p.aoi = AoiRecord(spec={}, name="x", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    if period:
        p.periods = {PER.id: PER}
        p.active_period = PER.id
    add_predefined_source(p, name, period_id=PER.id if period else None)
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
    """Swap AlertsStep's presentation children for recorders, so the test
    calls the real callables AlertsStep wires up. Returns (table, confirm,
    modal, details): every render's table rows, every render's confirms by
    title order (the Download again confirm, then Remove), the last modal and
    details props."""
    table_calls, confirm_calls, modal, details = [], [], {}, {}
    monkeypatch.setattr(alerts_step, "ProductTable", lambda **kw: table_calls.append({"rows": kw["rows"]}))
    monkeypatch.setattr(alerts_step, "ConfirmDialog", lambda **kw: confirm_calls.append(kw))
    monkeypatch.setattr(alerts_step, "SourceModal", lambda **kw: modal.update(kw))
    monkeypatch.setattr(alerts_step, "DetailsDialog", lambda **kw: details.update(kw))
    return table_calls, confirm_calls, modal, details


def _remove_confirm(confirm_calls):
    """The last render's Remove from this period confirm (rendered after Download again's)."""
    return confirm_calls[-1]


@solara.component
def _Harness(app_state, map_, gee_interface, layers, jobs=None):
    # AlertsStep's own use_notifications() call needs a mounted provider
    # rendered before it (pysepal 4 contract: see test_app_wiring.py's
    # test_render_fixed_tile_needs_the_provider_rendered_first).
    NotificationProvider()
    own = solara.use_memo(prepare_scope.PrepareJobs, [])  # as the shell does
    alerts_step.AlertsStep(app_state=app_state, map_=map_, gee_interface=gee_interface, layers=layers,
                           jobs=jobs or own)


def _render(app_state, map_=None, gee_interface=None, layers=None, jobs=None):
    map_ = map_ or _FakeMap()
    return reacton.render(
        _Harness(app_state=app_state, map_=map_, gee_interface=gee_interface or object(),
                 layers=layers or MapLayers(map_), jobs=jobs),
        handle_error=False,
    )


def _row(table_calls, name):
    return next(r for r in table_calls[-1]["rows"] if r["key"] == name)


def _act(row, kind):
    return next(a for a in row["actions"] if a["kind"] == kind)


def test_without_a_period_the_step_is_locked(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, *_ = _stub_children(monkeypatch)
    box, rc = _render(_app_state(_project_with_source(period=False)))
    try:
        assert table_calls == []
        assert [w for w in rc.find(v.Alert).widgets if t("workflow.lock_alerts") in str(w.children)]
    finally:
        rc.close()


def test_the_period_box_counts_one_day_of_look_back_in_the_singular(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _stub_children(monkeypatch)
    p = _project_with_source()
    p.periods = {PER.id: PER.model_copy(update={"look_back_days": 1})}
    box, rc = _render(_app_state(p))
    try:
        texts = [str(w.children) for w in rc.find(v.Html).widgets]
        assert any("(1 day of look-back)" in s for s in texts), texts
    finally:
        rc.close()


def test_toggle_on_reports_the_ee_aoi_error_and_stays_off(monkeypatch, tmp_path):
    """Not-ready path (nothing downloaded) + the ee_aoi ValueError catch."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, *_ = _stub_children(monkeypatch)

    p = _project_with_source()
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(state, map_, object(), layers)
    try:
        bus = get_current_bus()
        toggle = _act(_row(table_calls, "RADD"), "map_toggle")["on_click"]

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
    table_calls, *_ = _stub_children(monkeypatch)

    built = []

    def _fake_add_raster_layer(map_, path, *, key, name):
        built.append((str(path), key, name))
        map_.add_layer("raster", key=key)

    monkeypatch.setattr(raster_map_mod, "add_raster_layer", _fake_add_raster_layer)

    p = _project_with_source()
    p.artifacts[harm_key(PER.id, "RADD")] = Artifact(
        key=harm_key(PER.id, "RADD"), kind="harmonized", rel_path="periods/x/harmonized/radd.tif",
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
        toggle = _act(_row(table_calls, "RADD"), "map_toggle")["on_click"]

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

    box, rc = _render(state)
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
    table_calls, *_ = _stub_children(monkeypatch)

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
        toggle = _act(_row(table_calls, "RADD"), "map_toggle")["on_click"]

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


def test_a_preview_is_named_and_carries_its_legend(monkeypatch, tmp_path):
    """Spec §4.3, §9.4: a covered RADD row not downloaded yet previews its
    Earth Engine alerts over the period's window as "RADD · preview" with the
    preview legend."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "aoi")
    monkeypatch.setattr(catalogue, "build_image",
                        lambda key, a, b, aoi, meta: SimpleNamespace(select=lambda band: f"img.{band}"))
    table_calls, *_ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        _act(_row(table_calls, "RADD"), "map_toggle")["on_click"]()
        assert map_.ee_calls == [("RADD · preview", "src:RADD")]
        (legend,) = layers.legends.value
        assert legend.layer_id == "src:RADD" and legend.label.key == "legend.name.preview"
        a, b = PER.search_window()
        assert legend.spec.title.key == "legend.title.preview"
        assert dict(legend.spec.title.args) == {"source": "RADD", "start": str(a), "end": str(b)}
    finally:
        rc.close()


def _add_mine(p, **kw):
    fields = dict(name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                  date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                  native=NativeGrid(crs="EPSG:4326", scale=10.0), period_id=p.active_period)
    return add_custom_source(p, **{**fields, **kw})


def _used_by_a_set(p, name="mine", period_id=PER.id):
    p.alert_sets = {f"x__{name}": AlertSet(id=f"x__{name}", period_id=period_id, sources=[name],
                                           digit_map={3: name}, params=AlertSetParams(), store_rel_path="s",
                                           combined_rel_path="c", footprint_rel_path="f", fingerprint="f",
                                           created_at=datetime.now())}


def _toasts(bus, kind, message):
    """How many times ``message`` was toasted: pysepal merges identical
    non-error toasts within 2 s into one, bumping its ``count`` (ruling C2)."""
    return sum(x.count for x in bus.toasts.value if x.type.value == kind and x.message == message)


def _rows(p, **over):
    kw = dict(mine=set(), errors={}, stages={}, progress={}, on=set(), building=set(), locked=False, writing=False,
              stopped=frozenset(), run=lambda s: None, toggle=lambda s: None, on_info=lambda s: None,
              on_refresh=lambda s: None, on_remove=lambda s: None)
    kw.update(over)
    return {r["key"]: r for r in alerts_step.alert_rows(p, "/tmp/x", p.periods[p.active_period], **kw)}


def test_alert_rows_list_only_the_period_sources():
    p = _project_with_source("GLAD-L")
    add_predefined_source(p, "RADD", period_id=PER.id)
    assert list(_rows(p)) == ["GLAD-L", "RADD"]
    set_period_source(p, PER.id, "RADD", False)
    assert list(_rows(p)) == ["GLAD-L"]


def test_a_row_shows_its_chips_its_status_and_four_actions():
    p = _project_with_source("RADD")
    p.coverage = {"RADD": _cov(0.35, first=date(2000, 1, 1), last=date(2100, 1, 1))}
    row = _rows(p, rows=[PrepareRow("RADD", Status.MISSING, Status.MISSING)])["RADD"]
    title, state = row["cells"]
    assert title["chips"] == [{"value": t("steps.alerts.chip_aoi", pct="35"), "color": "warning"},
                              {"value": t("steps.alerts.chip_dates_ok", last="2100-01-01"), "color": "success"}]
    assert state["type"] == "verb" and state["label"] == t("steps.prepare.verb_download")
    assert [a["kind"] for a in row["actions"]] == ["map_toggle", "info", "refresh", "delete"]
    assert len(row["actions"]) == alerts_step.SOURCE_ACTIONS
    assert _act(row, "delete")["title"] == t("steps.alerts.action_remove")
    assert any("primary humid tropical forest" in n["text"] for n in row["notes"])  # RADD's note
    downloaded = _rows(p, rows=[PrepareRow("RADD", Status.CURRENT, Status.MISSING)])["RADD"]
    assert downloaded["cells"][1]["label"] == t("steps.alerts.state_downloaded")
    assert not _act(downloaded, "refresh")["disabled"]  # a live catalogue changes under the same window


def test_file_source_row_reads_read_file(tmp_path):
    p = _with_tif(tmp_path, sidecar=True)
    rows = _rows(p, rows=[PrepareRow("tif", Status.MISSING, Status.MISSING)])
    assert rows["tif"]["cells"][1]["label"] == t("steps.alerts.read_file")
    refresh = _act(rows["tif"], "refresh")  # nothing to download again from a file
    assert refresh["disabled"] and refresh["title"] == t("steps.prepare.action_nothing_to_fetch")
    key, pkey = alerts_step.inflight_key("d", JAN.id, "tif"), alerts_step.progress_key("d", JAN.id, "source", "tif")
    reading = _rows(p, rows=[PrepareRow("tif", Status.MISSING, Status.MISSING)], mine={key},
                    stages={pkey: "download"})["tif"]
    assert reading["cells"][1]["label"] == t("steps.alerts.state_reading")


def test_download_again_is_disabled_while_busy_on_a_blocked_row_and_on_a_review_copy():
    p = _project_with_source("RADD")
    add_predefined_source(p, "GLAD-S2", period_id=PER.id)
    blocked = PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                         (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),))
    plan = [PrepareRow("RADD", Status.CURRENT, Status.MISSING), blocked]
    key = alerts_step.inflight_key("d", PER.id, "RADD")
    busy = _rows(p, rows=plan, mine={key})
    assert _act(busy["RADD"], "refresh")["disabled"] and _act(busy["RADD"], "delete")["disabled"]
    assert busy["RADD"]["cells"][1]["label"] == t("steps.prepare.state_queued")
    assert _act(busy["GLAD-S2"], "refresh")["disabled"]  # blocked
    assert busy["GLAD-S2"]["cells"][0]["subtitle"].startswith("Not covered")
    locked = _rows(p, rows=plan, locked=True)["RADD"]
    assert _act(locked, "refresh")["disabled"] and _act(locked, "delete")["disabled"]
    assert _act(_rows(p, rows=plan, writing=True)["RADD"], "delete")["disabled"]


def test_show_on_map_is_disabled_for_an_uncovered_source(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, *_ = _stub_children(monkeypatch)
    p = _project_with_source("GLAD-S2")
    p.coverage = {"GLAD-S2": SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.0)}
    box, rc = _render(_app_state(p))
    try:
        row = _row(table_calls, "GLAD-S2")
        assert _act(row, "map_toggle")["disabled"]
        assert "does not cover this AOI" in row["cells"][0]["subtitle"]
        assert row["cells"][1]["type"] == "state"  # blocked: no Download pill
    finally:
        rc.close()


def test_show_on_map_is_disabled_until_the_coverage_is_checked_and_when_the_window_has_no_data(monkeypatch, tmp_path):
    """Spec §6.2: an unchecked row, and a row whose data does not reach the
    active window, read their reason and never build an Earth Engine preview."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, *_ = _stub_children(monkeypatch)
    monkeypatch.setattr(catalogue, "build_image", lambda *a, **k: pytest.fail("no preview for a blocked row"))
    p = _project_with_source("RADD", covered=False, period=False)
    add_predefined_source(p, "GLAD-S2")
    per = Period(id="x", start=date(2027, 1, 1), end=date(2027, 1, 31), sources=["RADD", "GLAD-S2"])
    p.periods = {"x": per}
    p.active_period = "x"
    p.coverage = {"GLAD-S2": _cov()}  # its data ends 2026-09-23, before the window
    box, rc = _render(_app_state(p))
    try:
        radd, glad = _row(table_calls, "RADD"), _row(table_calls, "GLAD-S2")
        for row in (radd, glad):
            assert _act(row, "map_toggle")["disabled"], row["key"]
        assert radd["cells"][0]["chips"] == [{"value": t("coverage.unchecked"), "color": "error"}]
        assert "2026-09-23" in glad["cells"][0]["subtitle"]
        assert glad["cells"][0]["chips"][1] == {"value": t("steps.alerts.chip_dates_none", last="2026-09-23"),
                                                "color": "error"}
    finally:
        rc.close()


def test_a_custom_earth_engine_source_previews_its_alert_band_over_the_periods_window(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "aoi")
    built = []

    def _build_custom(src, a, b, aoi):
        built.append((src.name, a, b, aoi))
        return SimpleNamespace(select=lambda band: f"img.{band}")

    monkeypatch.setattr(catalogue, "build_custom_image", _build_custom)
    table_calls, *_ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    p.coverage = {**p.coverage, "mine": _cov(first=date(2021, 3, 1), last=date(2100, 6, 30))}
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        _act(_row(table_calls, "mine"), "map_toggle")["on_click"]()
        assert built == [("mine", *PER.search_window(), "aoi")]
        assert map_.ee_calls == [("mine · preview", "src:mine")]
    finally:
        rc.close()


def test_a_geotiff_source_has_no_preview_until_it_is_harmonized(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, *_ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p, origin="file", ref="/data/mine.tif", asset_type="FILE", band_map=BandMap(alert=1, date=2))
    p.coverage = {**p.coverage, "mine": _cov(first=date(2000, 1, 1), last=date(2100, 1, 1))}
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(_app_state(p), map_, object(), layers)
    try:
        bus = get_current_bus()
        _act(_row(table_calls, "mine"), "map_toggle")["on_click"]()
        assert layers.on.value == frozenset() and map_.layers == {}
        assert _toasts(bus, "info", t("steps.alerts.file_preview_after_harmonize")) == 1
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Details (spec §2): Add to new periods, and Edit for a custom source
# ---------------------------------------------------------------------------
def _open_details(table_calls, name):
    _act(_row(table_calls, name), "info")["on_click"]()


def test_details_hold_the_add_to_new_periods_checkbox(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, _, _, details = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    state = _app_state(p)
    box, rc = _render(state)
    try:
        _open_details(table_calls, "RADD")
        assert details["open"] and details["new_periods"] is True and details["on_edit"] is None  # predefined
        details["on_new_periods"](False)
        assert "RADD" not in state.project.value.new_period_sources
        assert details["new_periods"] is False
        assert "RADD" in state.project.value.periods[PER.id].sources  # this period keeps it
    finally:
        rc.close()


def _edit(table_calls, details, name):
    _open_details(table_calls, name)
    details["on_edit"]()


def test_edit_is_refused_once_a_set_used_the_source(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, _, modal, details = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    _used_by_a_set(p)
    state = _app_state(p)

    async def main():
        box, rc = _render(state)
        try:
            bus = get_current_bus()
            _edit(table_calls, details, "mine")
            assert modal["editing"] is None  # the modal is not opened for an edit
            assert modal["open_"].value is False
            warnings = [x.message for x in bus.toasts.value if x.type.value == "warning"]
            assert any("add a new one instead" in m for m in warnings), warnings
        finally:
            rc.close()

    asyncio.run(main())


def test_edit_and_its_save_wait_for_the_projects_writers(monkeypatch, tmp_path):
    """Spec §5.1: editing is refused while a job writes into the project, when
    the modal would open and at Save (a job can start while it is open)."""
    from gui.store.project_writers import writing

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, _, modal, details = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    state = _app_state(p)

    async def main():
        box, rc = _render(state)
        try:
            bus = get_current_bus()
            with writing(p.name):
                _edit(table_calls, details, "mine")
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
    table_calls, _, modal, details = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)

    async def main():
        box, rc = _render(state, map_, object(), layers)
        try:
            _edit(table_calls, details, "mine")
            assert modal["editing"] == "mine" and modal["open_"].value is True
            assert details["open"] is False  # the details close for the edit
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


def test_save_is_refused_once_a_set_used_the_source_meanwhile(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, _, modal, details = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    state = _app_state(p)

    async def main():
        box, rc = _render(state)
        try:
            _edit(table_calls, details, "mine")
            state.update_project(p.name, _used_by_a_set)  # an Integrate ran while the modal was open
            with pytest.raises(ValueError, match=t("steps.alerts.edit_used")):
                modal["on_save_custom"]("mine", {"name": "mine", "ref": "users/x/b"}, _cov())
            assert state.project.value.sources["mine"].ref == "users/x/a"
        finally:
            rc.close()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Add (spec §2, §3): the active period's cap, project sources, Add to new periods
# ---------------------------------------------------------------------------
def test_add_custom_saves_the_source_with_its_coverage_and_raises_translated_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _, _, modal, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    state = _app_state(p)
    fields = dict(name="mine", origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                  date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"),
                  native=NativeGrid(crs="EPSG:4326", scale=10.0))

    async def main():
        box, rc = _render(state)
        try:
            cov = _cov(fraction=0.4)
            modal["on_add_custom"](fields, cov)
            live = state.project.value
            assert live.sources["mine"].digit == 3 and live.coverage["mine"] == cov
            assert "mine" in live.periods[PER.id].sources and "mine" in live.new_period_sources
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


def test_add_dialog_offers_sources_not_in_the_period(monkeypatch, tmp_path):
    """RADD is a project source the period does not use: listed, and picking
    it adds it to the period without a new definition."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _, _, modal, _ = _stub_children(monkeypatch)
    p = _project_with_source("GLAD-L")
    add_predefined_source(p, "RADD")  # in the project, not in the period
    p.coverage = {**p.coverage, "RADD": _cov()}
    state = _app_state(p)
    assert [i["value"] for i in source_modal.predefined_items(p, pending=False)] == ["RADD", "GLAD-S2"]
    box, rc = _render(state)
    try:
        before = state.project.value.sources["RADD"]
        modal["on_add_predefined"]("RADD")
        live = state.project.value
        assert live.periods[PER.id].sources == ["GLAD-L", "RADD"] and live.sources["RADD"] is before
    finally:
        rc.close()


def test_add_to_new_periods_checkbox_defaults_on(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _, _, modal, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    state = _app_state(p)
    box, rc = _render(state)
    try:
        [add] = [b for b in rc.find(v.Btn).widgets if t("steps.alerts.add") in b.children]
        add.fire_event("click", None)
        assert modal["open_"].value is True and modal["new_periods"].value is True
        modal["on_add_predefined"]("GLAD-S2")  # the checkbox untouched
        assert "GLAD-S2" in state.project.value.new_period_sources
        modal["new_periods"].set(False)
        modal["on_add_predefined"]("GLAD-L")
        live = state.project.value
        assert "GLAD-L" not in live.new_period_sources and "GLAD-L" in live.periods[PER.id].sources
        # each opening's default is the form's (source_modal.new_periods_default)
    finally:
        rc.close()


def test_a_project_custom_source_is_offered_disabled_once_the_period_has_six():
    p = _project_with_source("RADD")
    for i in range(6):
        _add_mine(p, name=f"c{i}")
    _add_mine(p, name="spare", period_id=None)  # in the project only
    items = {i["value"]: i for i in source_modal.predefined_items(p, pending=False)}
    assert items["spare"]["disabled"] and source_modal.custom_in_use(p) == 6
    set_period_source(p, PER.id, "c0", False)
    items = {i["value"]: i for i in source_modal.predefined_items(p, pending=False)}
    assert not items["spare"]["disabled"] and not items["c0"]["disabled"]


# ---------------------------------------------------------------------------
# Remove from this period (spec §2, review focus 2)
# ---------------------------------------------------------------------------
def _with_period_files(p, name):
    pdir = paths.project_dir(p.name)
    rels = []
    for key, kind in ((raw_key(PER.id, name), "raw_alert"), (harm_key(PER.id, name), "harmonized")):
        rel = f"periods/x/{kind}/{name}.tif"
        (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
        (pdir / rel).write_bytes(b"x")
        p.artifacts[key] = Artifact(key=key, kind=kind, rel_path=rel, fingerprint="f", inputs={}, content_id=kind,
                                    created_at=datetime.now())
        rels.append(pdir / rel)
    return rels


def test_remove_from_last_period_keeps_the_definition(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _sync_spawn(monkeypatch)
    table_calls, confirm_calls, _, _ = _stub_children(monkeypatch)
    p = _project_with_source("GLAD-L")
    files = _with_period_files(p, "GLAD-L")
    state = _app_state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)
    box, rc = _render(state, map_, object(), layers)
    try:
        layers.show("src:GLAD-L", lambda: map_.add_layer("x", key="src:GLAD-L"))
        _act(_row(table_calls, "GLAD-L"), "delete")["on_click"]()
        confirm = _remove_confirm(confirm_calls)
        assert confirm["open"] and confirm["note"] is None
        assert confirm["title"] == t("steps.alerts.remove_from_period_title", name=source_label(p.sources["GLAD-L"]))
        confirm["on_confirm"]()
        live = state.project.value
        assert "GLAD-L" not in live.periods[PER.id].sources and "GLAD-L" in live.sources
        assert live.artifacts == {} and not any(f.exists() for f in files)
        assert layers.on.value == frozenset() and "src:GLAD-L" in map_.removed
        assert _remove_confirm(confirm_calls)["open"] is False
    finally:
        rc.close()


def test_remove_used_source_warns_and_keeps_set(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, confirm_calls, _, _ = _stub_children(monkeypatch)
    p = _project_with_source("RADD")
    _add_mine(p)
    _with_period_files(p, "mine")
    _used_by_a_set(p)
    _used_by_a_set_other = AlertSet(id="y__mine", period_id="other", sources=["mine"], digit_map={3: "mine"},
                                    params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                    footprint_rel_path="f", fingerprint="f", created_at=datetime.now())
    p.alert_sets = {**p.alert_sets, "y__mine": _used_by_a_set_other}  # another period's: not named
    p.coverage = {**p.coverage, "mine": _cov()}
    state = _app_state(p)
    box, rc = _render(state)
    try:
        _act(_row(table_calls, "mine"), "delete")["on_click"]()
        confirm = _remove_confirm(confirm_calls)
        assert confirm["note"] == tn("steps.alerts.delete_used", 1)
        confirm["on_confirm"]()
        live = state.project.value
        assert "x__mine" in live.alert_sets and "mine" in live.sources and "mine" in live.coverage
        assert "mine" not in live.periods[PER.id].sources and "RADD" in live.periods[PER.id].sources
    finally:
        rc.close()


def test_remove_is_refused_while_a_writer_runs(monkeypatch, tmp_path):
    """Ruling (c): the ``is_writing`` refusal is deliberately wider than "a
    download job" -- an Integrate reading the source's harmonized files must
    not lose them either."""
    from gui.store.project_writers import writing

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    table_calls, confirm_calls, _, _ = _stub_children(monkeypatch)
    p = _project_with_source()
    state = _app_state(p)
    box, rc = _render(state)
    try:
        _act(_row(table_calls, "RADD"), "delete")["on_click"]()
        bus = get_current_bus()
        with writing(p.name):
            _remove_confirm(confirm_calls)["on_confirm"]()
        assert "RADD" in state.project.value.periods[PER.id].sources
        assert _toasts(bus, "warning", t("steps.alerts.delete_busy")) == 1
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Downloads (spec §2): the rows' pills, Download again, the footer. Jobs are
# queued, not started (tests/test_prepare_step.py's ``step`` fixture).
# ---------------------------------------------------------------------------
@pytest.fixture
def dl(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(table=[], jobs=[], started=[])

    @solara.component
    def _RecorderTable(title, columns, rows, empty_text):
        ns.table.append(rows)

    monkeypatch.setattr(alerts_step, "ProductTable", _RecorderTable)
    monkeypatch.setattr(prepare_scope, "spawn_in_context", lambda target, args=(), **kw: ns.jobs.append(target))
    monkeypatch.setattr(prepare_scope, "run_rows",
                        lambda ctx, items, force=False: ns.started.append((ctx, _named(items), force)))

    def plan(*rows):
        for module in (alerts_step, prepare_scope):  # the table's rows, and the start's
            monkeypatch.setattr(module, "plan_prepare", lambda p, pdir, period: list(rows))

    def run_jobs():
        while ns.jobs:
            ns.jobs.pop(0)()

    ns.plan, ns.run_jobs = plan, run_jobs
    return ns


def _dl_render(state, jobs=None):
    return _render(state, jobs=jobs)


def _info_toasts(kind):
    return [x.message for x in get_current_bus().toasts.value if x.type.value == kind]


def _pill(row):
    """Click a row's Download (Read file) pill."""
    assert row["cells"][1]["type"] == "verb" and not row["cells"][1].get("disabled"), row["cells"][1]
    row["cells"][1]["on_click"]()


def _offers_run(row) -> bool:
    return row["cells"][1]["type"] == "verb"


def _refresh(rc, row):
    """Click a row's Download again and confirm it."""
    _act(row, "refresh")["on_click"]()
    _click(_confirm(rc, t("steps.prepare.redownload_title", source=row["cells"][0]["value"])),
           t("steps.prepare.redownload_label"))


def _footer_item(rc):
    """The footer caret's one item (Download all again), opened."""
    [caret] = [b for b in rc.find(v.Btn).widgets if "defo-split-caret" in (b.class_ or "")]
    caret.click()
    label = tn("steps.prepare.redownload_all", 1)
    [item] = [b for b in rc.find(v.Btn).widgets if any(str(c).startswith(label.split(" (")[0]) for c in b.children)]
    return item


def test_a_row_downloads_in_download_mode(dl, monkeypatch):
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("RADD", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "RADD", bounds=PARAGUAY, grid=True), meta={"RADD": {}})
    box, rc = _dl_render(state)
    try:
        _pill(dl.table[-1][0])
        dl.run_jobs()
        [(ctx, sources, force)] = dl.started
        assert sources == ["RADD"] and force is False and ctx.mode == "download"
    finally:
        rc.close()


def test_a_download_waits_for_the_catalogue_metadata(dl, monkeypatch):
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _dl_render(state)
    try:
        _pill(dl.table[-1][0])  # downloads, so it needs the metadata
        assert dl.jobs == [] and _info_toasts("info") == [t("steps.alerts.meta_loading")]

        state.catalogue_meta.value = {"GLAD-L": {"end": "2026-06-01"}}
        _pill(dl.table[-1][0])  # the refused claim was released
        dl.run_jobs()
        [(ctx, sources, force)] = dl.started
        assert sources == ["GLAD-L"] and force is False
        assert ctx.aoi == "AOI" and ctx.meta == {"GLAD-L": {"end": "2026-06-01"}}
    finally:
        rc.close()


def test_a_download_without_an_aoi_geometry_is_refused(dl, monkeypatch):
    def no_aoi(result, pdir):
        raise ProjectRuleError(key="steps.aoi.no_geometry")

    monkeypatch.setattr(aoi_io_mod, "ee_aoi", no_aoi)
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _dl_render(state)
    try:
        _pill(dl.table[-1][0])
        assert dl.jobs == [] and _info_toasts("error") == [t("steps.aoi.no_geometry")]
        assert _offers_run(dl.table[-1][0])  # the claim was released
    finally:
        rc.close()


@pytest.mark.parametrize("error", [OSError("aoi.geojson: permission denied"), RuntimeError("")],
                         ids=["OSError", "empty-message"])
def test_a_download_whose_ee_aoi_fails_releases_its_rows(dl, monkeypatch, error):
    """Any failure building the Earth Engine AOI, not only a missing geometry,
    must give the claimed keys back: otherwise the rows read "running" for the
    rest of the session."""
    def bad_aoi(result, pdir):
        raise error

    monkeypatch.setattr(aoi_io_mod, "ee_aoi", bad_aoi)
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})
    jobs = prepare_scope.PrepareJobs()

    box, rc = _dl_render(state, jobs=jobs)
    try:
        _pill(dl.table[-1][0])
        assert dl.jobs == [] and _info_toasts("error") == [str(error) or error.__class__.__name__]
        assert _offers_run(dl.table[-1][0])  # the claim was released
        assert not _stop_shown(rc) and jobs.inflight.value == frozenset()
    finally:
        rc.close()


def test_a_geotiff_row_needs_no_ee_aoi_and_claims_only_its_row(dl, monkeypatch, tmp_path):
    """A GeoTIFF row is read locally (no Earth Engine AOI, no catalogue
    metadata) from the AOI bounds alone: its download never builds the AOI
    raster, so it claims only its own key (only Harmonize claims the
    project's AOI-raster key)."""
    def no_aoi(result, pdir):
        pytest.fail("a GeoTIFF row must not build the Earth Engine AOI")

    claims = []

    class _Recording(prepare_scope.InflightKeys):
        def claim(self, *keys, **kw):
            claims.append(keys)
            return super().claim(*keys, **kw)

    monkeypatch.setattr(aoi_io_mod, "ee_aoi", no_aoi)
    monkeypatch.setattr(prepare_scope, "InflightKeys", _Recording)
    dl.plan(PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=True))

    box, rc = _dl_render(state, jobs=prepare_scope.PrepareJobs())
    try:
        [row] = dl.table[-1]
        assert row["cells"][0]["value"] == "tif"  # a custom source by its name
        assert row["cells"][1]["label"] == t("steps.alerts.read_file")
        _pill(row)
        dl.run_jobs()
        [(ctx, sources, force)] = dl.started
        assert sources == ["tif"] and not force and ctx.aoi is None
        assert claims == [(alerts_step.inflight_key("d", JAN.id, "tif"),)]
        assert _info_toasts("info") == []
    finally:
        rc.close()


def _check_again_shown(rc):
    return bool([w for w in rc.find(v.Btn).widgets if t("coverage.check_again") in w.children])


def test_a_geotiff_row_does_not_wait_for_the_aoi_geometry(dl, tmp_path):
    dl.plan(PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=False))
    state.sidecar_pending.set(True)  # the loader is fetching it

    box, rc = _dl_render(state)
    try:
        _pill(dl.table[-1][0])
        dl.run_jobs()
        [(ctx, sources, _)] = dl.started
        assert sources == ["tif"] and ctx.mode == "download" and _info_toasts("info") == []
    finally:
        rc.close()


def test_a_failed_aoi_sidecar_holds_back_no_download_and_offers_no_check_again(dl, monkeypatch, tmp_path):
    """The AOI sidecar only matters to Harmonize: a Download all runs the
    Earth Engine and the GeoTIFF rows alike, and the Alerts step offers no
    Check again for it."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=False), meta={"GLAD-L": {}})

    box, rc = _dl_render(state)
    try:
        assert not _check_again_shown(rc)
        _button(rc, tn("steps.alerts.download_all", 2)).fire_event("click", None)
        dl.run_jobs()
        [(ctx, sources, _)] = dl.started
        assert sources == ["GLAD-L", "tif"] and ctx.aoi == "AOI" and ctx.mode == "download"
        assert _info_toasts("info") == []
    finally:
        rc.close()


def test_download_all_with_a_geotiff_row_is_not_refused_while_a_harmonize_builds_the_aoi_raster(
        dl, monkeypatch, tmp_path):
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=True), meta={"GLAD-L": {}})
    jobs = prepare_scope.PrepareJobs()
    assert jobs.inflight.claim(prepare_scope.aoi_key("d"))  # a harmonize job is building the AOI raster

    box, rc = _dl_render(state, jobs=jobs)
    try:
        _button(rc, tn("steps.alerts.download_all", 2)).fire_event("click", None)
        dl.run_jobs()
        [(ctx, sources, _)] = dl.started
        assert sources == ["GLAD-L", "tif"] and ctx.mode == "download"
        assert _info_toasts("info") == []
    finally:
        rc.close()


def test_download_all_runs_the_pending_rows_only(dl, monkeypatch):
    """The footer's Download N pending: raw missing or out of date, not blocked."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    blocked = PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                         (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),))
    dl.plan(PrepareRow("GLAD-L", Status.STALE, Status.STALE), PrepareRow("RADD", Status.CURRENT, Status.MISSING),
            blocked)
    state = _state(_project("d", "GLAD-L", "RADD", "GLAD-S2", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})
    box, rc = _dl_render(state)
    try:
        _button(rc, tn("steps.alerts.download_all", 1)).fire_event("click", None)
        dl.run_jobs()
        assert [(items, force) for _, items, force in dl.started] == [(["GLAD-L"], False)]
    finally:
        rc.close()


def test_download_all_skips_a_row_a_harmonize_job_holds_and_its_stop_is_its_own(dl, monkeypatch):
    """Option C: a harmonize job holding a row neither disables Download all
    nor makes its claim refuse the batch; that row reads its download state
    (never Harmonizing or Queued), and the footer's Stop stops only the
    downloads."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.STALE, Status.STALE), PrepareRow("RADD", Status.STALE, Status.STALE))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})
    jobs = prepare_scope.PrepareJobs()
    box, rc = _dl_render(state, jobs=jobs)
    try:
        prepare_scope.start_items(jobs, app_state=state, layers=None, map_=None,
                                  notifications=Notifier(get_current_bus()), items=[("source", "RADD")],
                                  mode="harmonize")
        radd = dl.table[-1][1]
        assert radd["cells"][1]["type"] == "verb" and radd["cells"][1]["disabled"]  # held, not Queued
        assert _act(radd, "refresh")["disabled"]
        assert not _stop_shown(rc)  # the harmonize is the Harmonize step's job
        download_all = _button(rc, tn("steps.alerts.download_all", 1))
        assert not download_all.disabled
        download_all.fire_event("click", None)
        assert _info_toasts("info") == []
        _button(rc, t("steps.prepare.footer_stop")).fire_event("click", None)
        dl.run_jobs()
        cancelled = {(ctx.mode, *items): ctx.cancel.is_set() for ctx, items, _ in dl.started}
        assert cancelled == {("harmonize", "RADD"): False, ("download", "GLAD-L"): True}
    finally:
        rc.close()


def test_download_all_again_is_not_held_back_by_pending_masks(dl, monkeypatch):
    """A download no longer waits for the period's masks (spec §2)."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_with_mask(), meta={"GLAD-L": {}})
    box, rc = _dl_render(state)
    try:
        item = _footer_item(rc)
        assert tn("steps.prepare.redownload_all", 1) in item.children and not item.disabled
        assert not _act(dl.table[-1][0], "refresh")["disabled"]
        _refresh(rc, dl.table[-1][0])
        dl.run_jobs()
        assert [(items, force) for _, items, force in dl.started] == [(["GLAD-L"], True)]  # no mask first
    finally:
        rc.close()


def test_download_all_again_forces_the_sources_that_have_something_to_fetch(dl, monkeypatch, tmp_path):
    """Every catalogue / Earth Engine source that is not blocked, in one forced job."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    p = _with_tif(tmp_path, sidecar=True)
    add_predefined_source(p, "RADD")
    add_predefined_source(p, "GLAD-S2")
    dl.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), PrepareRow("tif", Status.CURRENT, Status.CURRENT),
            PrepareRow("RADD", Status.CURRENT, Status.CURRENT),
            PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                       (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)))
    state = _state(p, meta={"GLAD-L": {}})

    box, rc = _dl_render(state)
    try:
        item = _footer_item(rc)
        assert not item.disabled
        item.fire_event("click", None)  # asks first
        assert dl.jobs == []
        names = ", ".join(source_label(p.sources[s]) for s in ("GLAD-L", "RADD"))
        dialog = _confirm(rc, tn("steps.prepare.redownload_all_title", 2))
        assert t("steps.prepare.redownload_all_message", names=names) in _texts(dialog)
        _click(dialog, t("steps.prepare.redownload_label"))
        dl.run_jobs()
        [(ctx, items, force)] = dl.started
        assert items == ["GLAD-L", "RADD"] and force and ctx.mode == "download"
        assert ctx.downloads == {("source", "GLAD-L"), ("source", "RADD")}
    finally:
        rc.close()


def test_download_again_asks_then_forces(dl, monkeypatch):
    """Spec "Confirms": the window and the date of the file it replaces; a
    source never downloaded reads the short message. Only that source runs."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), PrepareRow("RADD", Status.MISSING, Status.MISSING))
    p = _project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True)
    p.periods = {JAN.id: JAN.model_copy(update={"look_back_days": 10})}
    p.artifacts.update(_arts("GLAD-L"))
    downloaded = p.artifacts[raw_key(JAN.id, "GLAD-L")].created_at.date().isoformat()
    state = _state(p, meta={"GLAD-L": {}})
    window = t("steps.period.range", start="2025-12-22", end="2026-01-31")

    box, rc = _dl_render(state)
    try:
        glad, radd = dl.table[-1]
        _act(radd, "refresh")["on_click"]()
        title = t("steps.prepare.redownload_title", source=radd["cells"][0]["value"])
        assert t("steps.prepare.redownload_message_new", window=window) in _texts(_confirm(rc, title))
        _click(_confirm(rc, title), t("common.cancel"))
        assert _closed(rc, title) and dl.jobs == []

        _act(glad, "refresh")["on_click"]()
        title = t("steps.prepare.redownload_title", source=glad["cells"][0]["value"])
        dialog = _confirm(rc, title)
        assert t("steps.prepare.redownload_message", window=window, date=downloaded) in _texts(dialog)
        assert dl.jobs == []  # nothing runs before the confirm
        _click(dialog, t("steps.prepare.redownload_label"))
        assert _closed(rc, title)
        dl.run_jobs()
        [(ctx, items, force)] = dl.started
        assert items == ["GLAD-L"] and force is True
    finally:
        rc.close()


def test_download_again_waits_for_the_catalogue_metadata(dl, monkeypatch):
    """A forced download needs the metadata too (Task 3's carried case)."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _dl_render(state)
    try:
        assert not _act(dl.table[-1][0], "refresh")["disabled"]  # a downloaded row can still be downloaded again
        _refresh(rc, dl.table[-1][0])
        assert dl.jobs == [] and _info_toasts("info") == [t("steps.alerts.meta_loading")]

        state.catalogue_meta.value = {"GLAD-L": {"end": "2026-06-01"}}
        _refresh(rc, dl.table[-1][0])  # the refused claim was released
        dl.run_jobs()
        [(ctx, sources, force)] = dl.started
        assert sources == ["GLAD-L"] and force is True
        assert ctx.aoi == "AOI" and ctx.meta == {"GLAD-L": {"end": "2026-06-01"}}
    finally:
        rc.close()


def test_download_again_during_a_download_all_keeps_stop(dl, monkeypatch):
    """A Download again started while Download all is in its current row joins
    the scope: the footer's Stop (``stop_scope``) stops both."""
    monkeypatch.setattr(aoi_io_mod, "ee_aoi", lambda result, pdir: "AOI")
    dl.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("RADD", Status.CURRENT, Status.CURRENT))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}, "RADD": {}})
    contexts = {}

    box, rc = _dl_render(state)

    def download_all_blocked_mid_row(ctx, items, force=False):
        sources = _named(items)
        contexts[tuple(sources)] = (ctx, force)
        if sources == ["GLAD-L"]:  # Download all, still in its current row
            _refresh(rc, dl.table[-1][1])  # Download again the downloaded RADD row
            _button(rc, t("steps.prepare.footer_stop")).fire_event("click", None)

    monkeypatch.setattr(prepare_scope, "run_rows", download_all_blocked_mid_row)
    try:
        _button(rc, tn("steps.alerts.download_all", 1)).fire_event("click", None)
        dl.run_jobs()
        assert set(contexts) == {("GLAD-L",), ("RADD",)} and contexts[("RADD",)][1] is True
        assert contexts[("GLAD-L",)][0].cancel.is_set()  # Download all stops after this row
        assert contexts[("RADD",)][0].cancel.is_set()  # and so does the Download again
        assert not _stop_shown(rc)
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
