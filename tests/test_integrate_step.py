"""Integrate & vectorize (spec §7.4): the form's validation, the worker, the
attribute raster form and the step itself.

The worker is driven with a fake ``run_integration`` (the engine has its own
tests in tests/test_engine_integrate.py). The step is rendered with
``reacton.render`` against a real ``AppState``/``Project`` and a real
ipyleaflet map, with its worker queued instead of started so the test runs
it (tests/test_prepare_step.py's pattern).
"""

import asyncio
import uuid
from datetime import date, datetime
from types import SimpleNamespace

import geopandas as gpd
import ipyleaflet
import ipyvuetify as v
import numpy as np
import pytest
import rasterio
import reacton
import solara
from affine import Affine
from pysepal.mapping.draw_control import DrawControl
from pysepal.solara.notifications import NoopNotifier, NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus
from shapely.geometry import box, shape

from deforestation_alerts.engine import grid as gridmod
from deforestation_alerts.project import io
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    AoiRecord,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from deforestation_alerts.project.models import set_id as make_set_id
from deforestation_alerts.scripts.raster_attribute import ALERT_RESERVED_COLUMNS
from gui.i18n import t, tn
from gui.scripts import integrate_jobs
from gui.scripts.alert_layers import EARLIER_PREFIX, SET_LAYER_KEY
from gui.scripts.map_layers import MapLayers
from gui.store.project_writers import is_writing
from gui.store.state_manager import AppState
from gui.store.workflow_steps import step_index
from gui.tile import integrate_step
from gui.widget import attribute_form, raster_input

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FC = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {}, "geometry": box(0, 0, 1, 1).__geo_interface__}]}
SET_DIR = f"periods/{JAN.id}/sets/glad-l"
PARAGUAY = (-55.5, -24.5, -55.0, -24.0)  # UTM 21S


# ---------------------------------------------------------------------------
# params_from_form, reviewed_in, apply_result (brief Step 1)
# ---------------------------------------------------------------------------
def test_params_from_form():
    params, err = integrate_jobs.params_from_form("0.5", "aoi", None, 1, "0", None)
    assert err is None and params.min_size_ha == 0.5 and params.max_alerts == 0
    assert integrate_jobs.params_from_form("-1", "aoi", None, 1, "0", None)[1] == "steps.integrate.error_min_size"
    assert integrate_jobs.params_from_form("0.5", "drawn", {"type": "FeatureCollection", "features": []},
                                           1, "0", None)[1] == "steps.integrate.error_no_drawing"
    params, err = integrate_jobs.params_from_form("1", "drawn", FC, 3, "25", None)
    assert err is None and params.area_geojson == FC and params.sorting == 3 and params.max_alerts == 25


@pytest.mark.parametrize("min_size, max_alerts, key", [
    ("abc", "0", "steps.integrate.error_min_size"),
    ("nan", "0", "steps.integrate.error_min_size"),  # would crash min_cluster_pixels
    ("inf", "0", "steps.integrate.error_min_size"),
    ("0.5", "-3", "steps.integrate.error_max_alerts"),
    ("0.5", "2.5", "steps.integrate.error_max_alerts"),
])
def test_params_from_form_refuses_bad_numbers(min_size, max_alerts, key):
    assert integrate_jobs.params_from_form(min_size, "aoi", None, 1, max_alerts, None) == (None, key)


def test_params_from_form_keeps_the_drawing_only_for_a_drawn_area():
    params, _ = integrate_jobs.params_from_form("0", "aoi", FC, 2, "", [{"source": "x"}])
    assert params.area_geojson is None and params.max_alerts == 0
    assert params.attribute_layers == [{"source": "x"}] and params.attribute_config is None


def test_params_from_form_writes_the_layers_never_the_legacy_raster():
    """Spec D3: a new run writes ``attribute_layers`` and leaves
    ``attribute_config`` None; without layers the dump is the old one."""
    a, b = {"source": "a.tif", "kind": "local", "bands": []}, {"source": "users/me/b", "kind": "gee", "bands": []}
    params, _ = integrate_jobs.params_from_form("0.5", "aoi", None, 1, "0", (a, b))
    assert params.attribute_layers == [a, b] and params.attribute_config is None
    params, _ = integrate_jobs.params_from_form("0.5", "aoi", None, 1, "0", None)
    assert params.attribute_layers == [] and "attribute_layers" not in params.model_dump(mode="json")


def _set(sid, reviewed, sources=("GLAD-L",), params=None):
    return AlertSet(id=sid, period_id=JAN.id, sources=list(sources), digit_map={0: "GLAD-L"},
                    params=params or AlertSetParams(), store_rel_path=f"{SET_DIR}/alert_db.parquet",
                    combined_rel_path=f"{SET_DIR}/c.tif", footprint_rel_path=f"{SET_DIR}/footprint.tif",
                    n_reviewed=reviewed, fingerprint="f", created_at=datetime.now())


def test_reviewed_in():
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    assert integrate_jobs.reviewed_in(p, "x") == 0
    p.alert_sets["x"] = _set("x", 4)
    assert integrate_jobs.reviewed_in(p, "x") == 4


def test_apply_result_records_the_set_and_activates_it():
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    s = _set("x", 0)
    art = SimpleNamespace(key="store:x")
    result = SimpleNamespace(alert_set=s, artifacts={"store:x": art}, alerts=None)
    integrate_jobs.apply_result(p, result)
    assert p.alert_sets["x"] is s and p.active_set == "x"
    assert p.artifacts == {"store:x": art}  # no pmtiles record: Overview uses mtimes (ruling #79)


def test_apply_result_replaces_the_shared_containers_instead_of_mutating_them():
    """``AppState`` publishes shallow copies, so every snapshot shares the
    live project's dicts. A render iterating one on another thread (the Period
    step's ``_has_sets``, the Alerts step's ``_sets_using``) must not see it
    change size under the worker's record."""
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    p.alert_sets["y"] = _set("y", 0)
    p.artifacts["store:y"] = SimpleNamespace(key="store:y")
    snapshot = p.model_copy()
    sets_being_iterated = iter(snapshot.alert_sets.values())
    artifacts_being_iterated = iter(snapshot.artifacts.values())

    integrate_jobs.apply_result(p, SimpleNamespace(alert_set=_set("x", 0), artifacts={"store:x": "a"}, alerts=None))

    assert [s.id for s in sets_being_iterated] == ["y"]  # no "dictionary changed size during iteration"
    assert len(list(artifacts_being_iterated)) == 1
    assert set(snapshot.alert_sets) == {"y"} and set(snapshot.artifacts) == {"store:y"}
    assert set(p.alert_sets) == {"x", "y"} and set(p.artifacts) == {"store:x", "store:y"}


# ---------------------------------------------------------------------------
# run_integration_job (the worker body): review focus 1 and 5
# ---------------------------------------------------------------------------
class _Notifier(NoopNotifier):
    def __init__(self):
        self.toasts = []

    def success(self, message, *, timeout=None):
        self.toasts.append(("success", message))

    def info(self, message, *, timeout=None):
        self.toasts.append(("info", message))

    def warning(self, message, *, timeout=None):
        self.toasts.append(("warning", message))

    def error(self, message, *, timeout=None):
        self.toasts.append(("error", message))


def _bare(name="d"):
    p = Project(name=name, created_at=datetime.now(), updated_at=datetime.now())
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    return p


def _open(name="d"):
    state = AppState()
    state.new_project_state(_bare(name))
    return state


def _job(name="d", pdir=None, project=None):
    """A job as the step builds it: ``project`` is the deep copy the run used
    (name, AOI and grid are what the record is checked against)."""
    project = project or SimpleNamespace(name=name, aoi=None, grid=None)
    return SimpleNamespace(project=project, pdir=pdir, period=JAN)


def _art(key, kind="store"):
    return Artifact(key=key, kind=kind, rel_path=f"{SET_DIR}/alert_db.parquet", fingerprint="f", inputs={},
                    content_id=uuid.uuid4().hex, created_at=datetime.now())


