"""Helpers over the catalogue metadata in ``app_state.catalogue_meta``.

``gui.scripts.remote_facts.RemoteFactsLoader`` fetches it (GLAD-L's band
list, once per session); the steps only read it.
"""

from datetime import date

from deforestation_alerts.engine import catalogue


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
