"""Download & harmonize step (spec §7.3): the grid form, the row worker and
the step itself.

``run_rows`` is driven with a fake ``run_source_row`` (the engine has its own
tests); the step is rendered with ``reacton.render`` against a real
``AppState``/``Project``, with the table swapped for a recorder so the test
calls the exact callables the step wires up (tests/test_alerts_step.py's
pattern).
"""

import asyncio
import threading
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.solara.notifications import NoopNotifier, NotificationProvider
from pysepal.solara.notifications.bus import NotificationBus, get_current_bus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus

from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.engine.coverage import Note
from deforestation_alerts.engine.fingerprint import Status, harm_key, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.engine.runner import HarmonizeFailed
from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import (
    AoiRecord,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from gui.i18n import t, tn
from gui.scripts import aoi_io, prepare_jobs, raster_map, source_layers
from gui.scripts.map_layers import MapLayers
from gui.scripts.prepare_view import source_label
from gui.store.project_writers import is_writing, writing
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
# grid_error_key
# ---------------------------------------------------------------------------
def test_grid_error_keys():
    assert grid_error_key("4326", 10) == "steps.prepare.grid_geographic"
    assert grid_error_key("32721", 10) is None


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


def test_apply_artifacts_replaces_the_shared_dict_instead_of_mutating_it():
    """The row callback runs on the worker while a render on another thread
    may iterate the same ``artifacts`` dict (every published snapshot shares
    it): the record must not change that dict's size."""
    from deforestation_alerts.engine.runner import apply_artifacts

    p = _project("d", "GLAD-L", "RADD")
    p.artifacts.update(_arts("GLAD-L"))
    snapshot = p.model_copy()
    being_iterated = iter(snapshot.artifacts.values())

    apply_artifacts(p, _arts("RADD"))

    assert len(list(being_iterated)) == 2  # no "dictionary changed size during iteration"
    assert set(snapshot.artifacts) == set(_arts("GLAD-L")) and len(p.artifacts) == 4


def test_run_rows_applies_artifacts_and_stops_on_project_switch(monkeypatch, tmp_path):
    """Review focus 5 and spec §12: nothing lands in the project open now. The
    row that finished after the switch is recorded in its own project's saved
    manifest, which must keep describing the files the row just wrote in that
    project's folder (spec §5.2, §5.3).
    """
    s = AppState()
    d = _project("d", "GLAD-L", "RADD", "GLAD-S2")
    io.save_project(d, root=tmp_path)  # "d" as last saved
    s.new_project_state(d)
    other = _project("other", "GLAD-L", "RADD", "GLAD-S2")
    ran, seen, written = [], {}, {}

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        if job.source == "RADD":
            seen["d"] = s.project.value  # "d" as the user leaves it
            s.new_project_state(other)  # the user opens another project mid-row
        written[job.source] = _arts(job.source)
        return written[job.source]

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d"), ["GLAD-L", "RADD", "GLAD-S2"])

    assert ran == ["GLAD-L", "RADD"]  # GLAD-S2 never started: the project is gone
    live = seen["d"]
    assert raw_key(JAN.id, "GLAD-L") in live.artifacts and harm_key(JAN.id, "GLAD-L") in live.artifacts
    assert raw_key(JAN.id, "RADD") not in live.artifacts  # not applied to the left project's live object
    assert s.project.value is other and other.artifacts == {}  # nothing landed in the new project
    assert not (tmp_path / "other").exists()
    saved = io.load_project("d", root=tmp_path)
    for key in (raw_key(JAN.id, "RADD"), harm_key(JAN.id, "RADD")):
        assert saved.artifacts[key] == written["RADD"][key]  # recorded in "d"'s own manifest


def test_a_download_shows_its_percent_on_its_row_and_in_the_pill(monkeypatch, tmp_path):
    """Bug 2026-10-16: a download said nothing while it ran. Its percent is
    published by row (``row_progress``) and in the task's title, and both go
    once the row ends; a finished download says the harmonize has begun."""
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L"))
    bus = NotificationBus()
    progress = solara.reactive({})
    key = prepare_jobs.progress_key("d", JAN.id, "source", "GLAD-L")
    seen = []

    def fake_run(job, on_tiles=None, on_wait=None):
        for done in (1, 2, 4):
            on_tiles(done, 4)
            task = bus.tasks.value[-1]
            seen.append((progress.value.get(key), task.title))
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d", notifier=Notifier(bus), row_progress=progress,
                               errors_lock=threading.Lock()), ["GLAD-L"])

    title = t("steps.prepare.job_title")
    assert seen == [(25, t("steps.prepare.job_progress", title=title, pct=25)),
                    (50, t("steps.prepare.job_progress", title=title, pct=50)),
                    (100, title)]  # the download is done: the pill is back on its milestones
    (task,) = bus.tasks.value
    assert task.title == title and progress.value == {}
    assert t("steps.prepare.job_harmonize_row", source="GLAD-L") in [m.message for m in task.milestones]


def test_run_rows_stops_when_the_project_is_closed(monkeypatch, tmp_path):
    s = AppState()
    d = _project("d", "GLAD-L", "RADD")
    io.save_project(d, root=tmp_path)
    s.new_project_state(d)
    ran, written = [], {}

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.close_project_state()  # the user closes the project mid-row
        written[job.source] = _arts(job.source)
        return written[job.source]

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d"), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"] and s.project.value is None
    saved = io.load_project("d", root=tmp_path)
    assert {k: saved.artifacts[k] for k in written["GLAD-L"]} == written["GLAD-L"]


def test_a_late_row_is_not_recorded_for_a_period_its_saved_project_no_longer_has(monkeypatch, tmp_path):
    """The saved manifest is the left project's own truth: a period removed
    there gets no orphan records.
    """
    s = AppState()
    saved_without_jan = _project("d", "GLAD-L")
    saved_without_jan.periods, saved_without_jan.active_period = {}, None
    io.save_project(saved_without_jan, root=tmp_path)
    s.new_project_state(_project("d", "GLAD-L"))

    def fake_run(job, on_tiles=None, on_wait=None):
        s.close_project_state()
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d"), ["GLAD-L"])
    assert io.load_project("d", root=tmp_path).artifacts == {}


def test_a_late_row_that_cannot_be_recorded_does_not_fail_the_job(monkeypatch, tmp_path, caplog):
    """No manifest to record into (never saved, or unreadable): the error is
    logged, the job ends normally, and no project is created from nothing.
    """
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L"))

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.errors = []

        def error(self, message, *, timeout=None):
            self.errors.append(message)

    def fake_run(job, on_tiles=None, on_wait=None):
        s.close_project_state()
        return _arts(job.source)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    notifier = _Notifier()
    with caplog.at_level("ERROR", logger="SEPALUI.deforestation_alerts"):
        prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d", notifier=notifier), ["GLAD-L"])
    assert notifier.errors == []  # tracked_job saw no failure
    assert any("d" in r.getMessage() and r.exc_info for r in caplog.records)
    assert not (tmp_path / "d").exists()


def _write(pdir, arts):
    for a in arts.values():
        path = pdir / a.rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return arts


def _change_scope(change):
    def _change(live):
        if change == "aoi":
            live.aoi = live.aoi.model_copy(update={"digest": "another-geometry"})
            live.grid = None
            live.artifacts = {}  # aoi_io.apply_aoi drops every record
        elif change == "grid":
            live.grid = gridmod.build_grid(PARAGUAY, gridmod.auto_utm_epsg(PARAGUAY), 10.0)
        else:
            live.periods = {}
            live.active_period = None
    return _change


@pytest.mark.parametrize("change", ["aoi", "grid", "period"])
def test_run_rows_stops_when_the_aoi_the_grid_or_the_period_changes(monkeypatch, tmp_path, change):
    """The EE AOI, the grid and the period are fixed when the run starts: a
    row started after one changed would record fingerprints of inputs it did
    not use, and the row that was running is not recorded either: its files
    are deleted, so an AOI change leaves no old-AOI file (review A I1).
    """
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True))
    ran, written = [], {}

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.update_project("d", _change_scope(change))
        written[job.source] = _write(tmp_path, _arts(job.source))
        return written[job.source]

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"]
    live = s.project.value
    assert raw_key(JAN.id, "GLAD-L") not in live.artifacts and harm_key(JAN.id, "GLAD-L") not in live.artifacts
    assert not any((tmp_path / a.rel_path).exists() for a in written["GLAD-L"].values())