def _result(n):
    alerts = gpd.GeoDataFrame({"alert_id": list(range(n))}, geometry=[box(0, 0, 1, 1)] * n, crs="EPSG:4326")
    s = _set("x", 0).model_copy(update={"n_alerts": n})
    return SimpleNamespace(alert_set=s, artifacts={"store:x": _art("store:x")}, alerts=alerts, outcomes={})


def _swapped_in(pdir, result):
    """Write the files a run swaps in: its artifacts and the set's tiles."""
    for a in result.artifacts.values():
        (pdir / a.rel_path).parent.mkdir(parents=True, exist_ok=True)
        (pdir / a.rel_path).write_bytes(b"x")
    (pdir / SET_DIR / "alerts.pmtiles").write_bytes(b"x")
    return result


def _tiles(monkeypatch, available=True, fail=None):
    """Stub the tile build; returns the list of (alerts, out_path) it was asked for."""
    built = []

    def build(alerts, out):
        built.append((alerts, out))
        if fail is not None:
            raise fail

    monkeypatch.setattr(integrate_jobs.pmt, "tippecanoe_available", lambda: available)
    monkeypatch.setattr(integrate_jobs.pmt, "alerts_to_pmtiles", build)
    return built


def test_zero_alerts_still_records_the_set_and_skips_tiles(monkeypatch, tmp_path):
    """Review focus 1 and spec §12: an empty set is recorded and announced with
    an info toast; no tiles, and no jump to an Analysis with nothing to review.
    """
    state = _open("d")
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: _result(0))
    built = _tiles(monkeypatch)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"))
    assert built == []
    assert state.project.value.active_set == "x"
    assert state.current_page_view.value != "analysis_tile"  # nothing to review
    assert notifier.toasts == [("info", t("steps.integrate.done_empty"))]
    assert len(state.alerts_gdf.value) == 0 and state.actual_alert_id.value == -1


def test_an_empty_set_lists_each_sources_outcome(monkeypatch, tmp_path):
    """Spec §7.4: the info toast of an empty set says what each source gave,
    the left-out sources included."""
    state = _open("d")
    result = _result(0)
    result.outcomes = {"RADD": {"alert_pixels": 0, "alert_pixels_unmasked": 7},
                       "GLAD-L": {"alert_pixels": 3, "alert_pixels_unmasked": 3}}
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: result)
    _tiles(monkeypatch)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"),
                                       left_out={"GLAD-S2": "GLAD-S2 does not cover this AOI"})
    details = "; ".join([tn("steps.integrate.outcome_pixels", 3, source="GLAD-L"),
                         tn("steps.integrate.outcome_masked", 7, source="RADD"),
                         "GLAD-S2 does not cover this AOI"])
    assert notifier.toasts == [("info", t("steps.integrate.done_empty_detail", details=details))]


def test_a_finished_run_records_the_set_builds_its_tiles_and_opens_analysis(monkeypatch, tmp_path):
    state = _open("d")
    state.actual_alert_id.set(0)  # alert 0 of the set being replaced is on screen
    picked = []

    def auto_select(gdf):  # what Analysis's _select_first_alert_cb does
        if gdf is not None and len(gdf) and state.actual_alert_id.value < 0:
            state.go_to(0)

    state.alerts_gdf.subscribe(auto_select)
    state.actual_alert_id.subscribe(picked.append)
    result, busy = _result(2), []

    def fake_run(job, on_step=None):
        busy.append(is_writing("d"))
        for key in ("steps.integrate.step_combine", "steps.integrate.step_vectorize",
                    "steps.integrate.step_store", "steps.integrate.step_footprint"):
            on_step(key)
        return result

    monkeypatch.setattr(integrate_jobs, "run_integration", fake_run)
    built = _tiles(monkeypatch)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"))

    live = state.project.value
    assert live.active_set == "x" and live.alert_sets["x"] is result.alert_set
    assert live.artifacts == result.artifacts  # no pmtiles record (ruling #79)
    [(alerts, out)] = built
    assert alerts is result.alerts and out == tmp_path / "d" / SET_DIR / "alerts.pmtiles"
    assert state.alerts_gdf.value is result.alerts
    # ruling #76: a fresh selection even though alert 0 was already showing
    assert picked == [-1, 0]
    assert state.current_page_view.value == "analysis_tile"
    assert notifier.toasts == [("success", tn("steps.integrate.done", 2))]
    assert busy == [True] and not is_writing("d")


def test_the_published_alerts_are_stamped_with_the_store_the_run_wrote(monkeypatch, tmp_path):
    """A review Save of these alerts checks that the set's store on disk is
    still the one they came from (set_store.generation_changed)."""
    from gui.scripts import set_store

    state = _open("d")
    result = _result(2)
    store = tmp_path / "d" / SET_DIR / "alert_db.parquet"
    store.parent.mkdir(parents=True)
    result.alerts.to_parquet(store)  # run_integration swapped it in
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: result)
    _tiles(monkeypatch)
    integrate_jobs.run_integration_job(state, _Notifier(), _job(pdir=tmp_path / "d"))

    assert state.alerts_gdf.value is result.alerts
    assert result.alerts.attrs[set_store.STORE_GENERATION] == set_store.store_generation(store)
    assert not set_store.generation_changed(result.alerts, store)


def test_a_tile_failure_warns_and_still_records_the_set(monkeypatch, tmp_path):
    """The engine already swapped the set's files in: a tippecanoe or scratch
    failure must not leave them under the old manifest record (Task 10 ruling,
    spec §12's tippecanoe row).
    """
    state = _open("d")
    result = _result(2)
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: result)
    _tiles(monkeypatch, fail=RuntimeError("tippecanoe crashed"))
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"))  # does not raise
    assert state.project.value.active_set == "x" and state.alerts_gdf.value is result.alerts
    assert notifier.toasts == [("warning", t("steps.integrate.tiles_failed", error="tippecanoe crashed")),
                               ("success", tn("steps.integrate.done", 2))]
    assert state.current_page_view.value == "analysis_tile"


def test_without_tippecanoe_the_set_is_recorded_with_a_warning(monkeypatch, tmp_path):
    state = _open("d")
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: _result(2))
    built = _tiles(monkeypatch, available=False)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"))
    assert built == [] and state.project.value.active_set == "x"
    assert notifier.toasts[0] == ("warning", t("steps.integrate.no_tippecanoe"))


def test_result_is_dropped_when_the_project_was_closed(monkeypatch, tmp_path):
    """Review focus 5 and ruling #75: nothing reaches the session, and the set
    is recorded in the job's own saved manifest, whose files it just replaced.
    """
    state = AppState()
    io.save_project(_bare("d"), root=tmp_path)
    result = _result(2)
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: result)
    monkeypatch.setattr(integrate_jobs.pmt, "tippecanoe_available", lambda: False)
    integrate_jobs.run_integration_job(state, None, _job(pdir=tmp_path / "d"))  # no project open
    assert state.project.value is None and state.alerts_gdf.value is None
    assert state.current_page_view.value != "analysis_tile"
    saved = io.load_project("d", root=tmp_path)
    assert saved.active_set == "x" and saved.alert_sets["x"] == result.alert_set
    assert saved.artifacts["store:x"] == result.artifacts["store:x"]


def test_a_run_whose_project_was_switched_records_the_set_in_its_own_project(monkeypatch, tmp_path):
    state = _open("d")
    io.save_project(_bare("d"), root=tmp_path)
    other = _bare("other")
    result = _result(2)

    def fake_run(job, on_step=None):
        state.new_project_state(other)  # the user opens another project mid-run
        return result

    monkeypatch.setattr(integrate_jobs, "run_integration", fake_run)
    built = _tiles(monkeypatch)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, _job(pdir=tmp_path / "d"))

    assert state.project.value is other and other.alert_sets == {} and other.active_set is None
    assert state.alerts_gdf.value is None and state.current_page_view.value != "analysis_tile"
    assert notifier.toasts == []
    assert not (tmp_path / "other").exists()
    assert io.load_project("d", root=tmp_path).active_set == "x"
    assert [out for _, out in built] == [tmp_path / "d" / SET_DIR / "alerts.pmtiles"]  # d's own set folder


