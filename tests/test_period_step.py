"""Pure-function tests for apply_period/default_dates (brief's TDD tests,
plus the Task 17 pre-flight rulings' required behaviour), plus render-based
coverage of PeriodStep/_PeriodForm mirroring tests/test_alerts_step.py's
pattern: presentation is driven through the real ipyvuetify widgets, and
``AppState``/``Project`` are real, unmocked.
"""

from datetime import date, datetime

import ipyvuetify as v
import reacton
import solara
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus

from deforestation_alerts.engine.fingerprint import harm_key, raw_key
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, Artifact, Period, Project, add_predefined_source,
)
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.period_step import PeriodStep, apply_period, default_dates


def _p(name="d"):
    return Project(name=name, created_at=datetime.now(), updated_at=datetime.now())


def _p_with_source(name="d"):
    p = _p(name)
    add_predefined_source(p, "RADD")
    return p


# ---------------------------------------------------------------------------
# apply_period / default_dates (brief Step 1, verbatim)
# ---------------------------------------------------------------------------

def test_apply_creates_and_activates_the_period():
    p = _p()
    assert apply_period(p, "2026-01-01", "2026-01-31", 90) is None
    assert p.active_period == "2026-01-01_2026-01-31"
    assert p.periods[p.active_period].end == date(2026, 1, 31)


def test_editing_replaces_a_period_without_sets():
    p = _p()
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    assert apply_period(p, "2026-02-01", "2026-02-28", 30) is None
    assert list(p.periods) == ["2026-02-01_2026-02-28"] and p.look_back_days == 30


def test_apply_period_replaces_the_shared_containers_instead_of_mutating_them():
    """Every published snapshot shares the live project's ``periods`` and
    ``artifacts`` (``AppState`` publishes shallow copies): a render iterating
    them on another thread must not see them change size under the edit."""
    p = _p_with_source()
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    jan = p.active_period
    p.artifacts[raw_key(jan, "RADD")] = Artifact(key=raw_key(jan, "RADD"), kind="raw_alert", rel_path="r.tif",
                                               fingerprint="f", inputs={}, content_id="c", created_at=datetime.now())
    snapshot = p.model_copy()
    periods_being_iterated = iter(snapshot.periods.values())
    artifacts_being_iterated = iter(snapshot.artifacts.values())

    assert apply_period(p, "2026-02-01", "2026-02-28", 30) is None

    assert [per.id for per in periods_being_iterated] == [jan]  # no "dictionary changed size during iteration"
    assert len(list(artifacts_being_iterated)) == 1
    assert list(snapshot.periods) == [jan] and list(snapshot.artifacts) == [raw_key(jan, "RADD")]
    assert list(p.periods) == ["2026-02-01_2026-02-28"] and p.artifacts == {}


def test_validation_errors():
    p = _p()
    assert apply_period(p, "nope", "2026-01-31", 90) == "bad_date"
    assert apply_period(p, "2026-02-01", "2026-01-31", 90) == "order"
    assert apply_period(p, "2026-01-01", "2026-01-31", -1) == "bad_look_back"