def test_a_refused_row_keeps_a_file_a_live_record_still_names(monkeypatch, tmp_path):
    """Only the row's files no live record names are deleted."""
    s = AppState()
    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    kept = _write(tmp_path, _arts("GLAD-L"))
    p.artifacts = dict(kept)
    s.new_project_state(p)

    def fake_run(job, on_tiles=None, on_wait=None):
        s.update_project("d", _change_scope("grid"))
        return {k: a.model_copy(update={"content_id": "new"}) for k, a in kept.items()}

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path), ["GLAD-L"])
    assert s.project.value.artifacts == kept  # the new records are refused
    assert all((tmp_path / a.rel_path).exists() for a in kept.values())


def test_a_harmonize_failure_after_an_aoi_change_records_nothing(monkeypatch, tmp_path):
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True))
    rkey = raw_key(JAN.id, "GLAD-L")
    kept = _write(tmp_path, {rkey: _art(rkey, "raw_alert")})
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        s.update_project("d", _change_scope("aoi"))
        raise HarmonizeFailed(OSError("disk I/O error"), kept)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"] and s.project.value.artifacts == {}
    assert not (tmp_path / kept[rkey].rel_path).exists()


@pytest.mark.parametrize("change", ["aoi", "grid"])
def test_a_late_row_is_not_recorded_when_its_saved_project_changed_aoi_or_grid(monkeypatch, tmp_path, change):
    """The own-manifest path refuses what the open-project path refuses, and
    deletes the row's files no saved record names."""
    s = AppState()
    d = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    s.new_project_state(d)
    changed = d.model_copy(deep=True)
    _change_scope(change)(changed)
    io.save_project(changed, root=tmp_path)
    written = {}

    def fake_run(job, on_tiles=None, on_wait=None):
        s.close_project_state()
        written[job.source] = _write(tmp_path / "d", _arts(job.source))
        return written[job.source]

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d"), ["GLAD-L"])
    assert io.load_project("d", root=tmp_path).artifacts == {}
    assert not any((tmp_path / "d" / a.rel_path).exists() for a in written["GLAD-L"].values())


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


@pytest.mark.parametrize("left", [False, True], ids=["open", "closed-mid-row"])
def test_a_harmonize_failing_after_a_download_still_records_the_download(monkeypatch, tmp_path, left):
    """The row fails (error shown) but its new raw file is recorded, in the
    open project or, once left, in its own saved manifest, so the next run
    re-harmonizes instead of downloading again."""
    s = AppState()
    d = _project("d", "GLAD-L", "RADD")
    io.save_project(d, root=tmp_path)
    s.new_project_state(d)
    rkey = raw_key(JAN.id, "GLAD-L")
    kept = {rkey: _art(rkey, "raw_alert")}
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        if job.source != "GLAD-L":
            return {}
        if left:
            s.close_project_state()
        raise HarmonizeFailed(OSError("disk I/O error"), kept)

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.errors = []

        def error(self, message, *, timeout=None):
            self.errors.append(message)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    errors, notifier = solara.reactive({}), _Notifier()
    prepare_jobs.run_rows(_ctx(s, pdir=tmp_path / "d", row_errors=errors, notifier=notifier), ["GLAD-L", "RADD"])
    assert notifier.errors == [t("steps.prepare.row_failed", source="GLAD-L", error="disk I/O error")]
    if left:
        assert ran == ["GLAD-L"] and s.project.value is None
        assert io.load_project("d", root=tmp_path).artifacts == kept
    else:
        assert ran == ["GLAD-L", "RADD"]  # the next row still runs
        assert errors.value == {"GLAD-L": "disk I/O error"}
        assert s.project.value.artifacts == kept


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
    """Stop ends the run before its next row, and the task reads "cancelled",
    not "completed" (slice-1 Task 18 minor), with no error toast."""
    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD"))
    cancel = threading.Event()
    ran = []

    def fake_run(job, on_tiles=None, on_wait=None):
        ran.append(job.source)
        cancel.set()
        return {}

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    bus = NotificationBus()
    with pytest.raises(asyncio.CancelledError):
        prepare_jobs.run_rows(_ctx(s, cancel, notifier=Notifier(bus)), ["GLAD-L", "RADD"])
    assert ran == ["GLAD-L"] and not is_writing("d")
    [task] = bus.tasks.value
    assert task.status == TaskStatus.CANCELLED
    assert [toast for toast in bus.toasts.value if toast.type.value == "error"] == []


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

    def refresh(key, build, content_id, legend=None):
        refreshed.append((key, content_id, legend))
        build()

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    monkeypatch.setattr(raster_map, "add_raster_layer",
                        lambda map_, path, *, key, name: drawn.append((map_, path, key, name)))
    layers = SimpleNamespace(is_on=lambda k: k == "src:RADD", refresh=refresh)
    prepare_jobs.run_rows(_ctx(s, layers=layers, map_="MAP", pdir=tmp_path), ["GLAD-L", "RADD"])

    assert busy == [True, True] and not is_writing("d")
    harm = arts["RADD"][harm_key(JAN.id, "RADD")]
    # GLAD-L is not on the map; RADD's layer keeps its name and legend (spec §4.3, §9.4)
    assert refreshed == [("src:RADD", harm.content_id, source_layers.harmonized_legend("src:RADD", "RADD", JAN))]
    assert drawn == [("MAP", paths.resolve(tmp_path, harm.rel_path), "src:RADD", "RADD · 2026-01-01 → 2026-01-31")]


def test_a_row_finishing_after_the_active_period_changed_does_not_redraw_its_source(monkeypatch, tmp_path):
    """A January run still going after the user switched to February must not
    swap January's file into ``src:<source>`` (which may show February's)."""
    s = AppState()
    p = _project("d", "RADD")
    p.periods = {**p.periods, FEB.id: FEB}
    s.new_project_state(p)
    refreshed = []

    def fake_run(job, on_tiles=None, on_wait=None):
        s.update_project("d", lambda live: setattr(live, "active_period", FEB.id))  # the user moves on
        return _arts("RADD")

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    layers = SimpleNamespace(is_on=lambda k: True, refresh=lambda *a, **k: refreshed.append(a))
    prepare_jobs.run_rows(_ctx(s, layers=layers, map_="MAP", pdir=tmp_path), ["RADD"])
    assert harm_key(JAN.id, "RADD") in s.project.value.artifacts  # still recorded
    assert refreshed == []


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


def _named(items):
    return [name if kind == "source" else (kind, name) for kind, name in prepare_jobs._items(items)]


@pytest.fixture
def step(monkeypatch, tmp_path):
    """No Earth Engine: the step only reads ``catalogue_meta`` (``_state``
    pre-fills GLAD-L's entry where a test needs it; RemoteFactsLoader fetches it
    in the app)."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(table=[], jobs=[], started=[])

    @solara.component
    def _RecorderTable(title, columns, rows, empty_text):
        ns.table.append(rows)

    monkeypatch.setattr(prepare_step, "ProductTable", _RecorderTable)
    # Jobs are queued, not started: the test runs them (``run_jobs``).
    monkeypatch.setattr(prepare_step, "spawn_in_context", lambda target, args=(), **kw: ns.jobs.append(target))
    # A job's items as the tests read them: a source by its bare name, a mask as ("mask", name).
    monkeypatch.setattr(prepare_step, "run_rows",
                        lambda ctx, items, force=False: ns.started.append((ctx, _named(items), force)))

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


def _stop_shown(rc) -> bool:
    """The footer's Stop replaces Run while this scope has a job."""
    return any(t("steps.prepare.footer_stop") in w.children for w in rc.find(v.Btn).widgets)


def _run_all_shown(rc, n) -> bool:
    return any(tn("steps.prepare.run_all", n) in w.children for w in rc.find(v.Btn).widgets)


def _toasts(kind):
    return [toast.message for toast in get_current_bus().toasts.value if toast.type.value == kind]


def _state(p, meta=None):
    state = AppState()
    state.new_project_state(p)
    state.catalogue_meta.value = meta if meta is not None else {}
    return state


def _run(row):
    """Click a row's verb pill (its Run)."""
    assert row["cells"][1]["type"] == "verb", row["cells"][1]
    row["cells"][1]["on_click"]()


def _act(row, kind):
    return next(a for a in row["actions"] if a["kind"] == kind)


def _offers_run(row) -> bool:
    """The row's Status cell is a pill: nothing claims it."""
    return row["cells"][1]["type"] == "verb"


def _queued(row) -> bool:
    return row["cells"][1]["label"] == t("steps.prepare.state_queued")


