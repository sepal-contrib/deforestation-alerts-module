"""What the Overview dashboard needs read, counted and drawn, Solara-free.

Two halves, both testable without a render harness:

* **Reading** -- ``summary_of`` and ``history_of`` turn a project's alert sets
  into ``SetSummary`` records. Both block on parquet, so a tile calls them from
  a worker thread (``spawn_in_context``); both are memoized on
  :func:`summary_key`, which carries the set's run, its review count and which
  write of its store is on disk, so a re-integrate or a review Save invalidates
  a summary by itself and nothing has to be persisted or invalidated by hand.
* **Drawing** -- ``draw_figures`` renders every figure of one dashboard in one
  call, catching each separately: a figure that raises comes back as ``None``
  and that single card falls back to its own numbers in text.

The memo is a process-global, like ``app_state`` and the job lists: a
single-user assumption this app already makes everywhere. Its keys name the
project and the exact bytes of the store, so a stale hit would need two
projects of the same name whose stores share an inode, size and mtime.
"""

import logging
from collections import OrderedDict
from typing import Optional

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts.set_summary import read_summary
from gui.i18n import t
from gui.scripts import charts, set_store
from gui.scripts.legend_specs import STACK_ORDER, STATUS_CLASSES, status_colors

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview_data")

#: How many summaries the memo keeps. A project has a handful of periods and
#: the dashboard reads one summary per period plus the selected set, so this
#: holds a whole session's worth without growing with it.
MEMO_LIMIT = 32

CONFIRMED = STATUS_CODES["Confirmed"]

_LABEL_KEYS = {code: key for code, _, key in STATUS_CLASSES}
_MEMO: "OrderedDict" = OrderedDict()


# --- reading --------------------------------------------------------------

def summary_key(project, alert_set) -> Optional[tuple]:
    """What makes a cached summary stale, or None without a set.

    The set's fingerprint changes when Integrate rebuilds it, ``n_reviewed``
    when a review is saved, and the store's generation on any write at all --
    so a key that still matches names the same numbers.
    """
    if project is None or alert_set is None:
        return None
    return (project.name, alert_set.id, alert_set.fingerprint, alert_set.n_reviewed,
            set_store.store_generation(set_store.store_path(project, alert_set)))


def period_sets(project) -> tuple:
    """``(period, alert_set)`` for every period that has a set, oldest period
    first; a period's newest run stands for it in the history."""
    if project is None:
        return ()
    out = []
    for period in sorted(project.periods.values(), key=lambda x: (x.start, x.end)):
        sets = [s for s in project.alert_sets.values() if s.period_id == period.id]
        if sets:
            out.append((period, max(sets, key=lambda s: s.created_at)))
    return tuple(out)


def history_keys(project) -> tuple:
    """The summary keys of the history, so a reader can watch all of them."""
    return tuple(summary_key(project, s) for _, s in period_sets(project))


def summary_of(project, alert_set, *, drivers_of=None) -> object:
    """``alert_set``'s summary, memoized on :func:`summary_key`.

    Blocking (parquet): call it from a worker thread. It raises when the store
    cannot be read -- the dashboard then says the set's files are missing
    rather than drawing an empty axis over a set that has alerts.
    """
    key = summary_key(project, alert_set)
    hit = _MEMO.get(key)
    if hit is not None:
        _MEMO.move_to_end(key)
        return hit
    period = project.periods.get(alert_set.period_id)
    summary = read_summary(
        set_store.store_path(project, alert_set), alert_set.id,
        period_start=period.start if period is not None else None, drivers_of=drivers_of,
    )
    _MEMO[key] = summary
    while len(_MEMO) > MEMO_LIMIT:
        _MEMO.popitem(last=False)
    return summary


def history_of(project, *, drivers_of=None) -> tuple:
    """``(period, alert_set, summary)`` per period, oldest first.

    A period whose store cannot be read is left out and logged: one deleted
    file must not cost the whole trend, and the period simply has no column.
    """
    out = []
    for period, alert_set in period_sets(project):
        try:
            out.append((period, alert_set, summary_of(project, alert_set, drivers_of=drivers_of)))
        except Exception:  # noqa: BLE001 - one unreadable store, not the whole history
            logger.exception("the Overview history skips set %s: its store could not be read", alert_set.id)
    return tuple(out)


