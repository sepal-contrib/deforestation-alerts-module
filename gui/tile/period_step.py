"""Period step (spec §6.4), single-period form for slice 1.

Slice 5 turns this into a list of periods; slice 1 has exactly one, whose
dates and look-back are editable until it has an alert set (D10-style lock,
via the period's own sets).
"""

from datetime import date
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import harm_key, raw_key
from deforestation_alerts.project.models import Period, period_id
from gui.i18n import t
from gui.store import workflow_steps


def _has_sets(p, pid) -> bool:
    return any(s.period_id == pid for s in p.alert_sets.values())


def _drop_period_artifacts(p, old_period_id: str) -> None:
    """The replaced period's raw/harmonized manifest records are unreachable
    once its id is gone from ``p.periods`` (ruling #69 on the Task 17
    pre-flight): drop them so the manifest doesn't accumulate orphans. The
    files themselves stay on disk (nothing downstream references them, and a
    project can only reach this branch while the period has no alert set).
    The dict is replaced, not mutated: the published snapshots share it."""
    gone = set()
    for source in p.sources:
        gone.update((raw_key(old_period_id, source), harm_key(old_period_id, source)))
    p.artifacts = {k: a for k, a in p.artifacts.items() if k not in gone}


def apply_period(p, start: str, end: str, look_back) -> Optional[str]:
    """Validate and, only on success, mutate ``p`` in place. Returns an error
    key (``bad_date``, ``order``, ``bad_look_back``, ``locked``) or None."""
    try:
        a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return "bad_date"
    if b <= a:  # spec §6.4: "checks end > start"
        return "order"
    try:
        lb = int(look_back)
    except (TypeError, ValueError):
        return "bad_look_back"
    if lb < 0:
        return "bad_look_back"
    new_id = period_id(a, b)
    current = p.active_period
    if current and current != new_id and _has_sets(p, current):
        return "locked"
    periods = dict(p.periods)  # replaced, not mutated: the published snapshots share it
    if current and current != new_id:
        periods.pop(current, None)
        _drop_period_artifacts(p, current)
    periods[new_id] = Period(id=new_id, start=a, end=b)
    p.periods = periods
    p.active_period = new_id
    p.look_back_days = lb
    return None


def default_dates(p, today: date) -> tuple:
    period = workflow_steps.active_period(p)
    if period is not None:
        return period.start.isoformat(), period.end.isoformat()
    return today.replace(day=1).isoformat(), today.isoformat()


@solara.component
def PeriodStep(app_state):
    """Hook-free gate (Task 17 pre-flight ruling #17): every hook this step
    needs lives in the keyed child below, never here, so the early return
    never skips a hook that a later render would call."""
    p = app_state.project.value
    if p is None or not p.sources:
        solara.Info(t("workflow.lock_period"))
        return
    _PeriodForm(app_state).key(f"period-{p.name}-{app_state.project_loaded_signal.value}")


@solara.component
def _PeriodForm(app_state):
    """Holds the form's local state. Keyed by project name + the load signal
    at the call site, so opening, reloading or switching projects remounts
    it with fresh state instead of keeping a previous project's unsaved
    dates and look-back (ruling #17: PeriodStep is mounted for the whole
    session, so without this a stale Save could write one project's period
    into another)."""
    notifications = use_notifications()
    p = app_state.project.value
    d_start, d_end = default_dates(p, date.today())
    start, set_start = solara.use_state(d_start)
    end, set_end = solara.use_state(d_end)
    look_back, set_look_back = solara.use_state(str(p.look_back_days) if p is not None else "")
    if p is None:
        # The project was closed: this form reads it itself, so it can render
        # once more before the gate above unmounts it. Every hook ran already.
        # Reading the closed project here raised out of close_project_state.
        return
    locked = bool(p.active_period) and _has_sets(p, p.active_period)

    def _save():
        # Validate on a deep copy first (ruling #69): an invalid edit must
        # neither mutate the live project nor publish/autosave. `deep=True`
        # matters here -- a shallow copy shares the `periods`/`artifacts`
        # dicts with the live project, so apply_period's in-place pop/assign
        # would corrupt the live project even while only "checking".
        trial_error = apply_period(p.model_copy(deep=True), start, end, look_back)
        if trial_error:
            notifications.error(t(f"steps.period.error_{trial_error}"))
            return

        errors = []

        def _apply(live):
            errors.append(apply_period(live, start, end, look_back))

        if not app_state.update_project(p.name, _apply):
            return  # the project was closed or switched meanwhile
        if errors[0]:
            notifications.error(t(f"steps.period.error_{errors[0]}"))
        else:
            notifications.success(t("steps.period.saved"))

    period = workflow_steps.active_period(p)
    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.period.title')}")
        solara.Text(t("steps.period.description"), classes=["text--secondary"])
        with solara.Row(style="gap:8px;"):
            rv.TextField(label=t("steps.period.start"), type="date", v_model=start, on_v_model=set_start,
                         dense=True, outlined=True, disabled=locked)
            rv.TextField(label=t("steps.period.end"), type="date", v_model=end, on_v_model=set_end,
                         dense=True, outlined=True, disabled=locked)
        rv.TextField(label=t("steps.period.look_back"), type="number", v_model=look_back,
                     on_v_model=set_look_back, dense=True, outlined=True, disabled=locked,
                     hint=t("steps.period.look_back_hint"), persistent_hint=True)
        if period is not None:
            ws, we = period.search_window(p.look_back_days)
            solara.Text(t("steps.period.window", start=ws.isoformat(), end=we.isoformat()),
                        classes=["text--secondary"])
        if locked:
            solara.Text(t("steps.period.locked"), classes=["text--secondary"])
        else:
            solara.Button(t("steps.period.save"), color="primary", small=True, on_click=_save)