def test_a_grid_change_reharmonizes_without_catalogue_metadata_or_an_ee_aoi(step, monkeypatch):
    """Drift note: only rows that download need Earth Engine metadata; a grid
    change re-harmonizes from the raw file on disk (runner contract), so the
    Earth Engine AOI is not even built (a reopened admin AOI not replayed yet,
    and no sidecar, would fail to).
    """
    def no_aoi(result, pdir):
        pytest.fail("no row downloads: ee_aoi must not be called")

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 1)).fire_event("click", None)
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["GLAD-L"] and force is False
        assert ctx.aoi is None and ctx.meta == {} and ctx.name == "d" and ctx.period == JAN
        assert _toasts("info") == [] and _toasts("error") == []
    finally:
        rc.close()


def test_a_download_waits_for_the_catalogue_metadata(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])  # downloads, so it needs the metadata
        assert step.jobs == [] and _toasts("info") == [t("steps.alerts.meta_loading")]

        state.catalogue_meta.value = {"GLAD-L": {"end": "2026-06-01"}}
        _run(step.table[-1][0])  # the refused claim was released
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["GLAD-L"] and force is False
        assert ctx.aoi == "AOI" and ctx.meta == {"GLAD-L": {"end": "2026-06-01"}}
    finally:
        rc.close()


def test_a_download_without_an_aoi_geometry_is_refused(step, monkeypatch):
    def no_aoi(result, pdir):
        raise ProjectRuleError(key="steps.aoi.no_geometry")

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        assert step.jobs == [] and _toasts("error") == [t("steps.aoi.no_geometry")]
        assert _offers_run(step.table[-1][0])  # the claim was released
    finally:
        rc.close()


@pytest.mark.parametrize("error", [OSError("aoi.geojson: permission denied"), RuntimeError("")],
                         ids=["OSError", "empty-message"])
def test_a_download_whose_ee_aoi_fails_releases_its_rows(step, monkeypatch, error):
    """Any failure building the Earth Engine AOI (a bad sidecar, an ee object
    that cannot be wrapped), not only a missing geometry, must give the claimed
    keys back: otherwise the rows read "running" for the rest of the session."""
    def bad_aoi(result, pdir):
        raise error

    monkeypatch.setattr(aoi_io, "ee_aoi", bad_aoi)
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        assert step.jobs == [] and _toasts("error") == [str(error) or error.__class__.__name__]
        assert _offers_run(step.table[-1][0])  # the claim was released
        assert not _button(rc, tn("steps.prepare.run_all", 1)).disabled
    finally:
        rc.close()


def _row_buttons(row):
    """Whether a row offers its Run pill, and whether Download again is disabled."""
    return [_offers_run(row), _act(row, "refresh")["disabled"]]


def test_a_row_in_flight_disables_its_run_buttons_until_its_job_ends(step, monkeypatch):
    """A second click on a running row is refused by its in-flight key, so the
    row must say it: no pill (it reads queued until its turn), Download again
    disabled. Only that row: another row stays startable while it runs."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING),
              PrepareRow("RADD", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True),
                   meta={"GLAD-L": {}, "RADD": {}})

    box, rc = _render(state)
    try:
        assert [_row_buttons(r) for r in step.table[-1]] == [[True, False]] * 2
        _run(step.table[-1][0])  # GLAD-L runs
        glad, radd = step.table[-1]
        assert _row_buttons(glad) == [False, True] and _queued(glad)
        assert _row_buttons(radd) == [True, False]

        step.run_jobs()  # the job ends and gives the row back
        assert [_row_buttons(r) for r in step.table[-1]] == [[True, False]] * 2
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
        on_run = step.table[-1][0]["cells"][1]["on_click"]
        on_run()
        step.jobs.pop(0)  # a's worker is still running...
        on_run()
        assert step.jobs == []  # ...so a second click is a no-op
        assert _queued(step.table[-1][0])
        assert _stop_shown(rc) and not _run_all_shown(rc, 1)

        state.new_project_state(_project("b", "GLAD-L", bounds=PARAGUAY, grid=True))
        assert _offers_run(step.table[-1][0])
        assert not _button(rc, tn("steps.prepare.run_all", 1)).disabled
        _run(step.table[-1][0])
        step.run_jobs()
        assert [ctx.name for ctx, _, _ in step.started] == ["b"]
    finally:
        rc.close()


def test_stop_reaches_a_job_after_another_row_started(step, monkeypatch):
    """Spec §7.3/§12: Stop cancels a running download between rows. A later
    start in the same project and period (here the Run of another row) must
    not take Stop away from the job still in its current row.
    """
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING),
              PrepareRow("RADD", Status.CURRENT, Status.STALE))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True),
                   meta={"GLAD-L": {}, "RADD": {}})
    contexts = {}

    box, rc = _render(state)

    def blocked_mid_row(ctx, items, force=False):
        sources = _named(items)
        contexts[tuple(sources)] = ctx
        if sources == ["GLAD-L"]:  # still in its current row
            _run(step.table[-1][1])  # RADD starts its own job
            _button(rc, t("steps.prepare.footer_stop")).fire_event("click", None)

    monkeypatch.setattr(prepare_step, "run_rows", blocked_mid_row)
    try:
        _run(step.table[-1][0])
        step.run_jobs()
        assert set(contexts) == {("GLAD-L",), ("RADD",)}
        assert contexts[("GLAD-L",)].cancel.is_set()  # the first job stops after this row
        assert contexts[("RADD",)].cancel.is_set()  # and so does every job of this project and period
        assert not _stop_shown(rc)  # every job ended
    finally:
        rc.close()


def test_stop_in_one_project_leaves_another_projects_job_running(step, monkeypatch):
    """Stop acts on the jobs of the open project and period only, the same
    scope as the in-flight keys that enable it. Each job has its own Event: a
    job started after a Stop is not born stopped, and an ended job's Event
    leaves the session-wide registry.
    """
    registries = []

    class _Recording(prepare_step._StopEvents):
        def __init__(self):
            super().__init__()
            registries.append(self)

    monkeypatch.setattr(prepare_step, "_StopEvents", _Recording)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE), PrepareRow("RADD", Status.CURRENT, Status.STALE))
    state = _state(_project("a", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])  # a's GLAD-L job: queued, so still running
        state.new_project_state(_project("b", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True))
        _run(step.table[-1][0])  # b's job, started last
        state.new_project_state(_project("a", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True))
        _button(rc, t("steps.prepare.footer_stop")).fire_event("click", None)
        _run(step.table[-1][1])  # a's RADD job, while GLAD-L's still runs
        step.run_jobs()

        stopped = {(ctx.name, *sources): ctx.cancel.is_set() for ctx, sources, _ in step.started}
        assert stopped == {("a", "GLAD-L"): True, ("b", "GLAD-L"): False, ("a", "RADD"): False}
        [stops] = registries
        assert stops._events == {}
    finally:
        rc.close()


def test_stop_events_are_scoped_and_forgotten_when_their_job_ends():
    """The registry lives as long as the session: an ended job's Event must
    not stay in it.
    """
    stops = prepare_step._StopEvents()
    a1, a2, b = stops.add(("a", JAN.id)), stops.add(("a", JAN.id)), stops.add(("b", JAN.id))
    stops.stop(("a", JAN.id))
    assert a1.is_set() and a2.is_set() and not b.is_set()

    for scope, event in ((("a", JAN.id), a1), (("a", JAN.id), a2), (("b", JAN.id), b)):
        stops.discard(scope, event)
    assert stops._events == {}
    stops.stop(("b", JAN.id))  # nothing left to stop
    assert not b.is_set()


def test_row_errors_are_cleared_when_the_project_changes(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("a", "GLAD-L", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        step.run_jobs()
        [(ctx, _, _)] = step.started
        ctx.row_errors.set({"GLAD-L": "quota exceeded"})
        assert step.table[-1][0]["error"] == "quota exceeded"

        state.new_project_state(_project("b", "GLAD-L", bounds=PARAGUAY, grid=True))
        assert step.table[-1][0]["error"] is None
    finally:
        rc.close()


def test_a_row_error_recorded_after_a_period_switch_stays_with_its_period(step, monkeypatch):
    """A job's error that lands after the user moved to another period (past
    the job's own active-period check) is not shown on that period's row."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    p = _project("a", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.periods = {**p.periods, FEB.id: FEB}
    state = _state(p, meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        step.run_jobs()
        [(ctx, _, _)] = step.started
        state.update_project("a", lambda live: setattr(live, "active_period", FEB.id))
        ctx.row_errors.set({"GLAD-L": "quota exceeded"})  # January's job, late
        assert step.table[-1][0]["error"] is None
    finally:
        rc.close()


def test_the_info_action_opens_the_details_dialog_and_toggles_it(step):
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)

    def details():
        return next(d for d in rc.find(v.Dialog).widgets if d.max_width == 480)

    try:
        assert not _act(step.table[-1][0], "info")["is_on"] and not details().v_model
        _act(step.table[-1][0], "info")["on_click"]()
        assert _act(step.table[-1][0], "info")["is_on"] and details().v_model
        assert t("steps.prepare.not_run") in str(details().children)
        _act(step.table[-1][0], "info")["on_click"]()  # the same ⓘ again closes it
        assert not _act(step.table[-1][0], "info")["is_on"] and not details().v_model
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
        toggle = _act(step.table[-1][0], "map_toggle")
        assert toggle["kind"] == "map_toggle" and not toggle["disabled"] and not toggle["loading"]
        toggle["on_click"]()
        harm = p.artifacts[harm_key(JAN.id, "GLAD-L")]
        assert drawn == [(paths.resolve(paths.project_dir("d"), harm.rel_path), "src:GLAD-L",
                          "GLAD-L · 2026-01-01 → 2026-01-31")]
        assert _act(step.table[-1][0], "map_toggle")["is_on"]
    finally:
        rc.close()

    busy = SimpleNamespace(on=solara.reactive(frozenset({"src:GLAD-L"})),
                           inflight=solara.reactive(frozenset({"src:GLAD-L"})))
    box, rc = _render(state, layers=busy)
    try:
        assert _act(step.table[-1][0], "map_toggle")["loading"]
    finally:
        rc.close()


