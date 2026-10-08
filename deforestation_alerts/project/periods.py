"""Period rules (spec §5.1, §6.4), Solara-free.

- A new period pre-fills its start as the latest period's end plus one day and
  its end as today, needs ``end > start``, and starts with a copy of the mask
  choices of the period with the latest start (empty for the first one).
- Each period has its own look-back (days before its start that the alert
  search covers); ``Project.look_back_days`` is the value a new period starts
  with, set to the last value used.
- A period's dates cannot change once it has sets (its look-back can). Changing them before that
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


def parse_look_back(value) -> tuple[Optional[str], Optional[int]]:
    try:
        days = int(str(value).strip())
    except ValueError:
        return "bad_look_back", None
    return ("bad_look_back", None) if days < 0 else (None, days)


def new_period(p: Project, start, end, look_back=None) -> Optional[str]:
    """``look_back`` None means the value a new period starts with
    (``p.look_back_days``); the value used is remembered there."""
    days = p.look_back_days
    if look_back is not None:
        error, days = parse_look_back(look_back)
        if error:
            return error
    error, dates = parse_dates(start, end)
    if error:
        return error
    a, b = dates
    pid = period_id(a, b)
    if pid in p.periods:
        return "exists"
    p.periods = {**p.periods, pid: Period(id=pid, start=a, end=b, look_back_days=days,
                                                 masks=latest_period_masks(p))}
    p.look_back_days = days
    p.active_period = pid
    return None


def edit_period(p: Project, pid: str, start, end, look_back=None) -> tuple[Optional[str], list[str]]:
    """A look-back-only change is allowed on a period with sets (its raw
    downloads then read out of date through their window, spec §5.3); changed
    dates are not."""
    days = None
    if look_back is not None:
        error, days = parse_look_back(look_back)
        if error:
            return error, []
    error, dates = parse_dates(start, end)
    if error:
        return error, []
    a, b = dates
    new_id = period_id(a, b)
    if new_id == pid:
        if days is not None:
            p.periods = {**p.periods, pid: p.periods[pid].model_copy(update={"look_back_days": days})}
            p.look_back_days = days
        return None, []
    if sets_of(p, pid):
        return "locked", []
    if new_id in p.periods:
        return "exists", []
    gone = drop_artifacts(p, period_artifact_keys(p, pid))
    update = {"id": new_id, "start": a, "end": b}
    if days is not None:
        update["look_back_days"] = days
        p.look_back_days = days
    moved = p.periods[pid].model_copy(update=update)
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


def select_period(p: Project, pid: str) -> None:
    if pid in p.periods:
        p.active_period = pid
