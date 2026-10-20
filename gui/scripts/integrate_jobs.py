"""Integrate & vectorize: form validation and the worker (spec §7.4).

The worker runs on a thread (``spawn_in_context``) as one tracked job, with
the project's folder marked as being written. ``run_integration`` builds the
set's files under staged names and swaps them in only when the whole run has
succeeded, so from then on the files on disk are the new set's and the worker
must record it, whatever happens next:

- The map tiles are built next. A tippecanoe or scratch failure only warns
  (spec §12's tippecanoe row); Overview rebuilds tiles older than the store.
  No pmtiles artifact is recorded (ruling #79).
- The set is recorded in the open project only while it is still the project
  the run started from (``AppState.update_project``, review focus 5). A run
  that finishes after its project was closed or switched records the set in
  that project's own saved manifest instead, never in the project open now
  (ruling #75), and touches nothing in the session.
- Either record is refused when the project's period, AOI or grid changed
  while the run was going (a project's first Integrate leaves all three
  editable): a set of a period that is gone would be an orphan that locks the
  project for good, and one built on another AOI or grid would freeze a
  project whose grid is gone. The user is told to run again. The files the
  run swapped in are deleted when no record names the set (its first run),
  with the set folder if nothing else is in it; otherwise they stay under the
  set's previous record, which reads STALE.
- An empty set is still recorded, gets no tiles, and stays on the step with an
  info toast (review focus 1, spec §12) that lists each source's outcome, the
  sources left out of the run included (spec §7.4).
"""

import contextlib
import logging
import math
import threading
from pathlib import Path
from typing import Optional

import pandas as pd

from deforestation_alerts.engine import pmtiles as pmt
from deforestation_alerts.engine.integrate import run_integration
from deforestation_alerts.project.review_status import STATUS_CODES
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.cleanup import delete_orphans
from deforestation_alerts.project.models import AlertSetParams
from deforestation_alerts.project.review_copy import refuse_if_read_only
from deforestation_alerts.project.models import set_id as make_set_id
from deforestation_alerts.project.review_status import reviewed_mask
from gui.i18n import format_number, t, tn
from gui.scripts import set_store
from gui.scripts.error_text import error_text
from gui.scripts.legend_specs import STATUS_CLASSES
from gui.scripts.notify_bridge import tracked_job
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_STATUS_KEYS = {code: key for code, _, key in STATUS_CLASSES}

#: ``{(project name, set id): runs}`` of the Integrate jobs running now. A
#: split or a merge of a set refuses to write its store while one is (spec
#: §4.4): the run swaps a new store in and records a new set, which would
#: drop what they wrote. ``project_writers`` counts every writer of a
#: project (a review Save too), so it cannot tell this.
_INTEGRATING: dict = {}
_INTEGRATING_LOCK = threading.Lock()


@contextlib.contextmanager
def integrating(project_name: str, set_id: str):
    """Mark an Integrate job of ``set_id`` in ``project_name`` as running
    for the duration of the block; always released."""
    key = (project_name, set_id)
    with _INTEGRATING_LOCK:
        _INTEGRATING[key] = _INTEGRATING.get(key, 0) + 1
    try:
        yield
    finally:
        with _INTEGRATING_LOCK:
            count = _INTEGRATING.get(key, 0) - 1
            if count > 0:
                _INTEGRATING[key] = count
            else:
                _INTEGRATING.pop(key, None)


def is_integrating(project_name: str, set_id: str) -> bool:
    """True while an Integrate job of ``set_id`` in ``project_name`` runs."""
    with _INTEGRATING_LOCK:
        return _INTEGRATING.get((project_name, set_id), 0) > 0


def max_alerts_error(text) -> Optional[str]:
    """The alert limit's message key, None when ``text`` is a whole number of
    1 or more: the field shows it as typed, Run checks it again."""
    s = str(text if text is not None else "").strip()
    return None if s.isascii() and s.isdigit() and int(s) >= 1 else "steps.integrate.error_max_alerts"