def test_the_map_toggle_queues_a_click_behind_a_superseded_build_and_carries_its_legend(step, monkeypatch):
    """Slice-1 deferral closed: the row's map toggle uses the Alerts step's
    show/hide pattern, so a click while a superseded build of the key still
    runs is queued, not dropped; a click on a layer still building is a
    no-op; the layer lands named and with its legend (spec §4.3, §9.4)."""
    import gui.scripts.map_layers as ml

    jobs = []
    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: jobs.append((target, args)))
    drawn = []
    monkeypatch.setattr(raster_map, "add_raster_layer",
                        lambda map_, path, *, key, name: (drawn.append(name), map_.add_layer(name, key=key)))
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.artifacts.update(_arts("GLAD-L"))
    map_ = _FakeMap()
    layers = MapLayers(map_)

    box, rc = _render(_state(p), layers=layers, map_=map_)
    try:
        def click():
            _act(step.table[-1][0], "map_toggle")["on_click"]()

        click()  # starts a build
        click()  # on and still building: a no-op, not a hide
        assert layers.is_on("src:GLAD-L") and len(jobs) == 1
        layers.hide("src:GLAD-L")  # switched off elsewhere: that build is now superseded
        click()  # queued behind it, not dropped
        assert layers.is_on("src:GLAD-L")
        while jobs:
            target, args = jobs.pop(0)
            target(*args)
        name = "GLAD-L · 2026-01-01 → 2026-01-31"
        assert drawn == [name, name] and map_.layers == {"src:GLAD-L": name}
        assert layers.content_of("src:GLAD-L") == p.artifacts[harm_key(JAN.id, "GLAD-L")].content_id
        (legend,) = layers.legends.value
        assert legend == source_layers.harmonized_legend("src:GLAD-L", "GLAD-L", JAN)
        click()  # on and built: hides
        assert not layers.is_on("src:GLAD-L") and layers.legends.value == () and map_.layers == {}
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


def test_grid_form_does_not_overwrite_a_grid_frozen_since_it_rendered(step, monkeypatch):
    """Spec D10: the grid is frozen once a set exists. A Save clicked on a form
    rendered before a worker recorded the project's first set (the click and
    the worker race on ``update_project``; here the worker's record lands
    first) must leave the grid alone and say so."""
    from deforestation_alerts.project.models import AlertSet, AlertSetParams

    state = _state(_project("d", "RADD", bounds=PARAGUAY, grid=True))
    frozen = state.project.value.grid

    def _record_a_set(live):  # Integrate's apply_result, on its worker
        live.alert_sets["x"] = AlertSet(
            id="x", period_id=JAN.id, sources=["RADD"], digit_map={1: "RADD"}, params=AlertSetParams(),
            store_rel_path="s/alert_db.parquet", combined_rel_path="s/c.tif", footprint_rel_path="s/f.tif",
            fingerprint="f", created_at=datetime.now())

    real_update = state.update_project

    def _worker_wins_the_race(name, fn):
        assert real_update(name, _record_a_set)
        return real_update(name, fn)

    box, rc = _render(state)
    try:
        _field(rc, t("steps.prepare.grid_resolution")).v_model = "30"
        monkeypatch.setattr(state, "update_project", _worker_wins_the_race)
        _button(rc, t("steps.prepare.grid_save")).fire_event("click", None)

        assert state.project.value.grid == frozen
        assert _toasts("success") == [] and _toasts("warning") == [t("steps.prepare.grid_locked")]
    finally:
        rc.close()


@pytest.mark.parametrize("when", ["before", "during"])
def test_grid_form_refuses_a_save_while_a_job_writes_into_the_project(step, monkeypatch, when):
    """Review A I1: a Download & harmonize (or Integrate) job reads the grid;
    a Save under it is refused with a reason, also when the job set its mark
    after the click (checked under the state lock)."""
    state = _state(_project("d", "RADD", bounds=PARAGUAY, grid=True))
    before = state.project.value.grid
    real_update = state.update_project

    def _job_started(name, fn):
        with writing(name):
            return real_update(name, fn)

    box, rc = _render(state)
    try:
        _field(rc, t("steps.prepare.grid_resolution")).v_model = "30"
        save = _button(rc, t("steps.prepare.grid_save"))
        if when == "before":
            with writing("d"):
                save.fire_event("click", None)
        else:
            monkeypatch.setattr(state, "update_project", _job_started)
            save.fire_event("click", None)
        assert state.project.value.grid == before
        assert _toasts("success") == [] and _toasts("warning") == [t("steps.prepare.grid_busy")]
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


# ---------------------------------------------------------------------------
# Coverage states, GeoTIFF rows, Stop (spec §7.3)
# ---------------------------------------------------------------------------
def test_stop_ends_the_job_quietly_and_gives_its_rows_back(step, monkeypatch):
    """``run_rows`` raises ``CancelledError`` on Stop (the task reads
    "cancelled"); the step's job treats it as an ordinary end."""
    def cancelled(ctx, sources, force=False):
        raise asyncio.CancelledError()

    monkeypatch.setattr(prepare_step, "run_rows", cancelled)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        assert _queued(step.table[-1][0])
        step.run_jobs()  # no CancelledError escapes the worker
        assert _offers_run(step.table[-1][0])
        assert not _stop_shown(rc) and _toasts("error") == []
    finally:
        rc.close()


def test_blocked_rows_cannot_run_and_run_all_skips_them(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                         (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)),
              PrepareRow("RADD", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-S2", "RADD", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        blocked, radd = step.table[-1]
        assert not _offers_run(blocked)
        assert all(a["disabled"] for a in blocked["actions"] if a["kind"] != "info")
        assert blocked["cells"][0]["subtitle"].startswith(t("coverage.not_covered_row", reason=""))
        assert blocked["notes"] == []  # said once, in the subtitle
        assert _offers_run(radd) and not _act(radd, "refresh")["disabled"]
        _button(rc, tn("steps.prepare.run_all", 1)).fire_event("click", None)
        step.run_jobs()
        assert [sources for _, sources, _ in step.started] == [["RADD"]]
    finally:
        rc.close()


def test_the_subtitle_sums_both_files_of_a_row(step):
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.artifacts = {k: a.model_copy(update={"size_bytes": 2_700_000}) for k, a in _arts("GLAD-L").items()}
    state = _state(p)

    box, rc = _render(state)
    try:
        [row] = step.table[-1]
        assert row["cells"][0]["value"] == t("steps.alerts.label_glad_l") and len(row["cells"]) == 2
        assert " · 5.4 MB" in row["cells"][0]["subtitle"] and row["notes"] == []
        assert len(row["actions"]) == prepare_step.SOURCE_ACTIONS  # the budget's count is the most a row has
    finally:
        rc.close()


def test_check_again_follows_a_failed_coverage_check(step):
    """Spec §6.2 "Failure": the unchecked rows get a Check again action, which
    restarts the check (``coverage_retry``); none while nothing failed."""
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING, "unchecked",
                         (Note("coverage.unchecked", tone="error"),)))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        assert not [w for w in rc.find(v.Btn).widgets if t("coverage.check_again") in w.children]
        state.coverage_error.set("quota exceeded")
        _button(rc, t("coverage.check_again")).fire_event("click", None)
        assert state.coverage_retry.value == 1
        state.coverage_pending.set(True)
        assert _button(rc, t("coverage.check_again")).disabled
        state.coverage_pending.set(False)
        state.sidecar_pending.set(True)  # the retry also refetches the AOI sidecar
        assert _button(rc, t("coverage.check_again")).disabled
        state.sidecar_pending.set(False)
        assert not _button(rc, t("coverage.check_again")).disabled
    finally:
        rc.close()