def test_a_period_with_sets_is_locked():
    p = _p()
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    p.alert_sets["s"] = AlertSet(id="s", period_id=p.active_period, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", fingerprint="f", created_at=datetime.now())
    assert apply_period(p, "2026-02-01", "2026-02-28", 90) == "locked"


def test_default_dates():
    assert default_dates(_p(), date(2026, 9, 30)) == ("2026-09-01", "2026-09-30")
    p = _p()
    p.periods["x"] = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.active_period = "x"
    assert default_dates(p, date(2026, 9, 30)) == ("2026-01-01", "2026-01-31")


# ---------------------------------------------------------------------------
# apply_period: pre-flight ruling #69 (strict end > start, artifact cleanup)
# ---------------------------------------------------------------------------

def test_end_equal_to_start_is_an_order_error():
    """Spec §6.4: 'checks end > start' -- a same-day period is rejected, not
    silently accepted as a one-day window."""
    p = _p()
    assert apply_period(p, "2026-01-01", "2026-01-01", 90) == "order"


def test_replacing_a_period_drops_its_raw_and_harmonized_artifact_records():
    """Ruling #69: once a period is replaced (only possible while it has no
    sets), its raw/harmonized manifest records become unreachable (no period
    in p.periods points at that id any more) and must be dropped."""
    p = _p_with_source("RADD")
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    old_id = p.active_period
    p.artifacts[raw_key(old_id, "RADD")] = Artifact(
        key=raw_key(old_id, "RADD"), kind="raw_alert", rel_path="raw.tif", fingerprint="f",
        inputs={}, content_id="c1", created_at=datetime.now(),
    )
    p.artifacts[harm_key(old_id, "RADD")] = Artifact(
        key=harm_key(old_id, "RADD"), kind="harmonized", rel_path="harm.tif", fingerprint="f",
        inputs={}, content_id="c2", created_at=datetime.now(),
    )

    assert apply_period(p, "2026-02-01", "2026-02-28", 90) is None

    assert raw_key(old_id, "RADD") not in p.artifacts
    assert harm_key(old_id, "RADD") not in p.artifacts


def test_editing_the_same_dates_keeps_its_own_artifact_records():
    """Re-saving with the same start/end (e.g. only the look-back changes)
    must not treat the period as replaced -- its own records must survive."""
    p = _p_with_source("RADD")
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    pid = p.active_period
    p.artifacts[raw_key(pid, "RADD")] = Artifact(
        key=raw_key(pid, "RADD"), kind="raw_alert", rel_path="raw.tif", fingerprint="f",
        inputs={}, content_id="c1", created_at=datetime.now(),
    )

    assert apply_period(p, "2026-01-01", "2026-01-31", 30) is None

    assert raw_key(pid, "RADD") in p.artifacts
    assert p.look_back_days == 30


# ---------------------------------------------------------------------------
# PeriodStep / _PeriodForm: render-based coverage
# ---------------------------------------------------------------------------

@solara.component
def _Harness(app_state):
    # PeriodStep's use_notifications() needs a mounted provider rendered
    # first (pysepal 4 contract, see test_app_wiring.py).
    NotificationProvider()
    PeriodStep(app_state=app_state)


def _render(app_state):
    return reacton.render(_Harness(app_state=app_state), handle_error=False)


def _field(rc, label):
    """Locate a TextField by its label -- robust against the widgets' render
    order, which does not follow source order (the hinted look-back field
    renders before the plain start/end pair)."""
    return next(w for w in rc.find(v.TextField).widgets if w.label == label)


def test_locked_step_shows_no_sources_yet():
    state = AppState()
    state.new_project_state(_p())
    box, rc = _render(state)
    try:
        assert rc.find(v.TextField).widgets == []
    finally:
        rc.close()


def test_save_applies_the_period_and_notifies_success(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    state.new_project_state(_p_with_source())

    box, rc = _render(state)
    try:
        _field(rc, t("steps.period.start")).v_model = "2026-01-01"
        _field(rc, t("steps.period.end")).v_model = "2026-01-31"
        _field(rc, t("steps.period.look_back")).v_model = "30"

        save_btn = rc.find(v.Btn).widgets[-1]
        save_btn.fire_event("click", None)

        assert state.project.value.active_period == "2026-01-01_2026-01-31"
        assert state.project.value.look_back_days == 30
        bus = get_current_bus()
        successes = [toast for toast in bus.toasts.value if toast.type.value == "success"]
        assert successes and successes[-1].message == t("steps.period.saved")
    finally:
        rc.close()


def test_save_with_invalid_input_does_not_touch_the_project(monkeypatch, tmp_path):
    """Ruling #69: an invalid edit must not publish (no wasted autosave/
    re-render) and must not toast success."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    state.new_project_state(_p_with_source())
    before = state.project.value

    box, rc = _render(state)
    try:
        _field(rc, t("steps.period.start")).v_model = "2026-02-01"
        _field(rc, t("steps.period.end")).v_model = "2026-01-31"  # end before start

        save_btn = rc.find(v.Btn).widgets[-1]
        save_btn.fire_event("click", None)

        assert state.project.value is before  # untouched: no publish on invalid input
        bus = get_current_bus()
        errors = [toast for toast in bus.toasts.value if toast.type.value == "error"]
        assert len(errors) == 1
        assert errors[0].message == t("steps.period.error_order")
    finally:
        rc.close()


def test_locked_period_hides_save_and_disables_the_fields():
    state = AppState()
    p = _p_with_source()
    apply_period(p, "2026-01-01", "2026-01-31", 90)
    pid = p.active_period
    p.alert_sets["s"] = AlertSet(id="s", period_id=pid, sources=["RADD"], digit_map={1: "RADD"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", fingerprint="f", created_at=datetime.now())
    state.new_project_state(p)

    box, rc = _render(state)
    try:
        assert rc.find(v.Btn).widgets == []  # Save is hidden while locked
        assert _field(rc, t("steps.period.start")).disabled
        assert _field(rc, t("steps.period.end")).disabled
        assert _field(rc, t("steps.period.look_back")).disabled
    finally:
        rc.close()


def test_switching_projects_remounts_the_form_and_drops_unsaved_input(monkeypatch, tmp_path):
    """The Task 17 pre-flight's core finding: PeriodStep is mounted for the
    whole session (Task 22), so without a keyed remount the form would keep
    project A's typed-but-unsaved dates after switching to project B, and a
    later Save could write A's period into B."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    a = _p_with_source("a")
    state.new_project_state(a)

    box, rc = _render(state)
    try:
        _field(rc, t("steps.period.start")).v_model = "2020-01-01"
        _field(rc, t("steps.period.end")).v_model = "2020-01-31"

        b = _p_with_source("b")
        state.new_project_state(b)

        d_start, d_end = default_dates(b, date.today())
        assert _field(rc, t("steps.period.start")).v_model == d_start
        assert _field(rc, t("steps.period.end")).v_model == d_end
        assert _field(rc, t("steps.period.look_back")).v_model == str(b.look_back_days)
    finally:
        rc.close()