def params_from_form(min_size, sorting, max_alerts, attribute_layers, confirmed_only=()):
    """The form's values as ``(AlertSetParams, None)``, or ``(None, message key)``.

    ``max_alerts``: the limit as typed, or None for no limit (stored as 0).
    ``attribute_layers``: the attribute-layer configs in order (None: none). A
    new set writes them as ``attribute_layers``, never as the legacy
    ``attribute_config`` (spec D3). ``confirmed_only``: sources whose probable
    alerts are left out. The area is always the whole AOI.
    """
    try:
        ha = float(str(min_size).strip())
    except (TypeError, ValueError):
        return None, "steps.integrate.error_min_size"
    if not math.isfinite(ha) or ha < 0:
        return None, "steps.integrate.error_min_size"
    if max_alerts is not None and (err := max_alerts_error(max_alerts)):
        return None, err
    cap = 0 if max_alerts is None else int(str(max_alerts).strip())
    return AlertSetParams(
        min_size_ha=ha,
        sorting=int(sorting),
        max_alerts=cap,
        attribute_config=None,
        attribute_layers=list(attribute_layers or []),
        confirmed_only=list(confirmed_only or []),
    ), None


def _digest(aoi) -> Optional[str]:
    return aoi.digest if aoi is not None else None


def _signature(grid) -> Optional[str]:
    return grid.signature if grid is not None else None


def run_scope(project) -> tuple:
    """What a job's results depend on besides its period: ``(AOI digest,
    grid signature)``, read when the job starts."""
    if project is None:
        return None, None
    return _digest(project.aoi), _signature(project.grid)


def scope_changed(project, period_id: str, scope: tuple) -> Optional[str]:
    """What ``project`` no longer shares with a job started on ``period_id``
    and ``scope`` (``run_scope``): ``"period"``, ``"aoi"`` or ``"grid"``, else
    None (the job's results may be recorded). Both job layers use it
    (Integrate and Download & harmonize)."""
    if period_id not in project.periods:
        return "period"
    digest, signature = run_scope(project)
    if digest != scope[0]:
        return "aoi"
    if signature != scope[1]:
        return "grid"
    return None


def changed_since(job, project) -> Optional[str]:
    """What ``project`` no longer shares with the project ``job`` ran on:
    ``"period"``, ``"aoi"`` or ``"grid"``, else None (the set may be recorded)."""
    return scope_changed(project, job.period.id, run_scope(job.project))


class _ProjectChanged(Exception):
    """Raised inside the ``update_project`` callback: nothing is recorded or
    published (``update_project`` publishes only when the callback returns)."""


def reviewed_in(project, set_id: str) -> int:
    """Reviewed alerts a re-run of ``set_id`` would discard (0 for a new set)."""
    s = project.alert_sets.get(set_id)
    return s.n_reviewed if s is not None else 0


def split_in(project, set_id: str) -> bool:
    """Whether a re-run of ``set_id`` would discard an evaluator split."""
    s = project.alert_sets.get(set_id)
    return s is not None and s.split is not None


def replace_message(project, set_id: str) -> Optional[str]:
    """The Replace confirmation's message (spec §3): one sentence for the
    reviewed alerts and one for the evaluator split a re-run of ``set_id``
    would discard, each when it applies; None when nothing would be lost
    (the run starts without asking)."""
    parts = []
    reviewed = reviewed_in(project, set_id)
    if reviewed:
        parts.append(tn("steps.integrate.replace_message", reviewed))
    if split_in(project, set_id):
        parts.append(t("steps.integrate.replace_split"))
    return " ".join(parts) or None


def status_label(status) -> str:
    """A review status in the legend's words (spec §9.4: "Reviewed: confirmed")."""
    return t(_STATUS_KEYS[STATUS_CODES.get(status, 0)])


def reviewed_lines(project, set_id: str, limit: int = 20) -> list:
    """The reviewed alerts a re-run of ``set_id`` would discard (spec §7.4),
    read from the set's store on disk: one line each, at most ``limit``, then
    "… and N more". No lines when the set has no readable store: the
    confirmation's message still gives the count (``reviewed_in``)."""
    s = project.alert_sets.get(set_id)
    if s is None:
        return []
    store = paths.resolve(paths.project_dir(project.name), s.store_rel_path)
    try:
        df = pd.read_parquet(store, columns=["alert_id", "status", "area_ha"])
    except Exception:  # noqa: BLE001 - a missing or unreadable store only loses the list
        logger.warning("could not list the reviewed alerts of set %s from %s", set_id, store, exc_info=True)
        return []
    reviewed = df[reviewed_mask(df)]
    lines = [t("steps.integrate.replace_line", id=int(row.alert_id), status=status_label(row.status),
               area=format_number(float(row.area_ha), 2))
             for row in reviewed.head(limit).itertuples(index=False)]
    if len(reviewed) > limit:
        lines.append(t("steps.integrate.replace_more", n=format_number(len(reviewed) - limit)))
    return lines