def _with_tif(tmp_path, *, sidecar):
    from deforestation_alerts.project.models import BandMap, NativeGrid, add_custom_source

    tif = tmp_path / "user.tif"
    tif.write_bytes(b"")
    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    add_custom_source(p, name="tif", origin="file", ref=str(tif), asset_type="FILE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert=1, date=2),
                      native=NativeGrid(crs="EPSG:32721", scale=30.0, transform=[30, 0, 0, 0, -30, 0]))
    if sidecar:
        p.aoi = p.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR})
    return p


def test_a_geotiff_row_needs_no_ee_aoi_and_claims_the_aoi_raster(step, monkeypatch, tmp_path):
    """A GeoTIFF row is normalized locally (no Earth Engine AOI, no catalogue
    metadata) and, when the AOI raster must be built first, claims the
    project's AOI-raster key with its own, so two jobs never build it at once."""
    def no_aoi(result, pdir):
        pytest.fail("a GeoTIFF row must not build the Earth Engine AOI")

    claims = []

    class _Recording(prepare_step.InflightKeys):
        def claim(self, *keys, **kw):
            claims.append(keys)
            return super().claim(*keys, **kw)

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    monkeypatch.setattr(prepare_step, "InflightKeys", _Recording)
    step.plan(PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=True))

    box, rc = _render(state)
    try:
        [row] = step.table[-1]
        assert row["cells"][0]["value"] == "tif"  # a custom source by its name
        assert _act(row, "refresh")["disabled"]  # nothing to download again
        _run(row)
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["tif"] and not force and ctx.aoi is None
        assert claims == [(prepare_step._inflight_key("d", JAN.id, "tif"), prepare_step.aoi_key("d"))]
        assert prepare_step.aoi_key("d") == "prep:d:@aoi" and _toasts("info") == []
    finally:
        rc.close()


def _check_again_shown(rc):
    return bool([w for w in rc.find(v.Btn).widgets if t("coverage.check_again") in w.children])


def test_a_geotiff_row_waits_for_the_aoi_geometry(step, tmp_path):
    step.plan(PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=False))
    state.sidecar_pending.set(True)  # the loader is fetching it

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])
        assert step.jobs == [] and _toasts("info") == [t("steps.prepare.aoi_geometry_missing")]
        assert _offers_run(step.table[-1][0])  # nothing was claimed
        assert not _check_again_shown(rc)  # nothing failed
    finally:
        rc.close()


def test_a_failed_aoi_sidecar_refuses_only_the_geotiff_rows_and_offers_check_again(step, monkeypatch, tmp_path):
    """Spec §12: the Earth Engine rows of a Run all still run; the GeoTIFF
    rows are refused with a message that says the fetch failed, and Check
    again (which refetches the sidecar) is offered though coverage succeeded."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("tif", Status.MISSING, Status.MISSING))
    state = _state(_with_tif(tmp_path, sidecar=False), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        assert state.coverage_error.value is None and _check_again_shown(rc)
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        step.run_jobs()
        [(ctx, sources, _)] = step.started
        assert sources == ["GLAD-L"] and ctx.aoi == "AOI"
        assert _toasts("info") == [t("steps.prepare.aoi_geometry_failed")]

        _button(rc, t("coverage.check_again")).fire_event("click", None)
        assert state.coverage_retry.value == 1
        state.sidecar_pending.set(True)  # the retry fetches it again
        assert not _check_again_shown(rc)
    finally:
        rc.close()


def test_a_refusal_and_a_keyed_engine_error_read_translated(monkeypatch):
    """Task 14 review: no raw "SRC: unchecked" text; an engine error that
    names a message key (``AoiGeometryMissing``, ``MaskNotReady``,
    ``MaskFileMissing``) reads that message, with its ``values``."""
    from deforestation_alerts.engine.runner import AoiGeometryMissing, CoverageRefused

    class _Keyed(FileNotFoundError):  # MaskFileMissing's shape
        key = "steps.prepare.file_missing"

        def __init__(self, path):
            super().__init__(f"raw {path}")
            self.values = {"path": path}

    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "RADD", "GLAD-S2"))

    def fake_run(job, on_tiles=None, on_wait=None):
        if job.source == "GLAD-L":
            raise CoverageRefused("GLAD-L", "unchecked", (Note("coverage.unchecked", tone="error"),))
        if job.source == "GLAD-S2":
            raise _Keyed("/m.tif")
        cause = AoiGeometryMissing("raw engine text")
        raise HarmonizeFailed(cause, {}) from cause  # as the runner chains it

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    errors = solara.reactive({})
    prepare_jobs.run_rows(_ctx(s, row_errors=errors, errors_lock=threading.Lock()), ["GLAD-L", "RADD", "GLAD-S2"])
    assert errors.value == {"GLAD-L": t("coverage.unchecked"),
                            "RADD": t("steps.prepare.aoi_geometry_missing"),
                            "GLAD-S2": t("steps.prepare.file_missing", path="/m.tif")}


def test_a_refusal_toast_names_its_source_once(monkeypatch):
    """A refusal toasts its row's text ("Not covered: GLAD-S2 does not cover
    ..."), not "GLAD-S2: GLAD-S2 ..."; "Coverage not checked", which names no
    source, keeps the source prefix."""
    from deforestation_alerts.engine.runner import CoverageRefused

    s = AppState()
    s.new_project_state(_project("d", "GLAD-L", "GLAD-S2"))
    not_covered = (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)

    def fake_run(job, on_tiles=None, on_wait=None):
        if job.source == "GLAD-S2":
            raise CoverageRefused("GLAD-S2", "not_covered", not_covered)
        raise CoverageRefused("GLAD-L", "unchecked", (Note("coverage.unchecked", tone="error"),))

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.errors = []

        def error(self, message, *, timeout=None):
            self.errors.append(message)

    monkeypatch.setattr(prepare_jobs, "run_source_row", fake_run)
    notifier = _Notifier()
    prepare_jobs.run_rows(_ctx(s, notifier=notifier), ["GLAD-S2", "GLAD-L"])
    reason = t("coverage.not_covered_glad_s2", source="GLAD-S2")
    assert notifier.errors == [t("coverage.not_covered_row", reason=reason),
                               t("steps.prepare.row_failed", source="GLAD-L", error=t("coverage.unchecked"))]


# ---------------------------------------------------------------------------
# Mask rows in the step's jobs (Task 25)
# ---------------------------------------------------------------------------
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _with_mask(*, origin="gee", tmp_path=None, sidecar=True, periods=(JAN,)):
    """Project ``d`` with GLAD-L and a mask ``forest`` on (keep) in every one
    of ``periods``, never aligned, the first of them active."""
    from deforestation_alerts.project.models import NativeGrid, add_mask, set_period_mask

    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.periods = {per.id: per for per in periods}
    p.active_period = periods[0].id
    if origin == "gee":
        add_mask(p, name="forest", origin="gee", ref="UMD/x", asset_type="IMAGE", band="b",
                 native=NativeGrid(crs="EPSG:4326", scale=30.0), mode="keep", period_id=periods[0].id)
    else:
        tif = tmp_path / "forest.tif"
        tif.write_bytes(b"")
        add_mask(p, name="forest", origin="file", ref=str(tif), asset_type="FILE", band=1, native=None,
                 mode="keep", period_id=periods[0].id)
    for per in periods[1:]:
        set_period_mask(p, per.id, "forest", on=True, mode="keep")
    if sidecar:
        p.aoi = p.aoi.model_copy(update={"geometry_file": paths.AOI_SIDECAR})
    return p


def _recording_inflight(monkeypatch):
    claims = []

    class _Recording(prepare_step.InflightKeys):
        def claim(self, *keys, **kw):
            claims.append(keys)
            return super().claim(*keys, **kw)

    monkeypatch.setattr(prepare_step, "InflightKeys", _Recording)
    return claims


def test_run_all_aligns_the_periods_masks_first_and_stop_covers_their_keys(step, monkeypatch):
    """Spec §7.3: Run all is one job, the masks on in the period first; the
    label counts them; the mask's project-wide key is in this period's Stop
    scope; an Earth Engine mask that downloads needs the Earth Engine AOI."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    claims = _recording_inflight(monkeypatch)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_with_mask())

    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        assert claims == [(prepare_step.mask_inflight_key("d", "forest"),
                           prepare_step._inflight_key("d", JAN.id, "GLAD-L"))]
        assert _stop_shown(rc)  # the job's keys are this period's
        assert not _run_all_shown(rc, 2)
        step.run_jobs()
        [(ctx, items, force)] = step.started
        assert items == [("mask", "forest"), "GLAD-L"] and not force and ctx.aoi == "AOI"
        assert not _stop_shown(rc)
    finally:
        rc.close()


