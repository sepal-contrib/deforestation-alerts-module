"""Catalogue metadata (coverage end, GLAD-L band list) fetched once per session.

``use_catalogue_meta`` is the one loader every step that previews a predefined
source calls (ruling #68): it fetches what is still missing from
``app_state.catalogue_meta`` on a worker-free async task and reports a failure
as a toast, so the alerts and prepare steps share one fetch instead of each
running (and silently swallowing) its own.
"""

from datetime import date

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from gui.i18n import t


async def fetch_meta(gee_interface, keys) -> dict:
    out = {}
    for key in keys:
        out[key] = await gee_interface.get_info_async(catalogue.meta_request(key))
    return out


def coverage_text(key: str, meta) -> str:
    if not meta:
        return "…"
    first, last = catalogue.coverage(key, meta)
    return f"{first.isoformat()} → {last.isoformat()}"


def preview_window(project, meta, key) -> tuple[date, date]:
    period = project.periods.get(project.active_period) if project.active_period else None
    if period is not None:
        return period.search_window(project.look_back_days)
    # No active period yet: preview the whole catalogue (spec §6.2), not a
    # fixed look-back window.
    return catalogue.coverage(key, meta)


def use_catalogue_meta(app_state, gee_interface) -> dict:
    """The session-cached catalogue metadata, fetching whatever is missing.

    A hook so every step that needs it (alerts, prepare) shares one fetch and
    one cache instead of each running its own copy of the task.
    """
    notifications = use_notifications()
    p = app_state.project.value

    async def _load():
        missing = [k for k in catalogue.KEYS if k not in app_state.catalogue_meta.value]
        if missing:
            fetched = await fetch_meta(gee_interface, missing)
            app_state.catalogue_meta.set({**app_state.catalogue_meta.value, **fetched})

    # dependencies=[p is not None]: fetch once, the first time a project is
    # open; the catalogue is session-wide, not per-project, so a later
    # project switch must not refetch it.
    task = solara.lab.use_task(_load, dependencies=[p is not None], prefer_threaded=False, raise_error=False)

    def _on_error():
        if task.error:
            error = str(task.exception) or task.exception.__class__.__name__
            notifications.error(t("steps.alerts.meta_failed", error=error))

    solara.use_effect(_on_error, [task.pending, task.finished, task.error, task.cancelled])

    return app_state.catalogue_meta.value