def apply_result(project, result) -> None:
    """Record the set and its artifacts in ``project`` and make it the active set.

    The containers are replaced, not mutated: the published snapshots share
    them (``AppState`` publishes shallow copies), and a render iterating one
    on another thread would otherwise see it change size.
    """
    project.artifacts = {**project.artifacts, **result.artifacts}
    project.alert_sets = {**project.alert_sets, result.alert_set.id: result.alert_set}
    project.active_set = result.alert_set.id


def _orphan_files(job, result, project) -> list:
    """The files a refused run swapped in, when no record of ``project``
    names its set: its artifacts and its tiles. None when the set has a
    previous record (those files are that record's paths)."""
    if result.alert_set.id in project.alert_sets:
        return []
    store = paths.resolve(job.pdir, result.alert_set.store_rel_path)
    return [a.rel_path for a in result.artifacts.values()] + [paths.rel(job.pdir, paths.pmtiles_path(store.parent))]


def _build_tiles(job, result, notifier) -> None:
    """Build the set's Overview tiles next to its store; a failure only warns."""
    if not pmt.tippecanoe_available():
        if notifier is not None:
            notifier.warning(t("steps.integrate.no_tippecanoe"))
        return
    store = paths.resolve(job.pdir, result.alert_set.store_rel_path)
    try:
        pmt.alerts_to_pmtiles(result.alerts, paths.pmtiles_path(store.parent))
    except Exception as exc:  # noqa: BLE001 - the set's files are in place: it must still be recorded
        logger.exception("could not build the map tiles of set %s", result.alert_set.id)
        if notifier is not None:
            notifier.warning(t("steps.integrate.tiles_failed", error=error_text(exc)))


def _record_in_own_project(job, result) -> None:
    """Record a late set in its own project's saved manifest, under the
    project's manifest lock (``io.update_saved_project``).

    Called under the job's ``writing(name)`` once ``update_project`` refused
    the set because that project is no longer open. The set's files were
    already replaced in that project's folder: without the record, its
    manifest would keep the old content ids over new bytes (spec §5.3). A
    failure is logged and never fails the job.
    """
    name = job.project.name
    root = Path(job.pdir).parent
    orphans = []

    def _apply(own):
        changed = changed_since(job, own)
        if changed is not None:
            logger.warning("set %s is not recorded in project %s: its %s changed during the run",
                           result.alert_set.id, name, changed)
            orphans.extend(_orphan_files(job, result, own))
            return False  # a removed period would leave an orphan set; another AOI or grid is not this set's
        apply_result(own, result)

    try:
        io.update_saved_project(name, _apply, root=root)
    except Exception:  # noqa: BLE001 - the set's files are written; only the record is lost
        logger.exception("could not record set %s in project %s", result.alert_set.id, name)
    delete_orphans(job.pdir, orphans)


def _publish_alerts(app_state, name: str, result) -> bool:
    """Hand the new set's alerts to Analysis while it is still the open, active set.

    ``actual_alert_id`` goes to -1 first, so Analysis's auto-select picks the
    first alert as a real change even when alert 0 of the replaced set was
    showing (ruling #76). Under ``AppState``'s lock, as the shell loader's
    publish of the same set (``set_store.publish_set_alerts``).
    """
    with app_state._lock:
        live = app_state.project.value
        if live is None or live.name != name or live.active_set != result.alert_set.id:
            return False  # switched or closed right after the record
        app_state.actual_alert_id.set(-1)
        app_state.alerts_gdf.set(result.alerts)
        return True