def test_a_source_run_runs_its_stale_masks_first(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_with_mask(), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _run(step.table[-1][0])  # Run
        step.run_jobs()
        assert [(items, force) for _, items, force in step.started] == [([("mask", "forest"), "GLAD-L"], False)]
    finally:
        rc.close()


def _footer_item(rc):
    """The footer menu's one item (Download all again), and the tooltip
    around it; the Masks card's ⋮ menus hold list items too."""
    def walk(w):
        yield w
        for c in getattr(w, "children", None) or []:
            if not isinstance(c, str):
                yield from walk(c)

    def label(w):
        return " ".join(c for x in walk(w) if isinstance(x, v.ListItemTitle) for c in x.children)

    masks_menu = {t("steps.prepare.action_download_again"), t("widgets.product_table.action_edit"),
                  t("steps.prepare.mask_delete_action")}
    [item] = [w for w in rc.find(v.ListItem).widgets if label(w) not in masks_menu]
    tips = [w.attributes.get("title") for root in rc.find(v.Html).widgets for w in walk(root)
            if isinstance(w, v.Html) and item in (w.children or [])]
    return item, tips[0] if tips else None


def test_download_all_again_is_disabled_while_masks_pend(step, monkeypatch):
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_with_mask())

    box, rc = _render(state)
    try:
        item, tip = _footer_item(rc)
        assert tn("steps.prepare.redownload_all", 1) in [c for w in rc.find(v.ListItemTitle).widgets
                                                         for c in w.children]
        assert item.disabled and tip == t("steps.prepare.redownload_all_masks")
    finally:
        rc.close()