def test_a_late_set_is_not_recorded_for_a_period_its_saved_project_no_longer_has(monkeypatch, tmp_path):
    saved = _bare("d")
    saved.periods, saved.active_period = {}, None
    io.save_project(saved, root=tmp_path)
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: _result(2))
    _tiles(monkeypatch, available=False)
    integrate_jobs.run_integration_job(AppState(), None, _job(pdir=tmp_path / "d"))
    assert io.load_project("d", root=tmp_path).alert_sets == {}


def _edit(change):
    """What the user saves on another step while the worker runs."""
    def period(live):
        from deforestation_alerts.project import periods
        assert periods.edit_period(live, live.active_period, "2026-02-01", "2026-02-28")[0] is None

    def aoi(live):  # AoiStep's _apply
        live.aoi = live.aoi.model_copy(update={"digest": "moved"})
        live.grid = None
        live.artifacts = {}

    def grid(live):  # the grid form's _apply
        live.grid = gridmod.build_grid(PARAGUAY, "EPSG:32721", 10.0)

    return {"period": period, "aoi": aoi, "grid": grid}[change]


@pytest.mark.parametrize("change", ["period", "aoi", "grid"])
def test_a_set_is_not_recorded_when_the_period_aoi_or_grid_changed_during_the_run(monkeypatch, tmp_path, change):
    """A project's first Integrate leaves the period, AOI and grid editable.
    A set recorded after one of them changed would be an orphan that locks
    the project for good (period), a locked project without a grid (AOI), or
    a set built on another grid: the record is refused with a warning, and
    nothing reaches Analysis."""
    state = _open("d")
    state.new_project_state(_project("d", "GLAD-L"))
    before = state.project.value
    job = _job(pdir=tmp_path / "d", project=before.model_copy(deep=True))

    def fake_run(job, on_step=None):
        assert state.update_project("d", _edit(change))
        return _swapped_in(tmp_path / "d", _result(2))

    monkeypatch.setattr(integrate_jobs, "run_integration", fake_run)
    _tiles(monkeypatch)
    notifier = _Notifier()
    integrate_jobs.run_integration_job(state, notifier, job)

    assert not (tmp_path / "d" / SET_DIR).exists()  # review A M1: no orphan set folder
    live = state.project.value
    assert live.alert_sets == {} and live.active_set is None and live.artifacts == {} and not live.locked
    assert state.alerts_gdf.value is None and state.current_page_view.value != "analysis_tile"
    assert notifier.toasts == [("warning", t("steps.integrate.project_changed"))]
    if change == "period":
        assert JAN.id not in live.periods
    elif change == "aoi":
        assert live.grid is None  # and not locked: the AOI step can still set a new one


@pytest.mark.parametrize("change", ["aoi", "grid"])
def test_a_late_set_is_not_recorded_in_a_saved_project_whose_aoi_or_grid_changed(monkeypatch, tmp_path, change):
    """The own-project record (ruling #75) checks the same three things as
    the live one; the period is covered above."""
    saved = _project("d", "GLAD-L")
    _edit(change)(saved)
    io.save_project(saved, root=tmp_path)
    job = _job(pdir=tmp_path / "d", project=_project("d", "GLAD-L"))
    monkeypatch.setattr(integrate_jobs, "run_integration",
                        lambda job, on_step=None: _swapped_in(tmp_path / "d", _result(2)))
    _tiles(monkeypatch, available=False)

    integrate_jobs.run_integration_job(AppState(), None, job)

    assert io.load_project("d", root=tmp_path).alert_sets == {}
    assert not (tmp_path / "d" / SET_DIR).exists()


def test_a_refused_rerun_keeps_the_files_its_previous_record_names(monkeypatch, tmp_path):
    """Only a set no record names is an orphan: a refused re-run's files stay
    under the set's previous record (it reads out of date)."""
    p = _project("d", "GLAD-L")
    result = _result(2)
    p.alert_sets["x"] = result.alert_set
    p.artifacts = dict(result.artifacts)
    state = AppState()
    state.new_project_state(p)
    job = _job(pdir=tmp_path / "d", project=p.model_copy(deep=True))

    def fake_run(job, on_step=None):
        assert state.update_project("d", _edit("grid"))
        return _swapped_in(tmp_path / "d", result)

    monkeypatch.setattr(integrate_jobs, "run_integration", fake_run)
    _tiles(monkeypatch, available=False)
    integrate_jobs.run_integration_job(state, None, job)
    assert (tmp_path / "d" / SET_DIR / "alert_db.parquet").exists()


def test_a_late_set_that_cannot_be_recorded_does_not_fail_the_job(monkeypatch, tmp_path, caplog):
    """No manifest to record into (never saved, or unreadable): the error is
    logged, the job ends normally, and no project is created from nothing.
    """
    monkeypatch.setattr(integrate_jobs, "run_integration", lambda job, on_step=None: _result(2))
    _tiles(monkeypatch, available=False)
    notifier = _Notifier()
    with caplog.at_level("ERROR", logger="SEPALUI.deforestation_alerts"):
        integrate_jobs.run_integration_job(AppState(), notifier, _job(pdir=tmp_path / "d"))
    assert not any(kind == "error" for kind, _ in notifier.toasts)  # tracked_job saw no failure
    assert any("d" in r.getMessage() and r.exc_info for r in caplog.records)
    assert not (tmp_path / "d").exists()


def test_a_failed_run_records_nothing_and_fails_the_task(monkeypatch):
    state = _open("d")

    def boom(job, on_step=None):
        raise ValueError("RADD is not downloaded and harmonized for this period (missing)")

    monkeypatch.setattr(integrate_jobs, "run_integration", boom)
    built = _tiles(monkeypatch)
    notifier = _Notifier()
    with pytest.raises(ValueError):
        integrate_jobs.run_integration_job(state, notifier, _job())
    assert state.project.value.alert_sets == {} and state.project.value.active_set is None
    assert built == [] and not is_writing("d")
    assert notifier.toasts == [("error", "RADD is not downloaded and harmonized for this period (missing)")]


# ---------------------------------------------------------------------------
# IntegrateStep: render-based coverage
# ---------------------------------------------------------------------------
def _project(name="d", *sources, sets=()):
    p = _bare(name)
    for s in sources:
        add_predefined_source(p, s)
    p.aoi = AoiRecord(spec={}, name=name, method="DRAW", digest=f"digest-{name}", bounds=PARAGUAY)
    p.grid = gridmod.build_grid(PARAGUAY, gridmod.auto_utm_epsg(PARAGUAY), 30.0)
    for s in sets:
        p.alert_sets[s.id] = s
        p.active_set = s.id
    return p


@solara.component
def _Harness(app_state, map_, layers):
    NotificationProvider()  # the step's use_notifications() needs it rendered first
    integrate_step.IntegrateStep(app_state=app_state, map_=map_, gee_interface=object(), layers=layers)


def _render(state, layers=None):
    """The step over a fresh map. ``layers``: the session's layer registry, which
    the step only reads (``inflight``, to hold a switch while its own layer is
    being built) -- tests that assert on the switches pass their own."""
    map_ = ipyleaflet.Map()
    layers = MapLayers(map_) if layers is None else layers
    box_, rc = reacton.render(_Harness(app_state=state, map_=map_, layers=layers), handle_error=False)
    return map_, rc


