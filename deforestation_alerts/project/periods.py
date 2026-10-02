"""Period rules (spec §5.1, §6.4), Solara-free.

- A new period pre-fills its start as the latest period's end plus one day and
  its end as today, needs ``end > start``, and starts with a copy of the mask
  choices of the period with the latest start (empty for the first one).
- A period's dates cannot change once it has sets. Changing them before that
  gives the period a new id (its nominal dates are its id); its downloads and
  harmonized files were made for the old window, so their records are dropped.
- A period can be deleted only when it has no sets, or when it is the latest
  period (by start), so no later set loses the footprint it was built from.
  Its sets go with it.

Every helper replaces each container it changes (spec §5.4: published
snapshots share them). The ``list[str]`` a helper returns are the
project-relative files of the dropped records; the caller deletes them, and
the period's folder (``cleanup.delete_period_dir``), once
``AppState.update_project`` returned True, outside the state lock.
"""

from datetime import date, timedelta
from typing import Optional

from deforestation_alerts.project.cleanup import drop_artifacts, period_artifact_keys, set_artifact_keys
from deforestation_alerts.project.models import AlertSet, Period, Project, latest_period_masks, period_id

#: error keys, translated as ``steps.period.error_<key>``
ERRORS = ("bad_date", "order", "exists", "locked", "not_latest", "bad_look_back")


def parse_dates(start, end) -> tuple[Optional[str], Optional[tuple[date, date]]]:
    try:
        a, b = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return "bad_date", None
    if b <= a:
        return "order", None
    return None, (a, b)


def sets_of(p: Project, pid: str) -> list[str]:
    return sorted(s.id for s in p.alert_sets.values() if s.period_id == pid)


def _order(period: Period):
    return (period.start, period.end)


def latest_period(p: Project) -> Optional[Period]:
    return max(p.periods.values(), key=_order) if p.periods else None


def periods_newest_first(p: Project) -> list[Period]:
    return sorted(p.periods.values(), key=_order, reverse=True)


def sets_newest_first(p: Project) -> list[AlertSet]:
    """The set selector's order (spec §9.3): the newest period first, then the
    newest run of it."""
    def _key(s):
        period = p.periods.get(s.period_id)
        return (period.start if period is not None else date.min, s.created_at)

    return sorted(p.alert_sets.values(), key=_key, reverse=True)


def prefill(p: Project, today: date) -> tuple[str, str]:
    latest = latest_period(p)
    start = latest.end + timedelta(days=1) if latest is not None else today.replace(day=1)
    return start.isoformat(), today.isoformat()


def new_period(p: Project, start, end) -> Optional[str]:
    error, dates = parse_dates(start, end)
    if error:
        return error
    a, b = dates
    pid = period_id(a, b)
    if pid in p.periods:
        return "exists"
    p.periods = {**p.periods, pid: Period(id=pid, start=a, end=b, masks=latest_period_masks(p))}
    p.active_period = pid
    return None


def edit_period(p: Project, pid: str, start, end) -> tuple[Optional[str], list[str]]:
    if sets_of(p, pid):
        return "locked", []
    error, dates = parse_dates(start, end)
    if error:
        return error, []
    a, b = dates
    new_id = period_id(a, b)
    if new_id == pid:
        return None, []
    if new_id in p.periods:
        return "exists", []
    gone = drop_artifacts(p, period_artifact_keys(p, pid))
    moved = p.periods[pid].model_copy(update={"id": new_id, "start": a, "end": b})
    p.periods = {**{k: v for k, v in p.periods.items() if k != pid}, new_id: moved}
    if p.active_period == pid:
        p.active_period = new_id
    return None, gone


def delete_error(p: Project, pid: str) -> Optional[str]:
    latest = latest_period(p)
    if sets_of(p, pid) and latest is not None and latest.id != pid:
        return "not_latest"
    return None


def delete_period(p: Project, pid: str) -> tuple[Optional[str], list[str]]:
    error = delete_error(p, pid)
    if error:
        return error, []
    removed = set(sets_of(p, pid))
    gone = drop_artifacts(p, period_artifact_keys(p, pid) + set_artifact_keys(p, removed))
    p.alert_sets = {k: s for k, s in p.alert_sets.items() if k not in removed}
    p.periods = {k: v for k, v in p.periods.items() if k != pid}
    if p.active_period == pid:
        latest = latest_period(p)
        p.active_period = latest.id if latest is not None else None
    if p.active_set in removed:
        newest = sets_newest_first(p)
        p.active_set = newest[0].id if newest else None
    return None, gone


def set_look_back(p: Project, value) -> Optional[str]:
    """Look-back is a project field (spec §6.4); changing it makes the raw
    downloads out of date through their window (spec §5.3)."""
    try:
        days = int(str(value).strip())
    except ValueError:
        return "bad_look_back"
    if days < 0:
        return "bad_look_back"
    p.look_back_days = days
    return None


def select_period(p: Project, pid: str) -> None:
    if pid in p.periods:
        p.active_period = pid
