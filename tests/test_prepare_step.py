"""Download & harmonize step (spec §7.3): the grid form, the row worker and
the step itself.

``run_rows`` is driven with a fake ``run_source_row`` (the engine has its own
tests); the step is rendered with ``reacton.render`` against a real
``AppState``/``Project``, with the table swapped for a recorder so the test
calls the exact callables the step wires up (tests/test_alerts_step.py's
pattern).
"""

import threading
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.notifications import NoopNotifier, NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.engine.fingerprint import Status, harm_key, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import (
    AoiRecord,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from gui.i18n import t
from gui.scripts import aoi_io, catalogue_meta, prepare_jobs, raster_map
from gui.scripts.map_layers import MapLayers
from gui.store.project_writers import is_writing
from gui.store.state_manager import AppState
from gui.tile import prepare_step
from gui.widget.grid_form import grid_error_key

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
PARAGUAY = (-55.5, -24.5, -55.0, -24.0)  # UTM 21S
BRAZIL = (-50.5, -10.5, -50.0, -10.0)  # UTM 22S


def _project(name="d", *sources, bounds=None, grid=False):
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    for s in sources:
        add_predefined_source(p, s)
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    if bounds is not None:
        p.aoi = AoiRecord(spec={}, name=name, method="DRAW", digest=f"digest-{name}", bounds=bounds)
        if grid:
            p.grid = gridmod.build_grid(bounds, gridmod.auto_utm_epsg(bounds), 30.0)
    return p


def _art(key, kind):
    return Artifact(key=key, kind=kind, rel_path=f"periods/{key.replace(':', '_')}.tif", fingerprint="f",
                    inputs={}, content_id=uuid.uuid4().hex, created_at=datetime.now())


def _arts(source):
    rkey, hkey = raw_key(JAN.id, source), harm_key(JAN.id, source)
    return {rkey: _art(rkey, "raw_alert"), hkey: _art(hkey, "harmonized")}


# ---------------------------------------------------------------------------
# grid_error_key, row_cells
# ---------------------------------------------------------------------------
def test_grid_error_keys():
    assert grid_error_key("4326", 10) == "steps.prepare.grid_geographic"
    assert grid_error_key("32721", 10) is None


def test_row_cells_show_both_stages():
    cells = prepare_jobs.row_cells(PrepareRow("RADD", Status.CURRENT, Status.STALE), running=False)
    assert cells[0]["value"] == "RADD"
    assert [c.get("status") for c in cells[1:3]] == ["ready", "stale"]
    running = prepare_jobs.row_cells(PrepareRow("RADD", Status.MISSING, Status.MISSING), running=True)
    assert running[1]["status"] == "running"


# ---------------------------------------------------------------------------
# run_rows (the worker body)
# ---------------------------------------------------------------------------
def _ctx(app_state, cancel=None, **overrides):
    fields = dict(
        app_state=app_state, layers=SimpleNamespace(refresh=lambda *a, **k: None, is_on=lambda k: False),
        map_=None, notifier=None, name="d", pdir=None, period=JAN, meta={}, aoi="AOI",
        cancel=cancel or threading.Event(),
    )
    fields.update(overrides)
    return prepare_jobs.PrepareContext(**fields)


def test_run_rows_applies_artifacts_and_stops_on_project_switch(monkeypatch):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", "GLAD-S2"))
    other = _project("other", "GLAD-L", "RADD", "GLAD-S2")
    ran, seen = [], {}

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        if job.source == "RADD":
            seen["d"] = s.project.value  # "d" as the user leaves it
            s.new_project_state(other)  # the user opens another project mid-row
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s), ["GLAD-L", "RADD", "GLAD-S2"])

    assert ran == ["GLAD-L", "RADD"]  # GLAD-S2 never started: the project is gone
    d = seen["d"]
    assert raw_key(JAN.id, "GLAD-L") in d.artifacts and harm_key(JAN.id, "GLAD-L") in d.artifacts
    assert raw_key(JAN.id, "RADD") not in d.artifacts  # its result was dropped, not applied late
    assert s.project.value is other and other.artifacts == {}  # nothing landed in the new project


def test_run_rows_stops_when_the_project_is_closed(monkeypatch):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD"))
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.close_project_state()  # the user closes the project mid-row
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"] and s.project.value is None