@pytest.fixture
def step(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(ready=["GLAD-L", "RADD"], left_out={}, jobs=[], runs=[], kwargs=[], modal=[], tables={},
                         draws=[])
    monkeypatch.setattr(integrate_step, "integrate_choices", lambda p, pdir, period: [
        {"source": s, "label": s, "enabled": True, "note": ""} for s in ns.ready] + [
        {"source": s, "label": s, "enabled": False, "note": why} for s, why in ns.left_out.items()])
    # Jobs are queued, not started: the test runs them (``run_jobs``).
    monkeypatch.setattr(integrate_step, "spawn_in_context", lambda target, args=(), **kw: ns.jobs.append(target))

    def _run_job(app_state, notifier, job, **kw):
        ns.runs.append(job)
        ns.kwargs.append(kw)

    monkeypatch.setattr(integrate_step, "run_integration_job", _run_job)

    # The attribute modal is a recorder (its tests: tests/test_attribute_modal.py).
    # The table is the real one; its rows are recorded by title.
    @solara.component
    def _RecorderAttributeModal(open_, gee_interface, layers, editing, on_save):
        ns.modal.append(SimpleNamespace(open_=open_, layers=layers, editing=editing, on_save=on_save))

    monkeypatch.setattr(integrate_step, "AttributeModal", _RecorderAttributeModal)
    real_table = integrate_step.ProductTable

    @solara.component
    def _RecorderProductTable(title, columns, rows, empty_text):
        ns.tables[title] = rows
        real_table(title=title, columns=columns, rows=rows, empty_text=empty_text)

    monkeypatch.setattr(integrate_step, "ProductTable", _RecorderProductTable)

    class _Draw(DrawControl):
        def __init__(self, m, **kwargs):
            super().__init__(m, **kwargs)
            self.calls = []
            ns.draws.append(self)

        def show(self):
            self.calls.append("show")
            super().show()

        def hide(self):
            self.calls.append("hide")
            super().hide()

    monkeypatch.setattr(integrate_step, "DrawControl", _Draw)

    def run_jobs():
        while ns.jobs:
            ns.jobs.pop(0)()

    ns.run_jobs = run_jobs
    return ns


def _state(p, *, on_step=True):
    state = AppState()
    state.new_project_state(p)
    if on_step:
        state.active_step.set(step_index("integrate"))
    return state


def _field(rc, label):
    return next(w for w in rc.find(v.TextField).widgets if w.label == label)


def _select(rc, label):
    return next(w for w in rc.find(v.Select).widgets if w.label == label)


def _button(rc, label):
    return next(w for w in rc.find(v.Btn).widgets if label in w.children)


def _checkbox(rc, label):
    return next(w for w in rc.find(v.Checkbox).widgets if w.label == label)


def _bands_select(rc):
    return next(w for w in rc.find(v.Select).widgets if w.multiple)


def _name_field(rc):
    return next(w for w in rc.find(v.TextField).widgets if w.label == t("steps.integrate.attr_name"))


def _origin(rc):
    """RasterInput's origin Select (Earth Engine / GeoTIFF)."""
    return rc.find(v.Select, label=t("steps.raster_input.origin_label")).widget


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def _texts(rc):
    """Every text in the rendered tree (``rc.find`` stops at a matching widget,
    so it would miss an ``Html`` nested in another)."""
    return _strings(rc.container)


def _toasts(kind):
    return [toast.message for toast in get_current_bus().toasts.value if toast.type.value == kind]


def _run(rc):
    _button(rc, t("steps.integrate.run")).fire_event("click", None)


def test_the_step_is_locked_until_a_source_is_ready(step):
    step.ready = []
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        assert [w.children for w in rc.find(v.Alert).widgets] == [[t("workflow.lock_integrate")]]
        assert not [w for w in rc.find(v.Btn).widgets if t("steps.integrate.run") in w.children]
    finally:
        rc.close()


def test_run_starts_one_job_for_the_ticked_sources(step, tmp_path):
    state = _state(_project("d", "GLAD-L", "RADD"))
    map_, rc = _render(state)
    try:
        assert t("steps.integrate.will_create", period=JAN.id, sources="GLAD-L + RADD") in _texts(rc)
        _field(rc, t("steps.integrate.min_size")).v_model = "1"
        assert _field(rc, t("steps.integrate.min_size")).hint == t("steps.integrate.min_size_hint", px=12)  # 30 m
        _field(rc, t("steps.integrate.max_alerts")).v_model = "25"
        _select(rc, t("steps.integrate.sorting")).v_model = 3
        _checkbox(rc, "RADD").v_model = False
        assert t("steps.integrate.will_create", period=JAN.id, sources="GLAD-L") in _texts(rc)

        _run(rc)
        assert len(step.jobs) == 1 and _button(rc, t("steps.integrate.run")).disabled
        _run(rc)  # a second click while the job runs is a no-op
        assert len(step.jobs) == 1

        step.run_jobs()
        [job] = step.runs
        assert job.sources == ["GLAD-L"] and job.period == JAN and job.pdir == tmp_path / "d"
        assert job.params == AlertSetParams(min_size_ha=1.0, sorting=3, max_alerts=25)
        assert not hasattr(job, "workspace")
        assert job.project == state.project.value and job.project is not state.project.value  # a deep copy
        assert not _button(rc, t("steps.integrate.run")).disabled  # released
    finally:
        rc.close()


def test_invalid_numbers_are_refused_with_a_toast(step):
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        _field(rc, t("steps.integrate.min_size")).v_model = "-1"
        _run(rc)
        assert step.jobs == [] and _toasts("error") == [t("steps.integrate.error_min_size")]
    finally:
        rc.close()


def test_no_ticked_source_disables_run(step):
    step.ready = ["GLAD-L"]
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        _checkbox(rc, "GLAD-L").v_model = False
        assert _button(rc, t("steps.integrate.run")).disabled
        assert not any(JAN.id in s for s in _texts(rc))  # no set to create or replace
    finally:
        rc.close()


def test_replacing_a_set_with_reviewed_alerts_asks_first(step):
    """Spec §12: the confirmation lists the reviews a Replace discards;
    cancelling leaves the set untouched."""
    both = make_set_id(JAN.id, ["GLAD-L", "RADD"])
    state = _state(_project("d", "GLAD-L", "RADD", sets=[_set(both, 3, sources=["GLAD-L", "RADD"])]))
    map_, rc = _render(state)
    try:
        _run(rc)
        dialog = rc.find(v.Dialog, v_model=True).widget  # the dialog open after Run
        assert step.jobs == [] and dialog.v_model is True
        assert tn("steps.integrate.replace_message", 3) in _texts(rc)
        _button(rc, t("common.cancel")).fire_event("click", None)
        assert step.jobs == [] and dialog.v_model is False

        _run(rc)
        _button(rc, t("common.replace")).fire_event("click", None)
        assert dialog.v_model is False
        step.run_jobs()
        assert [job.sources for job in step.runs] == [["GLAD-L", "RADD"]]
    finally:
        rc.close()


def test_a_set_without_reviews_is_replaced_without_asking(step):
    both = make_set_id(JAN.id, ["GLAD-L", "RADD"])
    state = _state(_project("d", "GLAD-L", "RADD", sets=[_set(both, 0, sources=["GLAD-L", "RADD"])]))
    map_, rc = _render(state)
    try:
        _run(rc)
        assert len(step.jobs) == 1
        rc.find(v.Dialog, v_model=True).assert_empty()  # no confirmation asked
    finally:
        rc.close()


def _store(tmp_path, statuses):
    store = tmp_path / "d" / SET_DIR / "alert_db.parquet"
    store.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame({"alert_id": list(range(len(statuses))), "status": statuses,
                      "area_ha": [1.5 + i for i in range(len(statuses))]},
                     geometry=[box(i, i, i + 0.01, i + 0.01) for i in range(len(statuses))],
                     crs="EPSG:4326").to_parquet(store)


def test_the_replace_confirmation_lists_the_reviewed_alerts(step, tmp_path):
    both = make_set_id(JAN.id, ["GLAD-L", "RADD"])
    _store(tmp_path, ["Confirmed", "Not reviewed", "False Positive", "Need revision"])
    state = _state(_project("d", "GLAD-L", "RADD", sets=[_set(both, 3, sources=["GLAD-L", "RADD"])]))
    map_, rc = _render(state)
    try:
        _run(rc)
        texts = _texts(rc)
        assert tn("steps.integrate.replace_message", 3) in texts
        assert {"#0 · Reviewed: confirmed · 1.50 ha", "#2 · Reviewed: false positive · 3.50 ha",
                "#3 · Reviewed: needs revision · 4.50 ha"} <= set(texts)
        assert not any(s.startswith("#1 ·") for s in texts)  # not reviewed: nothing lost
    finally:
        rc.close()


def test_reviewed_lines_are_capped_and_survive_a_missing_store(step, tmp_path):
    p = _project("d", "GLAD-L", sets=[_set("x", 25)])
    assert integrate_jobs.reviewed_lines(p, "x") == []  # no store on disk: the count alone remains
    _store(tmp_path, ["Confirmed"] * 25)
    lines = integrate_jobs.reviewed_lines(p, "x", limit=20)
    assert len(lines) == 21 and lines[0] == "#0 · Reviewed: confirmed · 1.50 ha"
    assert lines[-1] == t("steps.integrate.replace_more", n=5)
    assert integrate_jobs.status_label(None) == t("legend.status.not_reviewed")


def _feature(geom):
    return {"type": "Feature", "properties": {"style": {"color": "#2196f3"}}, "geometry": geom.__geo_interface__}


def test_a_drawn_area_needs_a_drawing_on_the_map(step):
    step.ready = ["GLAD-L"]
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        _select(rc, t("steps.integrate.area")).v_model = "drawn"
        [draw] = step.draws
        assert draw in map_.controls
        _run(rc)
        assert step.jobs == [] and _toasts("error") == [t("steps.integrate.error_no_drawing")]

        drawn = box(-55.3, -24.3, -55.2, -24.2)
        draw.data = [_feature(drawn)]
        _run(rc)
        step.run_jobs()
        [job] = step.runs
        assert job.params.area_mode == "drawn"
        [feature] = job.params.area_geojson["features"]
        assert shape(feature["geometry"]).equals(drawn) and "style" not in feature["properties"]
    finally:
        rc.close()


def test_the_draw_control_is_on_the_map_only_on_this_step_in_drawn_mode(step):
    """Ruling #77: the DrawControl leaves the shared map with the step. pysepal's
    show() and hide() both clear the drawing, so an unrelated re-render must
    call neither (drift note).
    """
    state = _state(_project("d", "GLAD-L"), on_step=False)  # the AOI step is showing
    map_, rc = _render(state)
    try:
        [draw] = step.draws
        state.active_step.set(step_index("integrate"))
        state.active_step.set(0)
        assert draw.calls == []  # "Whole AOI": the control stays off the map, untouched
        _select(rc, t("steps.integrate.area")).v_model = "drawn"
        assert draw not in map_.controls and draw.calls == []
        state.active_step.set(step_index("integrate"))
        assert draw in map_.controls and draw.calls == ["show"]
        state.active_step.set(step_index("aoi"))  # another step is showing
        assert draw not in map_.controls
        state.active_step.set(step_index("integrate"))
        assert draw in map_.controls

        calls = list(draw.calls)
        _field(rc, t("steps.integrate.min_size")).v_model = "2"
        state.update_project("d", lambda p: setattr(p, "look_back_days", 30))  # a project publish
        assert draw.calls == calls and draw in map_.controls

        _select(rc, t("steps.integrate.area")).v_model = "aoi"
        assert draw not in map_.controls
        _select(rc, t("steps.integrate.area")).v_model = "drawn"
        assert draw in map_.controls
        state.close_project_state()  # the form goes away, and its control with it
        assert draw not in map_.controls
    finally:
        rc.close()


def test_the_form_starts_from_the_active_set_and_resets_for_another_project(step):
    """The step is mounted for the whole session (ruling #18's pattern): a
    project's typed values, drawing or attribute raster never run in another.
    """
    last = AlertSetParams(min_size_ha=2.0, sorting=3, max_alerts=25, area_mode="drawn", area_geojson=FC)
    state = _state(_project("d", "GLAD-L", sets=[_set("x", 0, params=last)]))
    step.ready = ["GLAD-L"]
    map_, rc = _render(state)
    try:
        assert _field(rc, t("steps.integrate.min_size")).v_model == "2.0"
        assert _select(rc, t("steps.integrate.sorting")).v_model == 3
        assert _field(rc, t("steps.integrate.max_alerts")).v_model == "25"
        assert _select(rc, t("steps.integrate.area")).v_model == "drawn"
        [first] = step.draws
        assert first in map_.controls
        _field(rc, t("steps.integrate.min_size")).v_model = "7"

        state.new_project_state(_project("e", "GLAD-L"))
        assert _field(rc, t("steps.integrate.min_size")).v_model == "0.5"
        assert _select(rc, t("steps.integrate.sorting")).v_model == 1
        assert _select(rc, t("steps.integrate.area")).v_model == "aoi"
        assert first not in map_.controls and not any(d in map_.controls for d in step.draws)
    finally:
        rc.close()


@pytest.mark.parametrize("status", ["current", "stale", "missing"])
def test_the_step_names_an_active_set_that_is_out_of_date_or_missing(step, tmp_path, status):
    """Spec §5.3 and review focus 4: the step says when the active set must be
    run again, and why; a current set gets no line."""
    from deforestation_alerts.engine.fingerprint import (
        fingerprint, harm_key, harmonized_inputs, raw_alert_inputs, raw_key, set_inputs,
    )

    step.ready = ["GLAD-L"]
    p = _project("d", "GLAD-L")
    # real fingerprints: set_status also compares the set's raw and harmonized records with their inputs
    rkey, hkey = raw_key(JAN.id, "GLAD-L"), harm_key(JAN.id, "GLAD-L")
    p.artifacts[rkey] = _art(rkey, "raw_alert").model_copy(
        update={"fingerprint": fingerprint(raw_alert_inputs(p, "GLAD-L", JAN))})
    p.artifacts[hkey] = _art(hkey, "harmonized").model_copy(
        update={"fingerprint": fingerprint(harmonized_inputs(p, "GLAD-L", JAN))})
    s = _set("x", 0)
    if status != "stale":
        s = s.model_copy(update={"fingerprint": fingerprint(set_inputs(p, JAN, ["GLAD-L"], s.params))})
    if status != "missing":
        store = tmp_path / "d" / s.store_rel_path
        store.parent.mkdir(parents=True)
        store.write_bytes(b"store")
    p.alert_sets[s.id] = s
    p.active_set = s.id
    map_, rc = _render(_state(p))
    try:
        texts = _texts(rc)
        label = f"{JAN.id} · GLAD-L"
        lines = {kind: t(f"steps.integrate.set_{kind}", set=label) for kind in ("stale", "missing")}
        assert [kind for kind, line in lines.items() if line in texts] == ([status] if status != "current" else [])
    finally:
        rc.close()


ATTR_LC = {"source": "/home/u/rasters/landcover.tif", "kind": "local",
           "bands": [{"band": "lc", "stat": "majority", "column": "lc"}]}
ATTR_DEM = {"source": "users/me/dem", "kind": "gee",
            "bands": [{"band": "elevation", "stat": "mean", "column": "elevation", "crs": "EPSG:4326",
                       "scale": 30.0},
                      {"band": "slope", "stat": "max", "column": "slope", "crs": "EPSG:4326", "scale": 30.0}]}


def _attr_rows(step):
    return step.tables[t("steps.integrate.attr_list_title")]


def test_attribute_rows_name_the_raster_list_its_columns_and_offer_edit_and_delete():
    edits, deletes = [], []
    rows = integrate_step.attribute_rows([ATTR_LC, ATTR_DEM], edit=edits.append, delete=deletes.append)
    assert [r["key"] for r in rows] == ["0:/home/u/rasters/landcover.tif", "1:users/me/dem"]
    assert [r["cells"] for r in rows] == [[{"value": "landcover.tif"}], [{"value": "users/me/dem"}]]
    assert rows[1]["notes"] == [{"text": t("steps.integrate.attr_columns", columns="elevation, slope"),
                                 "tone": None}]
    assert [[a["kind"] for a in r["actions"]] for r in rows] == [["edit", "delete"]] * 2
    assert max(len(r["actions"]) for r in rows) == integrate_step.ATTRIBUTE_ACTIONS
    rows[1]["actions"][0]["on_click"]()
    rows[0]["actions"][1]["on_click"]()
    assert edits == [1] and deletes == [0]
    assert integrate_step.attribute_columns() == [{"label": t("steps.integrate.attr_col_raster")}]


def test_attribute_layers_are_added_edited_and_deleted_and_reach_the_job(step):
    """Spec D3: the list is the form's state until Run, which writes it as
    ``attribute_layers``. The modal is mounted while closed (it mounts its
    form, which lists the user's Earth Engine assets, only when opened)."""
    step.ready = ["GLAD-L"]
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        assert _attr_rows(step) == [] and t("steps.integrate.attr_empty") in _texts(rc)
        assert step.modal[-1].open_.value is False and step.modal[-1].layers == []
        add = _button(rc, t("steps.integrate.attr_add"))
        assert add.outlined and add.block
        add.fire_event("click", None)
        assert step.modal[-1].open_.value is True and step.modal[-1].editing is None
        step.modal[-1].on_save(None, ATTR_LC)
        step.modal[-1].on_save(None, ATTR_DEM)
        assert [r["cells"][0]["value"] for r in _attr_rows(step)] == ["landcover.tif", "users/me/dem"]
        assert f"{t('steps.integrate.attr_list_title')} (2)" in _texts(rc)
        assert step.modal[-1].layers == [ATTR_LC, ATTR_DEM]

        _attr_rows(step)[0]["actions"][0]["on_click"]()  # edit the first
        assert step.modal[-1].editing == 0 and step.modal[-1].open_.value is True
        edited = {**ATTR_LC, "bands": [{"band": "lc", "stat": "mean", "column": "lc"}]}
        step.modal[-1].on_save(0, edited)
        _attr_rows(step)[1]["actions"][1]["on_click"]()  # delete the second
        assert step.modal[-1].layers == [edited]

        _run(rc)
        step.run_jobs()
        params = step.runs[0].params
        assert params.attribute_layers == [edited] and params.attribute_config is None
    finally:
        rc.close()


def test_a_set_made_before_attribute_layers_reopens_as_one_layer(step):
    """Review focus 2: a set with the legacy ``attribute_config`` lists its
    raster as one layer, and a re-run writes it as ``attribute_layers``."""
    step.ready = ["GLAD-L"]
    last = AlertSetParams(attribute_config=ATTR_LC)
    state = _state(_project("d", "GLAD-L", sets=[_set("x", 0, params=last)]))
    map_, rc = _render(state)
    try:
        [row] = _attr_rows(step)
        assert row["cells"] == [{"value": "landcover.tif"}]
        assert row["notes"] == [{"text": t("steps.integrate.attr_columns", columns="lc"), "tone": None}]
        assert step.modal[-1].layers == [ATTR_LC]
        _run(rc)
        step.run_jobs()
        params = step.runs[0].params
        assert params.attribute_layers == [ATTR_LC] and params.attribute_config is None
        assert params.model_dump(mode="json")["attribute_layers"] == [ATTR_LC]

        state.new_project_state(_project("e", "GLAD-L"))  # another project starts with none
        assert _attr_rows(step) == []
    finally:
        rc.close()


def test_a_left_out_source_is_shown_disabled_with_its_reason_and_reaches_the_toast(step):
    """Spec §7.4: a source of the period that is not covered or has no data
    is listed, disabled, with why; the job is told what was left out."""
    step.ready = ["GLAD-L"]
    step.left_out = {"GLAD-S2": "GLAD-S2 does not cover this AOI"}
    map_, rc = _render(_state(_project("d", "GLAD-L", "GLAD-S2")))
    try:
        left = _checkbox(rc, "GLAD-S2 · GLAD-S2 does not cover this AOI")
        assert left.disabled and left.v_model is False
        assert not _checkbox(rc, "GLAD-L").disabled
        assert t("steps.integrate.will_create", period=JAN.id, sources="GLAD-L") in _texts(rc)
        _run(rc)
        step.run_jobs()
        assert step.runs[0].sources == ["GLAD-L"]
        assert step.kwargs == [{"left_out": {"GLAD-S2": "GLAD-S2 does not cover this AOI"}}]
    finally:
        rc.close()


def test_the_lock_stays_while_only_left_out_sources_exist(step):
    step.ready = []
    step.left_out = {"RADD": "RADD starts 2020-01-01 here"}
    map_, rc = _render(_state(_project("d", "RADD")))
    try:
        assert [w.children for w in rc.find(v.Alert).widgets] == [[t("workflow.lock_integrate")]]
    finally:
        rc.close()


def test_another_projects_run_neither_shows_nor_blocks_this_one(step):
    """Review focus 5 at the UI level: the in-flight key belongs to the project
    that started the run."""
    step.ready = ["GLAD-L"]
    state = _state(_project("a", "GLAD-L"))
    map_, rc = _render(state)
    try:
        _run(rc)
        assert len(step.jobs) == 1 and _button(rc, t("steps.integrate.run")).disabled

        state.new_project_state(_project("b", "GLAD-L"))
        assert not _button(rc, t("steps.integrate.run")).disabled
        _run(rc)
        assert len(step.jobs) == 2

        state.new_project_state(_project("a", "GLAD-L"))  # reopening a: its run is still going
        assert _button(rc, t("steps.integrate.run")).disabled
        step.run_jobs()
        assert [job.project.name for job in step.runs] == ["a", "b"]
        assert not _button(rc, t("steps.integrate.run")).disabled
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# AttributeForm
# ---------------------------------------------------------------------------
@pytest.fixture
def pickers(monkeypatch):
    """The pysepal pickers are swapped for recorders exposing their reactives."""
    ns = SimpleNamespace(asset=None, path=None)

    @solara.component
    def _Asset(types, value, gee_interface):
        ns.asset = value

    @solara.component
    def _File(extensions, value, label):
        ns.path = value

    monkeypatch.setattr(raster_input, "AssetSelectComponent", _Asset)
    monkeypatch.setattr(raster_input, "FileInputComponent", _File)
    return ns


@solara.component
def _AttrHarness(gee_interface, initial, seen, reserved=ALERT_RESERVED_COLUMNS, on_busy=None):
    NotificationProvider()
    value, set_value = solara.use_state(initial)

    def on_value(new):
        seen.append(new)
        set_value(new)

    attribute_form.AttributeForm(gee_interface=gee_interface, value=value, on_value=on_value, reserved=reserved,
                                 on_busy=on_busy)


async def _settle(done, tries=200):
    for _ in range(tries):
        if done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the band load never finished")


def _geotiff(path, descriptions):
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=len(descriptions), dtype="uint8",
                       crs="EPSG:4326", transform=Affine(1, 0, 0, 0, -1, 4)) as dst:
        dst.write(np.ones((len(descriptions), 4, 4), np.uint8))
        dst.descriptions = tuple(descriptions)
    return path