def test_download_all_again_forces_the_sources_that_have_something_to_fetch(step, monkeypatch, tmp_path):
    """Every catalogue / Earth Engine source that is not blocked, in one forced job."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    p = _with_tif(tmp_path, sidecar=True)
    add_predefined_source(p, "RADD")
    add_predefined_source(p, "GLAD-S2")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), PrepareRow("tif", Status.CURRENT, Status.CURRENT),
              PrepareRow("RADD", Status.CURRENT, Status.CURRENT),
              PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                         (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)))
    state = _state(p, meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        item, tip = _footer_item(rc)
        assert not item.disabled and not tip
        item.fire_event("click", None)  # asks first
        assert step.jobs == []
        names = ", ".join(source_label(p.sources[s]) for s in ("GLAD-L", "RADD"))
        dialog = _confirm(rc, tn("steps.prepare.redownload_all_title", 2))
        assert t("steps.prepare.redownload_all_message", names=names) in _texts(dialog)
        _click(dialog, t("steps.prepare.redownload_label"))
        step.run_jobs()
        [(ctx, items, force)] = step.started
        assert items == ["GLAD-L", "RADD"] and force
        assert ctx.downloads == {("source", "GLAD-L"), ("source", "RADD")}
    finally:
        rc.close()


def _walk(w):
    yield w
    for c in getattr(w, "children", None) or []:
        if not isinstance(c, str):
            yield from _walk(c)


def _texts(w) -> list:
    """Every string a widget tree shows."""
    return [c for x in _walk(w) for c in (getattr(x, "children", None) or []) if isinstance(c, str)]


def _confirm(rc, title):
    """The open dialog titled ``title`` (the step's confirms render even when closed)."""
    [dialog] = [d for d in rc.find(v.Dialog).widgets if d.v_model and title in _texts(d)]
    return dialog


def _closed(rc, title) -> bool:
    return not any(d.v_model and title in _texts(d) for d in rc.find(v.Dialog).widgets)


def _click(dialog, label):
    next(b for b in _walk(dialog) if isinstance(b, v.Btn) and label in b.children).fire_event("click", None)


def _refresh(rc, row):
    """Click a row's Download again and confirm it."""
    _act(row, "refresh")["on_click"]()
    _click(_confirm(rc, t("steps.prepare.redownload_title", source=row["cells"][0]["value"])),
           t("steps.prepare.redownload_label"))


def test_download_again_asks_then_forces(step, monkeypatch):
    """Spec "Confirms": the window and the date of the file it replaces; a
    source never downloaded reads the short message. Only that source runs."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), PrepareRow("RADD", Status.MISSING, Status.MISSING))
    p = _project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True)
    p.periods = {JAN.id: JAN.model_copy(update={"look_back_days": 10})}
    p.artifacts.update(_arts("GLAD-L"))
    downloaded = p.artifacts[raw_key(JAN.id, "GLAD-L")].created_at.date().isoformat()
    state = _state(p, meta={"GLAD-L": {}})
    window = t("steps.period.range", start="2025-12-22", end="2026-01-31")

    box, rc = _render(state)
    try:
        glad, radd = step.table[-1]
        _act(radd, "refresh")["on_click"]()
        title = t("steps.prepare.redownload_title", source=radd["cells"][0]["value"])
        assert t("steps.prepare.redownload_message_new", window=window) in _texts(_confirm(rc, title))
        _click(_confirm(rc, title), t("common.cancel"))
        assert _closed(rc, title) and step.jobs == []

        _act(glad, "refresh")["on_click"]()
        title = t("steps.prepare.redownload_title", source=glad["cells"][0]["value"])
        dialog = _confirm(rc, title)
        assert t("steps.prepare.redownload_message", window=window, date=downloaded) in _texts(dialog)
        assert step.jobs == []  # nothing runs before the confirm
        _click(dialog, t("steps.prepare.redownload_label"))
        assert _closed(rc, title)
        step.run_jobs()
        [(ctx, items, force)] = step.started
        assert items == ["GLAD-L"] and force is True
    finally:
        rc.close()


def test_download_again_waits_for_the_catalogue_metadata(step, monkeypatch):
    """A forced download needs the metadata too (Task 3's carried case)."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_project("d", "GLAD-L", bounds=PARAGUAY, grid=True))

    box, rc = _render(state)
    try:
        assert not _act(step.table[-1][0], "refresh")["disabled"]  # a ready row can still be downloaded again
        _refresh(rc, step.table[-1][0])
        assert step.jobs == [] and _toasts("info") == [t("steps.alerts.meta_loading")]

        state.catalogue_meta.value = {"GLAD-L": {"end": "2026-06-01"}}
        _refresh(rc, step.table[-1][0])  # the refused claim was released
        step.run_jobs()
        [(ctx, sources, force)] = step.started
        assert sources == ["GLAD-L"] and force is True
        assert ctx.aoi == "AOI" and ctx.meta == {"GLAD-L": {"end": "2026-06-01"}}
    finally:
        rc.close()


def test_download_again_does_not_run_the_masks_first(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_with_mask(), meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _refresh(rc, step.table[-1][0])
        step.run_jobs()
        assert [(items, force) for _, items, force in step.started] == [(["GLAD-L"], True)]
    finally:
        rc.close()


def test_download_again_during_a_run_all_keeps_stop(step, monkeypatch):
    """A Download again started while Run all is in its current row joins the
    scope: the footer's Stop stops both."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("RADD", Status.CURRENT, Status.CURRENT))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}, "RADD": {}})
    contexts = {}

    box, rc = _render(state)

    def run_all_blocked_mid_row(ctx, items, force=False):
        sources = _named(items)
        contexts[tuple(sources)] = (ctx, force)
        if sources == ["GLAD-L"]:  # Run all, still in its current row
            _refresh(rc, step.table[-1][1])  # Download again the ready RADD row
            _button(rc, t("steps.prepare.footer_stop")).fire_event("click", None)

    monkeypatch.setattr(prepare_step, "run_rows", run_all_blocked_mid_row)
    try:
        _button(rc, tn("steps.prepare.run_all", 1)).fire_event("click", None)
        step.run_jobs()
        assert set(contexts) == {("GLAD-L",), ("RADD",)} and contexts[("RADD",)][1] is True
        assert contexts[("GLAD-L",)][0].cancel.is_set()  # Run all stops after this row
        assert contexts[("RADD",)][0].cancel.is_set()  # and so does the Download again
        assert not _stop_shown(rc)
    finally:
        rc.close()


def _with_files(*, sets=()):
    """Project ``d`` with GLAD-L's raw and harmonized files of January and
    February on disk, January active; ``sets``: (id, period) of the alert
    sets built from GLAD-L."""
    from deforestation_alerts.project.models import AlertSet, AlertSetParams

    p = _project("d", "GLAD-L", bounds=PARAGUAY, grid=True)
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    pdir = paths.project_dir("d")
    for period in (JAN, FEB):
        for key, kind in ((raw_key(period.id, "GLAD-L"), "raw_alert"), (harm_key(period.id, "GLAD-L"), "harmonized")):
            art = _art(key, kind).model_copy(update={"size_bytes": 2_700_000})
            (pdir / art.rel_path).parent.mkdir(parents=True, exist_ok=True)
            (pdir / art.rel_path).write_bytes(b"x")
            p.artifacts[key] = art
    for sid, period in sets:
        p.alert_sets[sid] = AlertSet(id=sid, period_id=period.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                     params=AlertSetParams(), store_rel_path="s.parquet", combined_rel_path="c.tif",
                                     footprint_rel_path="f.tif", fingerprint="f", created_at=datetime.now())
    return p


def _delete_title(row):
    return t("steps.prepare.delete_files_title", source=row["cells"][0]["value"])


def test_delete_files_hides_the_layer_and_drops_both_records(step, monkeypatch):
    """Spec "Delete files": this period's raw and harmonized files and records
    go, the layer first; the other period's stay, and so does the source."""
    import gui.scripts.map_layers as ml
    from deforestation_alerts.project import cleanup

    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args))
    monkeypatch.setattr(raster_map, "add_raster_layer", lambda map_, path, *, key, name: None)
    shown_when_deleted = []
    real_delete = cleanup.delete_files

    def delete_files(pdir, rel_paths):
        shown_when_deleted.append(layers.is_on("src:GLAD-L"))
        real_delete(pdir, rel_paths)

    monkeypatch.setattr(cleanup, "delete_files", delete_files)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _with_files()
    pdir = paths.project_dir("d")
    jan = [p.artifacts[k].rel_path for k in (raw_key(JAN.id, "GLAD-L"), harm_key(JAN.id, "GLAD-L"))]
    state = _state(p)
    layers = MapLayers(_FakeMap())

    box, rc = _render(state, layers=layers)
    try:
        _act(step.table[-1][0], "map_toggle")["on_click"]()
        assert layers.is_on("src:GLAD-L")
        _act(step.table[-1][0], "delete")["on_click"]()
        dialog = _confirm(rc, _delete_title(step.table[-1][0]))
        message = t("steps.prepare.delete_files_message", size="5.4 MB")  # this period's two files
        assert set(_texts(dialog)) == {_delete_title(step.table[-1][0]), message, t("common.cancel"),
                                       t("common.delete")}  # no set uses them: no warning line
        _click(dialog, t("common.delete"))

        live = state.project.value
        assert raw_key(JAN.id, "GLAD-L") not in live.artifacts and harm_key(JAN.id, "GLAD-L") not in live.artifacts
        assert {raw_key(FEB.id, "GLAD-L"), harm_key(FEB.id, "GLAD-L")} <= set(live.artifacts)
        assert all(not (pdir / rel).exists() for rel in jan)
        assert all((pdir / a.rel_path).exists() for a in live.artifacts.values())
        assert "GLAD-L" in live.sources
        assert shown_when_deleted == [False]  # the layer was hidden before the files went
        assert _closed(rc, _delete_title(step.table[-1][0])) and _toasts("warning") == []
    finally:
        rc.close()


def test_delete_files_warns_about_sets_of_this_period(step):
    """User decision 2026-10-23: allowed, with a line naming this period's sets
    that will read out of date; nothing cascades."""
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _with_files(sets=(("jan-a", JAN), ("jan-b", JAN), ("feb-a", FEB)))
    p.alert_sets["jan-b"].sources = ["GLAD-L", "RADD"]
    state = _state(p)

    box, rc = _render(state)
    try:
        _act(step.table[-1][0], "delete")["on_click"]()
        dialog = _confirm(rc, _delete_title(step.table[-1][0]))
        # named by their sources, not their ids (the period is this one)
        assert t("steps.prepare.delete_files_sets", names="GLAD-L, GLAD-L + RADD") in _texts(dialog)
        _click(dialog, t("common.delete"))
        live = state.project.value
        assert set(live.alert_sets) == {"jan-a", "jan-b", "feb-a"}  # the sets are not touched
        assert raw_key(JAN.id, "GLAD-L") not in live.artifacts
    finally:
        rc.close()


@pytest.mark.parametrize("busy", ["writing", "claimed"])
def test_delete_files_refuses_while_writing(step, monkeypatch, busy):
    """Spec §5.1: deletes wait for the project's writers, and for a job that
    claimed the row after the dialog opened."""
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _with_files()
    pdir = paths.project_dir("d")
    before = dict(p.artifacts)
    state = _state(p, meta={"GLAD-L": {}})

    box, rc = _render(state)
    try:
        _act(step.table[-1][0], "delete")["on_click"]()
        dialog = _confirm(rc, _delete_title(step.table[-1][0]))
        if busy == "writing":
            with writing("d"):
                _click(dialog, t("common.delete"))
        else:
            _refresh(rc, step.table[-1][0])  # a job claims the row while the dialog is open
            _click(dialog, t("common.delete"))
        assert _toasts("warning") == [t("steps.prepare.delete_files_busy")]
        assert state.project.value.artifacts == before
        assert all((pdir / a.rel_path).exists() for a in before.values())
    finally:
        rc.close()


def test_the_footer_counts_this_scopes_rows_and_forgets_the_batch_when_it_ends(step, monkeypatch):
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.MISSING, Status.MISSING), PrepareRow("RADD", Status.MISSING, Status.MISSING))
    state = _state(_project("d", "GLAD-L", "RADD", bounds=PARAGUAY, grid=True), meta={"GLAD-L": {}})
    seen = []

    def texts():
        return [c for w in rc.find(v.Html).widgets for c in (w.children or []) if isinstance(c, str)]

    def a_run(ctx, items, force=False):
        key = prepare_jobs.progress_key("d", JAN.id, "source", "GLAD-L")
        ctx.row_stage.set({key: "download"})
        seen.append(texts())
        ctx.row_stage.set({})
        ctx.on_row_end()
        seen.append(texts())

    monkeypatch.setattr(prepare_step, "run_rows", a_run)
    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        assert _stop_shown(rc) and t("steps.prepare.footer_strip", running="0", queued="2") in texts()
        step.run_jobs()
        running, after_one = seen
        assert t("steps.prepare.footer_strip", running="1", queued="1") in running
        assert t("steps.prepare.footer_done", done="0", total="2") in running
        assert t("steps.prepare.footer_done", done="1", total="2") in after_one
        assert not _stop_shown(rc) and _run_all_shown(rc, 2)

        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)  # a new batch counts afresh
        assert t("steps.prepare.footer_done", done="0", total="2") in texts()
    finally:
        rc.close()


def test_a_mask_another_periods_job_aligns_refuses_the_click_with_a_toast(step, monkeypatch):
    """The mask key is project-wide: February cannot align the mask January's
    Run all is aligning. The click is refused with a toast, not dropped
    silently, and January's job is not in February's Stop scope."""
    added = []

    class _Recording(prepare_step._StopEvents):
        def add(self, scope, keys=()):
            added.append(scope)
            return super().add(scope, keys)

    monkeypatch.setattr(prepare_step, "_StopEvents", _Recording)
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_with_mask(periods=(JAN, FEB)))

    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)  # January's job, still queued
        state.update_project("d", lambda q: setattr(q, "active_period", FEB.id))
        assert not _stop_shown(rc)  # February started nothing
        run_all = _button(rc, tn("steps.prepare.run_all", 2))
        assert not run_all.disabled
        run_all.fire_event("click", None)
        assert len(step.jobs) == 1 and _toasts("info") == [t("steps.prepare.claim_busy")]
        # the refused job never entered February's Stop scope, not even for a moment
        assert added == [("d", JAN.id)] and not _stop_shown(rc)

        step.run_jobs()  # January's job ends and gives the mask back
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        step.run_jobs()
        assert [(ctx.period.id, items) for ctx, items, _ in step.started] == [
            (JAN.id, [("mask", "forest"), "GLAD-L"]), (FEB.id, [("mask", "forest"), "GLAD-L"])]
    finally:
        rc.close()