@pytest.mark.parametrize("change", ["aoi", "period"])
def test_run_rows_stops_when_the_aoi_or_the_period_changes(monkeypatch, change):
    """The EE AOI and the period are fixed when the run starts: a row started
    after either changed would record fingerprints of inputs it did not use.
    """
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY))
    ran = []

    def _change(live):
        if change == "aoi":
            live.aoi = live.aoi.model_copy(update={"digest": "another-geometry"})
        else:
            live.periods = {}
            live.active_period = None

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.update_project("d", _change)
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"]


def test_a_failed_row_does_not_stop_the_next(monkeypatch):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD"))
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        if job.source == "GLAD-L":
            raise RuntimeError("quota exceeded")
        return {}

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.errors = []

        def error(self, message, *, timeout=None):
            self.errors.append(message)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    errors = solara.reactive({})
    ctx = _ctx(s, row_errors=errors, notifier=_Notifier())
    prepare_jobs.run_rows(ctx, ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L", "RADD"] and errors.value == {"GLAD-L": "quota exceeded"}
    assert ctx.notifier.errors == [t("steps.prepare.row_failed", source="GLAD-L", error="quota exceeded")]


FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _switch_period(live):
    live.periods = {FEB.id: FEB}
    live.active_period = FEB.id


@pytest.mark.parametrize("change", ["project", "period"])
def test_a_row_that_fails_after_the_step_moved_on_leaves_no_row_error(monkeypatch, change):
    """The step lists the open project's active period: a late failure of a
    row it no longer shows must not appear under the row now shown.
    """
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L"))

    def fake_run(job, on_tiles=None, on_wait=None):
        if change == "project":
            s.new_project_state(_project("other", "GLAD-L"))
        else:
            s.update_project("d", _switch_period)
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    errors = solara.reactive({})
    prepare_jobs.run_rows(_ctx(s, row_errors=errors), ["GLAD-L"])
    assert errors.value == {}


def test_run_rows_honours_cancel(monkeypatch):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD"))
    cancel = threading.Event()
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        cancel.set()
        return {}

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, cancel), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"]


def test_run_rows_skips_a_source_removed_meanwhile(monkeypatch):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", "GLAD-S2"))
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.update_project("d", lambda live: live.sources.pop("RADD"))
        return {}

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s), ["GLAD-L", "RADD", "GLAD-S2"])
    assert ran == ["GLAD-L", "GLAD-S2"]


def test_run_rows_marks_the_folder_busy_and_redraws_a_shown_source(monkeypatch, tmp_path):
    """The delete dialog refuses while the mark is set (spec §12), and a
    source already on the map is redrawn from its new harmonized file
    (spec §6.2, §9.1).
    """
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD"))
    busy, refreshed, drawn = [], [], []
    arts = {"GLAD-L": _arts("GLAD-L"), "RADD": _arts("RADD")}

    def fake_run(job, on_tiles=None, on_wait=None):
        busy.append(is_writing("d"))
        return arts[job.source]

    def refresh(key, build, content_id):
        refreshed.append((key, content_id))
        build()

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    monkeypatch.setattr(raster_map, "add_raster_layer",
                        lambda map_, path, *, key, name: drawn.append((map_, path, key, name)))
    layers = SimpleNamespace(is_on=lambda k: k == "src:RADD", refresh=refresh)
    prepare_jobs.run_rows(_ctx(s, layers=layers, map_="MAP", pdir=tmp_path), ["GLAD-L", "RADD"])

    assert busy == [True, True] and not is_writing("d")
    harm = arts["RADD"][harm_key(JAN.id, "RADD")]
    assert refreshed == [("src:RADD", harm.content_id)]  # GLAD-L is not on the map
    assert drawn == [("MAP", paths.resolve(tmp_path, harm.rel_path), "src:RADD", "RADD")]


# ---------------------------------------------------------------------------
# PrepareStep / GridForm: render-based coverage
# ---------------------------------------------------------------------------
class _FakeMap:
    def __init__(self):
        self.layers = {}

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def remove_layer(self, key, none_ok=False):
        self.layers.pop(key, None)


@solara.component
def _Harness(app_state, map_, gee_interface, layers):
    NotificationProvider()  # the step's use_notifications() needs it rendered first
    prepare_step.PrepareStep(app_state=app_state, map_=map_, gee_interface=gee_interface, layers=layers)


def _render(state, layers=None, map_=None):
    map_ = map_ or _FakeMap()
    layers = layers or MapLayers(map_)
    return reacton.render(_Harness(app_state=state, map_=map_, gee_interface=object(), layers=layers),
                          handle_error=False)