def test_attribute_form_loads_a_geotiffs_bands_and_edits_them(pickers, tmp_path):
    tif = _geotiff(tmp_path / "landcover.tif", ["lc", None])
    seen = []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            load = _button(rc, t("steps.integrate.attr_load"))
            load.fire_event("click", None)
            await _settle(lambda: t("steps.integrate.attr_no_source") in _texts(rc))
            assert seen == []

            pickers.path.set(str(tif))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen[-1].get("name"))
            # a fresh load selects no band and fills the name from the file
            assert seen[-1] == {"source": str(tif), "kind": "local", "bands": [], "name": "landcover"}
            assert _bands_select(rc).items == ["lc", "band_2"] and _bands_select(rc).v_model == []
            assert t("steps.integrate.attr_no_source") not in _texts(rc)

            _bands_select(rc).v_model = ["lc", "band_2"]
            assert [b["column"] for b in seen[-1]["bands"]] == ["landcover_lc", "landcover_band_2"]
            _bands_select(rc).v_model = ["lc"]
            next(w for w in rc.find(v.Select).widgets if w.v_model == "majority").v_model = "mean"
            _name_field(rc).v_model = "Land cover"
            assert seen[-1] == {"source": str(tif), "kind": "local", "name": "Land cover",
                                "bands": [{"band": "lc", "stat": "mean", "column": "land_cover_lc"}]}
            assert t("steps.integrate.attr_name_hint") in [w.hint for w in rc.find(v.TextField).widgets]

            pickers.path.set(str(tmp_path / "other.tif"))  # picked, not loaded yet
            _bands_select(rc).v_model = ["lc", "band_2"]
            assert seen[-1]["source"] == str(tif)  # the rows still describe the loaded raster
        finally:
            rc.close()

    asyncio.run(main())