def forget_summaries() -> None:
    """Empty the memo. For tests; nothing in the app needs it, because every
    change that matters changes a key."""
    _MEMO.clear()


# --- the numbers the cards quote -----------------------------------------

def total_of(summary, measure: str) -> float:
    """The whole set, in the measure on screen."""
    return float(summary.n_alerts) if measure == charts.MEASURE_ALERTS else float(summary.area_ha)


def status_value(summary, code: int, measure: str) -> float:
    """One review status of one set, in the measure on screen."""
    for breakdown in summary.by_status:
        if breakdown.code == code:
            return float(breakdown.n) if measure == charts.MEASURE_ALERTS else float(breakdown.area_ha)
    return 0.0


def previous_in(history, period_id) -> Optional[tuple]:
    """The history entry before the one for ``period_id``, or None when that
    period is the first (or is not in the history at all)."""
    ids = [period.id for period, _, _ in history]
    if period_id not in ids:
        return None
    spot = ids.index(period_id)
    return history[spot - 1] if spot else None


def table_rows(summary, measure: str) -> tuple:
    """``(code, colour, label, n, area_ha, median_ha, share)`` per status in
    ``STACK_ORDER``, then a total row whose ``code`` and ``median_ha`` are None.

    This table is the whole page's table view, so it always carries both the
    count and the area; only ``share`` follows the measure, so it agrees with
    the proportion bar above it.
    """
    colours = status_colors()
    found = {breakdown.code: breakdown for breakdown in summary.by_status}
    total = total_of(summary, measure)
    rows = []
    for code in STACK_ORDER:
        breakdown = found.get(code)
        n = int(breakdown.n) if breakdown is not None else 0
        area = float(breakdown.area_ha) if breakdown is not None else 0.0
        median = float(breakdown.median_ha) if breakdown is not None else 0.0
        value = float(n) if measure == charts.MEASURE_ALERTS else area
        rows.append((code, colours[code], t(_LABEL_KEYS[code]), n, area, median,
                     value / total if total > 0 else 0.0))
    rows.append((None, None, t("overview_tile.table_total"), int(summary.n_alerts),
                 float(summary.area_ha), None, 1.0 if total > 0 else 0.0))
    return tuple(rows)


# --- drawing --------------------------------------------------------------

#: The figures one dashboard holds. ``draw_figures`` returns this set of keys.
FIGURES = ("spark", "status", "history", "sizes", "timing", "drivers", "sources")


def draw_figures(summary, series, *, measure: str, theme: dict, label_of) -> dict:
    """Every figure of one dashboard, as inline SVG, keyed by its card.

    ``series`` is ``chart_trend``'s ``(label, SetSummary, under_review)``,
    oldest first. A figure that raises is ``None`` in the result and logged:
    the card that owns it falls back to its numbers in text, and no other card
    is affected (spec, Error handling).
    """
    spark = [status_value(s, CONFIRMED, measure) for _, s, _ in series]
    jobs = {
        "spark": lambda: charts.chart_spark(spark, theme=theme),
        "status": lambda: charts.chart_status(summary, measure=measure, theme=theme),
        "history": lambda: charts.chart_trend(series, measure=measure, theme=theme),
        "sizes": lambda: charts.chart_sizes(summary, measure=measure, theme=theme),
        "timing": lambda: charts.chart_timing(summary, measure=measure, theme=theme),
        "drivers": lambda: charts.chart_drivers(summary, measure=measure, theme=theme, label_of=label_of),
        "sources": lambda: charts.chart_sources(summary, measure=measure, theme=theme),
    }
    out = {}
    for name, draw in jobs.items():
        try:
            out[name] = draw()
        except Exception:  # noqa: BLE001 - one card's numbers stand in for its figure
            logger.exception("the Overview's %s figure could not be drawn", name)
            out[name] = None
    return out