@pytest.fixture
def step(monkeypatch, tmp_path):
    """No Earth Engine: the catalogue hook returns the cache as is."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(catalogue_meta, "use_catalogue_meta",
                        lambda app_state, gee_interface: app_state.catalogue_meta.value)
    ns = SimpleNamespace(table=[], jobs=[], started=[])

    @solara.component
    def _RecorderTable(title, columns, rows, empty_text):
        ns.table.append(rows)

    monkeypatch.setattr(prepare_step, "ProductTable", _RecorderTable)
    # Jobs are queued, not started: the test runs them (``run_jobs``).
    monkeypatch.setattr(prepare_step, "spawn_in_context", lambda target, args=(), **kw: ns.jobs.append(target))
    monkeypatch.setattr(prepare_step, "run_rows",
                        lambda ctx, sources, force=False: ns.started.append((ctx, list(sources), force)))

    def plan(*rows):
        monkeypatch.setattr(prepare_step, "plan_prepare", lambda p, pdir, period: list(rows))

    def run_jobs():
        while ns.jobs:
            ns.jobs.pop(0)()

    ns.plan, ns.run_jobs = plan, run_jobs
    return ns


def _field(rc, label):
    return next(w for w in rc.find(v.TextField).widgets if w.label == label)


def _button(rc, label):
    return next(w for w in rc.find(v.Btn).widgets if label in w.children)


def _toasts(kind):
    return [toast.message for toast in get_current_bus().toasts.value if toast.type.value == kind]


def _state(p, meta=None):
    state = AppState()
    state.new_project_state(p)
    state.catalogue_meta.value = meta if meta is not None else {}
    return state


def test_a_grid_change_reharmonizes_without_catalogue_metadata_or_an_ee_aoi(step, monkeypatch):
    """Drift note: only rows that download need Earth Engine metadata; a grid
    change re-harmonizes from the raw file on disk (runner contract).
    """
    def no_aoi(result, pdir):  # e.g. a reopened admin AOI not replayed yet, and no sidecar
        raise ValueError(t("steps.aoi.no_geometry"))

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        _button(rc, t("steps.prepare.run_all", n=1)).fire_event("click", None)
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["GLAD-L"] and force is False
        assert ctx.aoi is None and ctx.meta == {} and ctx.name == "d" and ctx.period == JAN
        assert _toasts("info") == [] and _toasts("error") == []
    finally:
        rc.close()


def test_a_download_waits_for_the_catalogue_metadata(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        redownload = step.table[-1][0]["actions"][1]["on_click"]
        redownload()  # force: downloads, so it needs the metadata
        assert step.jobs == [] and _toasts("info") == [t("steps.alerts.meta_loading")]

        state.catalogue_meta.value = {"GLAD-L": {"end": "2026-06-01"}}
        step.table[-1][0]["actions"][1]["on_click"]()  # the refused claim was released
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["GLAD-L"] and force is True
        assert ctx.aoi == "AOI" and ctx.meta == {"GLAD-L": {"end": "2026-06-01"}}
    finally:
        rc.close()


def test_a_download_without_an_aoi_geometry_is_refused(step, monkeypatch):
    def no_aoi(result, pdir):
        raise ValueError(t("steps.aoi.no_geometry"))

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        step.table[-1][0]["actions"][0]["on_click"]()
        assert step.jobs == [] and _toasts("error") == [t("steps.aoi.no_geometry")]
        assert step.table[-1][0]["cells"][1]["status"] == "missing"  # the claim was released
    finally:
        rc.close()


def test_another_projects_job_neither_shows_as_running_nor_blocks_this_one(step):
    """Review focus 5 at the UI level: a job's in-flight keys belong to the
    project that started it.
    """
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_project("a", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        step.table[-1][0]["actions"][0]["on_click"]()
        step.jobs.pop(0)  # a's worker is still running...
        step.table[-1][0]["actions"][0]["on_click"]()
        assert step.jobs == []  # ...so a second click is a no-op
        assert step.table[-1][0]["cells"][1]["status"] == "running"
        assert _button(rc, t("steps.prepare.run_all", n=1)).disabled

        state.new_project_state(_project("b", "GLAD-L", bounds=PARAGUAY, grid=True))
        assert step.table[-1][0]["cells"][1]["status"] == "ready"
        assert not _button(rc, t("steps.prepare.run_all", n=1)).disabled
        step.table[-1][0]["actions"][0]["on_click"]()
        step.run_jobs()
        assert [ctx.name for ctx, _, _ in step.started] == ["b"]
    finally:
        rc.close()


def test_row_errors_are_cleared_when_the_project_changes(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("a", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        step.table[-1][0]["actions"][0]["on_click"]()
        step.run_jobs()
        [(ctx, _, _)] = step.started
        ctx.row_errors.set({"GLAD-L": "quota exceeded"})
        assert step.table[-1][0]["error"] == "quota exceeded"

        state.new_project_state(_project("b", "GLAD-L", bounds=PARAGUAY, grid=True))
        assert step.table[-1][0]["error"] is None
    finally:
        rc.close()


def test_map_toggle_draws_the_harmonized_file_and_shows_a_build_in_flight(step, monkeypatch):
    import gui.scripts.map_layers as ml

    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args))
    drawn = []
    monkeypatch.setattr(raster_map, "add_raster_layer",
                        lambda map_, path, *, key, name: drawn.append((path, key, name)))
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.artifacts.update(_arts("GLAD-L"))
    state = _state(p)

    box, rc = _render(state)
    try:
        toggle = step.table[-1][0]["actions"][2]
        assert toggle["kind"] == "map_toggle" and not toggle["disabled"] and not toggle["loading"]
        toggle["on_click"]()
        harm = p.artifacts[harm_key(JAN.id, "GLAD-L")]
        assert drawn == [(paths.resolve(paths.project_dir("d"), harm.rel_path), "src:GLAD-L", "GLAD-L")]
        assert step.table[-1][0]["actions"][2]["is_on"]
    finally:
        rc.close()

    busy = SimpleNamespace(on=solara.reactive(frozenset({"src:GLAD-L"})),
                           inflight=solara.reactive(frozenset({"src:GLAD-L"})))
    box, rc = _render(state, layers=busy)
    try:
        assert step.table[-1][0]["actions"][2]["loading"]
    finally:
        rc.close()


def test_grid_form_blocks_a_geographic_crs_and_saves_a_projected_one(step):
    state = _state(_project("d", "RADD", bounds=PARAGUAY))

    box, rc = _render(state)
    try:
        epsg = _field(rc, t("steps.prepare.grid_epsg"))
        assert epsg.v_model == "EPSG:32721"  # the AOI's UTM zone
        assert _field(rc, t("steps.prepare.grid_resolution")).v_model == "10"  # RADD's native size

        epsg.v_model = "4326"
        assert epsg.error_messages == [t("steps.prepare.grid_geographic")]
        assert _button(rc, t("steps.prepare.grid_save")).disabled
        assert state.project.value.grid is None

        epsg.v_model = "32721"
        _field(rc, t("steps.prepare.grid_resolution")).v_model = "30"
        assert epsg.error_messages == []
        _button(rc, t("steps.prepare.grid_save")).fire_event("click", None)

        grid = state.project.value.grid
        assert grid == gridmod.build_grid(PARAGUAY, "EPSG:32721", 30.0)
        assert _toasts("success") == [t("steps.prepare.grid_saved", w=grid.width, h=grid.height)]
    finally:
        rc.close()


def test_grid_form_follows_a_project_switch_and_an_aoi_change(step):
    """Ruling #18: the step is mounted for the whole session, so the form must
    not keep one project's typed grid (or UTM zone) for another.
    """
    state = _state(_project("a", "GLAD-L", bounds=PARAGUAY))

    box, rc = _render(state)
    try:
        _field(rc, t("steps.prepare.grid_epsg")).v_model = "3857"
        _field(rc, t("steps.prepare.grid_resolution")).v_model = "25"

        state.new_project_state(_project("b", "RADD", bounds=BRAZIL))
        assert _field(rc, t("steps.prepare.grid_epsg")).v_model == "EPSG:32722"
        assert _field(rc, t("steps.prepare.grid_resolution")).v_model == "10"

        _field(rc, t("steps.prepare.grid_epsg")).v_model = "3857"
        new_aoi = state.project.value.aoi.model_copy(update={"digest": "moved", "bounds": PARAGUAY})
        state.update_project("b", lambda live: setattr(live, "aoi", new_aoi))
        assert _field(rc, t("steps.prepare.grid_epsg")).v_model == "EPSG:32721"
    finally:
        rc.close()


def test_closing_the_project_shows_the_lock_message(step):
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_project("a", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        state.close_project_state()
        assert rc.find(v.TextField).widgets == []
        assert rc.find(v.Alert, children=[t("workflow.lock_prepare")]).widgets
    finally:
        rc.close()