def outcome_lines(outcomes: dict, left_out: dict, confirmed_only=()) -> list:
    """What each source gave an empty set (spec §7.4), by source name, then
    the sources left out of the run (not covered, or no data for the period).

    ``outcomes`` is ``IntegrationResult.outcomes``: a source's alert pixels
    with and without the masks, ``{}`` when its file was never counted.
    A source in ``confirmed_only`` gets a further line: its probable alerts
    were left out, which may be why nothing is left.
    """
    lines = []
    for source in sorted(outcomes, key=str.casefold):
        st = outcomes[source] or {}
        kept, before = int(st.get("alert_pixels", 0)), int(st.get("alert_pixels_unmasked", 0))
        if kept:
            lines.append(tn("steps.integrate.outcome_pixels", kept, source=source))
        elif before:
            lines.append(tn("steps.integrate.outcome_masked", before, source=source))
        else:
            lines.append(t("steps.integrate.outcome_none", source=source))
        if source in (confirmed_only or ()):
            lines.append(t("steps.integrate.outcome_confirmed_only", source=source))
    lines += [_left_out_line(s, (left_out or {})[s]) for s in sorted(left_out or {}, key=str.casefold)]
    return lines


def _left_out_line(source: str, reason: str) -> str:
    """Why ``source`` was left out: its coverage note, which mostly names the
    source already ("RADD has data until ..."), so no data never reads as
    "not covered"; a reason without one is prefixed by the source."""
    if not reason:
        return t("steps.integrate.outcome_left_out", source=source)
    if source in reason:
        return reason
    return t("steps.integrate.outcome_left_out_reason", source=source, reason=reason)


def _empty_message(result, left_out) -> str:
    lines = outcome_lines(result.outcomes, left_out, result.alert_set.params.confirmed_only)
    if not lines:
        return t("steps.integrate.done_empty")
    return t("steps.integrate.done_empty_detail", details="; ".join(lines))


def _done_message(result) -> str:
    """The toast of a run that found alerts: how many, and how many of them
    were linked to earlier loss (spec 2026-10-20 §4.6)."""
    lines = [tn("steps.integrate.done", result.alert_set.n_alerts)]
    if result.n_growth:
        lines.append(tn("steps.integrate.growth_linked", result.n_growth, small=result.n_growth_small))
    return ". ".join(lines)


def run_integration_job(app_state, notifier, job, *, left_out: Optional[dict] = None) -> None:
    """The worker body: run the engine, build the tiles and record the set.

    The user stays on the step (bug 2026-10-16): the new set is the active
    one, and the Questionnaire and Evaluation steps come before Analysis.

    ``left_out``: the period's sources the form showed disabled, by name,
    with the reason; an empty set's toast names them. Raises
    ``ReadOnlyProject``, running nothing, for a review copy (spec §6.3).
    """
    refuse_if_read_only(job.project)
    name = job.project.name
    sid = make_set_id(job.period.id, job.sources)
    with writing(name), integrating(name, sid), \
            tracked_job(notifier, t("steps.integrate.job_title"), total_steps=5) as task:
        result = run_integration(job, on_step=lambda key: task.step(t(key)))
        # The store these alerts are is on disk now: a review Save of them
        # checks it is still that store (set_store.generation_changed).
        set_store.stamp_generation(result.alerts, paths.resolve(job.pdir, result.alert_set.store_rel_path))
        n = len(result.alerts)
        if n:
            task.step(t("steps.integrate.step_tiles"))
            _build_tiles(job, result, notifier)
        changed, orphans = [], []

        def _record(p):
            why = changed_since(job, p)
            if why is not None:
                changed.append(why)
                orphans.extend(_orphan_files(job, result, p))
                raise _ProjectChanged(why)
            apply_result(p, result)

        try:
            recorded = app_state.update_project(name, _record)
        except _ProjectChanged:
            recorded = True  # the project is still open: nothing else to record into
        if not recorded:
            _record_in_own_project(job, result)
            return
        if changed:
            logger.warning("set %s is not recorded in project %s: its %s changed during the run",
                           result.alert_set.id, name, changed[0])
            delete_orphans(job.pdir, orphans)  # still under writing(name)
            if notifier is not None:
                notifier.warning(t("steps.integrate.project_changed"))
            return
        if not _publish_alerts(app_state, name, result):
            return
        if notifier is not None:
            if n:
                notifier.success(_done_message(result))
            else:
                notifier.info(_empty_message(result, left_out))