def test_attribute_form_reports_its_load_as_busy(pickers, tmp_path):
    """The real form's ``on_busy`` (the attribute modal takes Save off Enter
    while it is busy; tests/test_attribute_modal.py fakes the form): idle at
    mount, busy while Load reads the bands, idle once they are read."""
    tif = _geotiff(tmp_path / "landcover.tif", ["lc"])
    seen, seen_busy = [], []

    async def main():
        _box, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen,
                                               on_busy=seen_busy.append), handle_error=False)
        try:
            assert seen_busy == [False]
            _origin(rc).v_model = "file"
            pickers.path.set(str(tif))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen_busy[-1] is False)
            assert seen[-1]["source"] == str(tif)
        finally:
            rc.close()

    asyncio.run(main())
    assert seen_busy == [False, True, False]


def test_closing_the_form_during_a_load_stops_it(pickers, monkeypatch, tmp_path):
    """ESC or Cancel unmounts the form mid-load, and use_task does not cancel
    on unmount: the load must stop there, or it writes the bands it read into
    a form the user closed (as the source, mask and vis modals stop theirs)."""
    import threading

    reading, release, read = threading.Event(), threading.Event(), []

    def slow_band_names(src):
        reading.set()
        release.wait(5)
        read.append(src)
        return ["lc"]

    monkeypatch.setattr(attribute_form, "band_names", slow_band_names)
    shown, seen = solara.reactive(True), []

    @solara.component
    def _Host():
        NotificationProvider()
        if shown.value:
            attribute_form.AttributeForm(gee_interface=None, value=None, on_value=seen.append)

    async def main():
        _box, rc = reacton.render(_Host(), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            pickers.path.set(str(tmp_path / "lc.tif"))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(reading.is_set)
            shown.set(False)  # the modal closed: the form unmounts
            release.set()
            await _settle(lambda: read)  # the read itself ran to its end...
            await asyncio.sleep(0.1)
            assert seen == []  # ...and nothing was handed back
        finally:
            release.set()
            rc.close()

    asyncio.run(main())


def test_attribute_form_shows_a_load_failure_next_to_the_field(pickers, tmp_path):
    seen = []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            pickers.path.set(str(tmp_path / "missing.tif"))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: any("missing.tif" in s for s in _texts(rc)))
            assert seen == []
        finally:
            rc.close()

    asyncio.run(main())


