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
  project whose grid is gone. The files the run swapped in stay on disk; the
  user is told to run again, and the previous record, if any, reads STALE.
- An empty set is still recorded, gets no tiles, and stays on the step with an
  info toast (review focus 1, spec §12).
"""

import logging
import math
from pathlib import Path
from typing import Optional

from deforestation_alerts.engine import pmtiles as pmt
from deforestation_alerts.engine.integrate import run_integration
from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import AlertSetParams
from gui.i18n import t
from gui.scripts import set_store
from gui.scripts.notify_bridge import tracked_job
from gui.store.project_writers import writing

logger = logging.getLogger("SEPALUI.deforestation_alerts")

ANALYSIS_VIEW = "analysis_tile"


def params_from_form(min_size, area_mode, drawn_geojson, sorting, max_alerts, attribute_config):
    """The form's values as ``(AlertSetParams, None)``, or ``(None, message key)``."""
    try:
        ha = float(str(min_size).strip())
    except (TypeError, ValueError):
        return None, "steps.integrate.error_min_size"
    if not math.isfinite(ha) or ha < 0:
        return None, "steps.integrate.error_min_size"
    try:
        cap = int(str(max_alerts or "0").strip())
    except (TypeError, ValueError):
        return None, "steps.integrate.error_max_alerts"
    if cap < 0:
        return None, "steps.integrate.error_max_alerts"
    if area_mode == "drawn" and not (drawn_geojson or {}).get("features"):
        return None, "steps.integrate.error_no_drawing"
    return AlertSetParams(
        min_size_ha=ha,
        area_mode=area_mode,
        area_geojson=drawn_geojson if area_mode == "drawn" else None,
        sorting=int(sorting),
        max_alerts=cap,
        attribute_config=attribute_config,
    ), None


def _digest(aoi) -> Optional[str]:
    return aoi.digest if aoi is not None else None


def _signature(grid) -> Optional[str]:
    return grid.signature if grid is not None else None


def changed_since(job, project) -> Optional[str]:
    """What ``project`` no longer shares with the project ``job`` ran on:
    ``"period"``, ``"aoi"`` or ``"grid"``, else None (the set may be recorded)."""
    if job.period.id not in project.periods:
        return "period"
    if _digest(project.aoi) != _digest(job.project.aoi):
        return "aoi"
    if _signature(project.grid) != _signature(job.project.grid):
        return "grid"
    return None


class _ProjectChanged(Exception):
    """Raised inside the ``update_project`` callback: nothing is recorded or
    published (``update_project`` publishes only when the callback returns)."""


def reviewed_in(project, set_id: str) -> int:
    """Reviewed alerts a re-run of ``set_id`` would discard (0 for a new set)."""
    s = project.alert_sets.get(set_id)
    return s.n_reviewed if s is not None else 0


def apply_result(project, result) -> None:
    """Record the set and its artifacts in ``project`` and make it the active set.

    The containers are replaced, not mutated: the published snapshots share
    them (``AppState`` publishes shallow copies), and a render iterating one
    on another thread would otherwise see it change size.
    """
    project.artifacts = {**project.artifacts, **result.artifacts}
    project.alert_sets = {**project.alert_sets, result.alert_set.id: result.alert_set}
    project.active_set = result.alert_set.id


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
            notifier.warning(t("steps.integrate.tiles_failed", error=str(exc) or exc.__class__.__name__))


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

    def _apply(own):
        changed = changed_since(job, own)
        if changed is not None:
            logger.warning("set %s is not recorded in project %s: its %s changed during the run",
                           result.alert_set.id, name, changed)
            return False  # a removed period would leave an orphan set; another AOI or grid is not this set's
        apply_result(own, result)

    try:
        io.update_saved_project(name, _apply, root=root)
    except Exception:  # noqa: BLE001 - the set's files are written; only the record is lost
        logger.exception("could not record set %s in project %s", result.alert_set.id, name)


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


def run_integration_job(app_state, notifier, job, *, navigate: bool = True) -> None:
    """The worker body: run the engine, build the tiles, record the set, and
    open Analysis when there is something to review (spec §7.4 step 9)."""
    name = job.project.name
    with writing(name), tracked_job(notifier, t("steps.integrate.job_title"), total_steps=5) as task:
        result = run_integration(job, on_step=lambda key: task.step(t(key)))
        # The store these alerts are is on disk now: a review Save of them
        # checks it is still that store (set_store.generation_changed).
        set_store.stamp_generation(result.alerts, paths.resolve(job.pdir, result.alert_set.store_rel_path))
        n = len(result.alerts)
        if n:
            task.step(t("steps.integrate.step_tiles"))
            _build_tiles(job, result, notifier)
        changed = []

        def _record(p):
            why = changed_since(job, p)
            if why is not None:
                changed.append(why)
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
            if notifier is not None:
                notifier.warning(t("steps.integrate.project_changed"))
            return
        if not _publish_alerts(app_state, name, result):
            return
        if notifier is not None:
            if n:
                notifier.success(t("steps.integrate.done", n=result.alert_set.n_alerts))
            else:
                notifier.info(t("steps.integrate.done_empty"))
        if navigate and n:
            app_state.current_page_view.value = ANALYSIS_VIEW
