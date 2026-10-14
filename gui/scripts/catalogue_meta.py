"""Preview windows of the alert sources (spec §6.2 "Show on map")."""

from datetime import date

from deforestation_alerts.engine.coverage import data_window, record_for


def preview_window(project, source) -> tuple:
    """The active period's search window; without one, the span the source's
    covering regions hold for this AOI; for an undated custom source, every date."""
    period = project.periods.get(project.active_period) if project.active_period else None
    if period is not None:
        return period.search_window()
    return data_window(record_for(project, source)) or (date(2000, 1, 1), date.today())