def test_attribute_form_restores_a_saved_config(pickers):
    cfg = {"source": "/home/u/lc.tif", "kind": "local", "name": "LC",
           "bands": [{"band": "lc", "stat": "median", "column": "lc_lc"}]}
    box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=cfg, seen=[]), handle_error=False)
    try:
        assert _origin(rc).v_model == "file" and pickers.path.value == "/home/u/lc.tif"
        assert _bands_select(rc).v_model == ["lc"] and _name_field(rc).v_model == "LC"
        assert [w.v_model for w in rc.find(v.Select).widgets if not w.multiple and w is not _origin(rc)] == ["median"]
        assert "lc_lc" in _texts(rc)  # the written column, read-only
    finally:
        rc.close()


def test_reloading_an_edited_layers_bands_keeps_its_selection_and_statistics(pickers, tmp_path):
    tif = _geotiff(tmp_path / "lc.tif", ["lc", "wider"])
    cfg = {"source": str(tif), "kind": "local", "name": "LC",
           "bands": [{"band": "lc", "stat": "median", "column": "lc_lc"}]}
    seen = []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=cfg, seen=seen), handle_error=False)
        try:
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen)
            await asyncio.sleep(0.1)
            assert _bands_select(rc).items == ["lc", "wider"] and _bands_select(rc).v_model == ["lc"]
            assert _name_field(rc).v_model == "LC"
            assert seen[-1] == {"source": str(tif), "kind": "local", "name": "LC",
                                "bands": [{"band": "lc", "stat": "median", "column": "lc_lc"}]}
        finally:
            rc.close()

    asyncio.run(main())


def test_a_name_typed_by_the_user_survives_loading_another_raster(pickers, tmp_path):
    a, b = _geotiff(tmp_path / "a.tif", ["x"]), _geotiff(tmp_path / "b.tif", ["y"])
    seen = []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            pickers.path.set(str(a))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen[-1].get("name") == "a")
            _name_field(rc).v_model = "Mine"
            pickers.path.set(str(b))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen[-1]["source"] == str(b))
            await asyncio.sleep(0.1)
            assert _name_field(rc).v_model == "Mine" and seen[-1]["name"] == "Mine"
        finally:
            rc.close()

    asyncio.run(main())


def test_attribute_form_keeps_a_nameless_saved_layer_as_it_was(pickers):
    """A layer saved before layers had names: its columns stay until it is named, and
    editing a statistic neither fills a name nor renames them."""
    cfg = {"source": "/home/u/lc.tif", "kind": "local",
           "bands": [{"band": "lc", "stat": "median", "column": "mine"}]}
    seen = []
    box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=cfg, seen=seen), handle_error=False)
    try:
        assert _name_field(rc).v_model == ""
        rc.find(v.Select, v_model="median").widget.v_model = "mean"
        assert seen[-1] == {"source": "/home/u/lc.tif", "kind": "local",
                            "bands": [{"band": "lc", "stat": "mean", "column": "mine"}]}
        _name_field(rc).v_model = "LC"
        assert seen[-1]["bands"][0]["column"] == "lc_lc" and seen[-1]["name"] == "LC"
    finally:
        rc.close()


def test_attribute_form_shows_the_column_that_is_written(pickers, tmp_path):
    """The written column is ``<name>_<band>``, shown read-only per selected band."""
    tif = _geotiff(tmp_path / "lc.tif", ["lc", "count"])
    seen = []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            pickers.path.set(str(tif))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen[-1].get("name"))
            assert _name_field(rc).v_model == "lc"
            _bands_select(rc).v_model = ["lc", "count"]
            assert [b["column"] for b in seen[-1]["bands"]] == ["lc_lc", "lc_count"]
            assert "lc_lc" in _texts(rc) and "lc_count" in _texts(rc)

            _name_field(rc).v_model = "Land Cover "
            assert [b["column"] for b in seen[-1]["bands"]] == ["land_cover_lc", "land_cover_count"]
            assert seen[-1]["name"] == "Land Cover"
        finally:
            rc.close()

    asyncio.run(main())


def test_attribute_form_never_writes_another_layers_or_an_alert_column(pickers, tmp_path):
    """Review focus 3: another layer writes ``lc_b1`` already, so this raster's
    ``lc_b1`` is written ``lc_b1_2``; the text shows what is written."""
    from deforestation_alerts.scripts.raster_attribute import reserved_for

    tif = _geotiff(tmp_path / "lc.tif", ["b1", "count"])
    other = {"source": "/home/u/x.tif", "kind": "local", "bands": [{"band": "b1", "stat": "mean", "column": "lc_b1"}]}
    seen = []

    async def main():
        _box, rc = reacton.render(_AttrHarness(gee_interface=None, initial=None, seen=seen,
                                               reserved=reserved_for([other])), handle_error=False)
        try:
            _origin(rc).v_model = "file"
            pickers.path.set(str(tif))
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen and seen[-1].get("name"))
            _bands_select(rc).v_model = ["b1", "count"]
            assert [b["column"] for b in seen[-1]["bands"]] == ["lc_b1_2", "lc_count"]
            assert "lc_b1_2" in _texts(rc)
        finally:
            rc.close()

    asyncio.run(main())