def test_a_geotiff_mask_claims_the_aoi_raster_and_waits_for_the_aoi_geometry(step, monkeypatch, tmp_path):
    """A GeoTIFF mask needs the AOI raster like a GeoTIFF source: it claims
    ``aoi_key`` when it must be built (no Earth Engine AOI), and without the
    AOI sidecar it waits while the other rows run."""
    def no_aoi(result, pdir):
        pytest.fail("no row downloads from Earth Engine")

    monkeypatch.setattr(aoi_io, "ee_aoi", no_aoi)
    claims = _recording_inflight(monkeypatch)
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.STALE))
    state = _state(_with_mask(origin="file", tmp_path=tmp_path))

    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        step.run_jobs()
        assert claims == [(prepare_step.mask_inflight_key("d", "forest"),
                           prepare_step._inflight_key("d", JAN.id, "GLAD-L"), prepare_step.aoi_key("d"))]
        assert step.started[-1][1] == [("mask", "forest"), "GLAD-L"] and step.started[-1][0].aoi is None
    finally:
        rc.close()

    state = _state(_with_mask(origin="file", tmp_path=tmp_path, sidecar=False))
    state.sidecar_pending.set(True)
    box, rc = _render(state)
    try:
        _button(rc, tn("steps.prepare.run_all", 2)).fire_event("click", None)
        step.run_jobs()
        assert step.started[-1][1] == ["GLAD-L"]
        assert _toasts("info") == [t("steps.prepare.aoi_geometry_missing")]
    finally:
        rc.close()


def test_the_masks_card_sits_between_the_grid_and_the_sources_and_runs_its_masks(step, monkeypatch):
    """Spec §7.3: the Masks card is shown once the grid is set, before the
    sources table; a mask row's Run and Re-download are jobs of their own,
    and the row reads running while its key is claimed."""
    from gui.tile import masks_card

    order, masks, confirms = [], [], {}
    monkeypatch.setattr(masks_card, "ProductTable", lambda **kw: (order.append("masks"), masks.append(kw["rows"])))
    monkeypatch.setattr(masks_card, "ConfirmDialog", lambda **kw: confirms.update({kw["confirm_label"]: kw}))
    sources_table = prepare_step.ProductTable

    @solara.component
    def _Sources(title, columns, rows, empty_text):
        order.append("sources")
        sources_table(title=title, columns=columns, rows=rows, empty_text=empty_text)

    monkeypatch.setattr(prepare_step, "ProductTable", _Sources)
    monkeypatch.setattr(aoi_io, "ee_aoi", lambda result, pdir: "AOI")
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    state = _state(_with_mask())

    box, rc = _render(state)
    try:
        assert order[:2] == ["masks", "sources"]
        [row] = masks[-1]
        assert row["key"] == "forest"
        row["cells"][2]["on_click"]()  # the Download pill
        assert masks[-1][0]["cells"][2]["label"] == t("steps.prepare.state_queued")  # claimed, no stage yet
        step.run_jobs()
        assert masks[-1][0]["cells"][2]["label"] == t("steps.prepare.verb_download")
        [again] = [i for a in masks[-1][0]["actions"] if a["kind"] == "menu" for i in a["items"]
                   if i["label"] == t("steps.prepare.action_download_again")]
        again["on_click"]()
        assert step.started[1:] == []  # it asks first
        confirms[t("steps.prepare.redownload_label")]["on_confirm"]()
        step.run_jobs()
        assert [(items, force) for _, items, force in step.started] == [
            ([("mask", "forest")], False), ([("mask", "forest")], True)]
    finally:
        rc.close()

    order.clear()
    p = _with_mask()
    p.grid = None
    box, rc = _render(_state(p))
    try:
        assert order == []  # no grid: neither the card nor the table
    finally:
        rc.close()


def test_mask_legends_follow_a_switch_and_the_active_period_without_a_rebuild(step, monkeypatch):
    """Spec §7.3, §9.4: switching a mask's mode and changing the active
    period give the mask's layer on the map the active period's choice; its
    layer is not rebuilt."""
    import gui.scripts.map_layers as ml
    from deforestation_alerts.project.models import set_period_mask

    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: target(*args))
    step.plan(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT))
    p = _with_mask()
    p.periods = {**p.periods, FEB.id: FEB}  # the mask is off in February
    state = _state(p)
    map_ = _FakeMap()
    layers = MapLayers(map_)
    built = []
    layers.show("mask:forest", lambda: built.append(1), content_id="c",
                legend=source_layers.mask_layer_legend(p, "forest"))

    def label():
        (entry,) = layers.legends.value
        return entry.spec.labels[0].key

    box, rc = _render(state, layers=layers, map_=map_)
    try:
        assert label() == "legend.mask.keep"
        state.update_project("d", lambda live: set_period_mask(live, JAN.id, "forest", mode="exclude"))
        assert label() == "legend.mask.exclude"
        state.update_project("d", lambda live: setattr(live, "active_period", FEB.id))
        assert label() == "legend.mask.off"
        assert built == [1] and layers.content_of("mask:forest") == "c"
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# The grid size hint
# ---------------------------------------------------------------------------
TINY = (-55.05, -24.05, -55.0, -24.0)  # a few km: a few MB per source at 10 m


def test_the_grid_size_hint_reads_mb_below_a_gb():
    """A small AOI read "about 0.0 GB per source"; below 1 GB the hint is in
    MB (never "0.0"), from 1 GB up it stays in GB."""
    from gui.widget.grid_form import grid_size_text

    small = gridmod.build_grid(TINY, "EPSG:32721", 10.0)
    text = grid_size_text(small)
    assert text == t("steps.prepare.grid_size_mb", w=f"{small.width:,}", h=f"{small.height:,}",
                     mb=f"{gridmod.estimate_bytes(small) / 1e6:.1f}")
    assert " MB " in text and "GB" not in text and "0.0" not in text
    national = gridmod.build_grid((-62.7, -27.6, -54.2, -19.3), "EPSG:32721", 10.0)
    assert " GB " in grid_size_text(national) and "MB" not in grid_size_text(national)
    speck = gridmod.build_grid((-55.001, -24.001, -55.0, -24.0), "EPSG:32721", 10.0)
    assert "about 0.1 MB" in grid_size_text(speck)  # never "0.0"


def test_the_grid_card_shows_the_hint_in_mb_for_a_small_aoi(tmp_path, monkeypatch):
    from gui.widget.grid_form import GridForm

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    state.project.value = _project("d", "RADD", bounds=TINY)

    @solara.component
    def _Harness():
        NotificationProvider()
        GridForm(app_state=state)

    box, rc = reacton.render(_Harness(), handle_error=False)
    try:
        texts = [c for w in rc.find(v.Html, tag="span").widgets for c in w.children if isinstance(c, str)]
        [hint] = [text for text in texts if "per source" in text]
        assert " MB " in hint and "GB" not in hint
        assert t("steps.prepare.grid_large") not in texts  # well under the 2 GB warning
    finally:
        rc.close()


def test_the_delete_files_message_of_a_record_without_a_size_has_no_empty_parentheses():
    p = _with_files()
    for key in (raw_key(JAN.id, "GLAD-L"), harm_key(JAN.id, "GLAD-L")):
        p.artifacts[key] = p.artifacts[key].model_copy(update={"size_bytes": 0})  # a legacy record
    _, message, note = prepare_step.delete_confirm(p, JAN, "GLAD-L")
    assert message == t("steps.prepare.delete_files_message_nosize") and "()" not in message and note is None
    p.artifacts[raw_key(JAN.id, "GLAD-L")] = p.artifacts[raw_key(JAN.id, "GLAD-L")].model_copy(
        update={"size_bytes": 2_700_000})
    assert prepare_step.delete_confirm(p, JAN, "GLAD-L")[1] == t("steps.prepare.delete_files_message", size="2.7 MB")