def test_attribute_form_keeps_a_saved_bands_projection_on_edit(pickers):
    """Task 15's free-space gate reads each band's crs/scale: an edit of a
    reopened config must not drop them (it would fall back to 10 m in 4326)."""
    cfg = {"source": "users/me/lc", "kind": "gee",
           "bands": [{"band": "b1", "stat": "majority", "column": "b1", "crs": "EPSG:32721", "scale": 30.0}]}
    seen = []
    box_, rc = reacton.render(_AttrHarness(gee_interface=None, initial=cfg, seen=seen), handle_error=False)
    try:
        assert _origin(rc).v_model == "gee"
        assert pickers.asset.value["asset_id"] == "users/me/lc"
        rc.find(v.Select, v_model="majority").widget.v_model = "mean"
        assert seen[-1]["bands"] == [{"band": "b1", "stat": "mean", "column": "b1", "crs": "EPSG:32721",
                                      "scale": 30.0}]
    finally:
        rc.close()


def test_attribute_form_reads_an_images_band_names_through_the_session_interface(pickers, gee):
    """Both requests go through the session's interface: the band names, then
    each band's projection, whose scale (``nominalScale``, metres) is what
    Integrate's free-space gate reads."""
    import ee

    from deforestation_alerts.engine.download import estimate_native_bytes
    from deforestation_alerts.scripts.attribute_source import download_estimate

    projections = {"landcover": {"crs": "EPSG:32721", "scale": 30.0},
                   "elevation": {"crs": "EPSG:4326", "scale": 92.76624232772798}}

    class _Gee:
        def __init__(self):
            self.asked = []

        async def get_info_async(self, obj):
            self.asked.append(obj)
            return ["landcover", "elevation"] if len(self.asked) == 1 else projections

    fake, seen = _Gee(), []

    async def main():
        box_, rc = reacton.render(_AttrHarness(gee_interface=fake, initial=None, seen=seen), handle_error=False)
        try:
            pickers.asset.set({"asset_id": "users/me/landcover", "type": "IMAGE", "column": "ALL", "value": None})
            _button(rc, t("steps.integrate.attr_load")).fire_event("click", None)
            await _settle(lambda: seen)
        finally:
            rc.close()

    asyncio.run(main())
    assert len(fake.asked) == 2 and all(isinstance(o, ee.ComputedObject) for o in fake.asked)
    assert seen == [{"source": "users/me/landcover", "kind": "gee", "bands": [
        {"band": "landcover", "stat": "majority", "column": "landcover", "crs": "EPSG:32721", "scale": 30.0},
        {"band": "elevation", "stat": "majority", "column": "elevation", "crs": "EPSG:4326",
         "scale": 92.76624232772798}]}]
    assert download_estimate(seen[-1], PARAGUAY) == sum(
        estimate_native_bytes(PARAGUAY, (b["crs"], b["scale"])) // 2 for b in projections.values())


def test_the_band_projections_request_reads_metres(gee):
    """Live: the request the form sends returns each band's nominal scale in
    metres, also for a band in a geographic CRS."""
    from deforestation_alerts.engine import user_raster

    got = user_raster.band_projections_request("USGS/SRTMGL1_003", ["elevation"]).getInfo()
    assert got["elevation"]["crs"] == "EPSG:4326"
    assert 25 < got["elevation"]["scale"] < 35  # 1 arc-second, in metres


# ---------------------------------------------------------------------------
# The alert layers' switches (spec §9.2): intent only
# ---------------------------------------------------------------------------
DEC = Period(id="2025-12-01_2025-12-31", start=date(2025, 12, 1), end=date(2025, 12, 31))


def _with_earlier(p):
    """``p`` with a December GLAD-L set beside its active January one, so the
    step has an earlier period to offer."""
    p.periods[DEC.id] = DEC
    sid = make_set_id(DEC.id, ["GLAD-L"])
    p.alert_sets[sid] = AlertSet(
        id=sid, period_id=DEC.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"periods/{DEC.id}/sets/glad-l/alert_db.parquet",
        combined_rel_path=f"periods/{DEC.id}/sets/glad-l/c.tif",
        footprint_rel_path=f"periods/{DEC.id}/sets/glad-l/footprint.tif",
        n_reviewed=1, fingerprint="f", created_at=datetime.now())
    return p


def _switches(rc):
    return {w.label: w for w in rc.find(v.Switch).widgets}


def test_the_alert_switches_appear_once_a_run_has_produced_a_set(step):
    """Spec §9.2: the alerts of the run that just finished can be put on the
    shared map from here. Nothing to show before a set exists, and no earlier
    period to show until one is there."""
    step.ready = ["GLAD-L"]
    map_, rc = _render(_state(_project("d", "GLAD-L")))
    try:
        assert _switches(rc) == {}
    finally:
        rc.close()

    map_, rc = _render(_state(_project("d", "GLAD-L", sets=[_set("x", 0)])))
    try:
        assert list(_switches(rc)) == [t("steps.integrate.show_alerts")]
    finally:
        rc.close()

    p = _with_earlier(_project("d", "GLAD-L", sets=[_set("x", 0)]))
    map_, rc = _render(_state(p))
    try:
        assert list(_switches(rc)) == [t("steps.integrate.show_alerts"), t("steps.integrate.show_earlier")]
    finally:
        rc.close()


def test_the_alert_switches_only_write_the_two_booleans_the_shell_reads(step):
    """The switches carry no build closure: the shell's ``_AlertLayers`` leaf
    owns every ``MapLayers`` call, because a layer switched on here stays on
    while the user moves to Analysis or opens the Overview dashboard, and this
    form unmounts with the step."""
    step.ready = ["GLAD-L"]
    state = _state(_with_earlier(_project("d", "GLAD-L", sets=[_set("x", 0)])))
    map_ = ipyleaflet.Map()
    layers = MapLayers(map_)
    box_, rc = reacton.render(_Harness(app_state=state, map_=map_, layers=layers), handle_error=False)
    try:
        assert state.show_alerts.value is False and state.show_earlier_sets.value is False
        _switches(rc)[t("steps.integrate.show_alerts")].v_model = True
        assert state.show_alerts.value is True and state.show_earlier_sets.value is False
        _switches(rc)[t("steps.integrate.show_earlier")].v_model = True
        assert state.show_earlier_sets.value is True
        _switches(rc)[t("steps.integrate.show_alerts")].v_model = False
        assert state.show_alerts.value is False and state.show_earlier_sets.value is True
        assert layers.on.value == frozenset()  # no layer was asked for from here
    finally:
        rc.close()


def test_a_switch_is_held_while_its_own_layer_is_being_built(step):
    """A flip during the build it started would race it, so each switch is
    disabled exactly while its own keys are in ``MapLayers.inflight``."""
    step.ready = ["GLAD-L"]
    state = _state(_with_earlier(_project("d", "GLAD-L", sets=[_set("x", 0)])))
    map_ = ipyleaflet.Map()
    layers = MapLayers(map_)
    box_, rc = reacton.render(_Harness(app_state=state, map_=map_, layers=layers), handle_error=False)
    try:
        alerts, earlier = t("steps.integrate.show_alerts"), t("steps.integrate.show_earlier")
        assert [_switches(rc)[k].disabled for k in (alerts, earlier)] == [False, False]

        layers.inflight.set(frozenset({SET_LAYER_KEY}))
        assert [_switches(rc)[k].disabled for k in (alerts, earlier)] == [True, False]

        layers.inflight.set(frozenset({f"{EARLIER_PREFIX}{make_set_id(DEC.id, ['GLAD-L'])}"}))
        assert [_switches(rc)[k].disabled for k in (alerts, earlier)] == [False, True]

        layers.inflight.set(frozenset())
        assert [_switches(rc)[k].disabled for k in (alerts, earlier)] == [False, False]
    finally:
        rc.close()
