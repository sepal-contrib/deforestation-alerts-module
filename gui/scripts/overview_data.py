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

import csv
import io
import logging
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Optional

from deforestation_alerts.engine.fingerprint import combined_key, harm_key
from deforestation_alerts.project import grouping, review_groups
from deforestation_alerts.project.review_groups import area_crs
from deforestation_alerts.project.review_status import CONFIRMED, STATUS_CODES
from deforestation_alerts.project.questions import column
from deforestation_alerts.project.store_schema import BELOW_MIN, GROWS_FROM
from deforestation_alerts.scripts.set_summary import read_frame, read_summary, summarize_alerts
from gui.i18n import t
from gui.scripts import charts, set_store
from gui.scripts.legend_specs import STACK_ORDER, STATUS_CLASSES, status_colors

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview_data")

#: How many summaries the memo keeps. A project has a handful of periods and
#: the dashboard reads one summary per period plus the selected set, so this
#: holds a whole session's worth without growing with it.
MEMO_LIMIT = 32

CONFIRMED_CODE = STATUS_CODES[CONFIRMED]

_LABEL_KEYS = {code: key for code, _, key in STATUS_CLASSES}
_MEMO: "OrderedDict" = OrderedDict()


# --- reading --------------------------------------------------------------

def answer_columns(project) -> dict:
    """``{question key: store column}`` for the project's questions, in order."""
    return {q.key: column(q) for q in project.questions} if project is not None else {}


def summary_key(project, alert_set) -> Optional[tuple]:
    """What makes a cached summary stale, or None without a set.

    The set's fingerprint changes when Integrate rebuilds it, ``n_reviewed``
    when a review is saved, and the store's generation on any write at all --
    so a key that still matches names the same numbers. The questions are in
    it too (spec D9): which columns are read, and each question's options,
    which the split reads stored labels back to codes through. Both as
    strings, because a pydantic model does not hash.
    """
    if project is None or alert_set is None:
        return None
    return (project.name, alert_set.id, alert_set.fingerprint, alert_set.n_reviewed,
            set_store.store_generation(set_store.store_path(project, alert_set)),
            tuple(answer_columns(project).items()),
            tuple(q.model_dump_json(include={"options"}) for q in project.questions))


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


def _memo(key, compute):
    hit = _MEMO.get(key)
    if hit is not None:
        _MEMO.move_to_end(key)
        return hit
    value = compute()
    _MEMO[key] = value
    while len(_MEMO) > MEMO_LIMIT:
        _MEMO.popitem(last=False)
    return value


def summary_of(project, alert_set, *, split_of=None) -> object:
    """``alert_set``'s summary, memoized on :func:`summary_key`: one breakdown
    per project question, split by ``split_of(key, stored)`` (see
    ``set_summary.summarize_alerts``).

    Blocking (parquet): call it from a worker thread. It raises when the store
    cannot be read -- the dashboard then says the set's files are missing
    rather than drawing an empty axis over a set that has alerts.
    """
    period = project.periods.get(alert_set.period_id)
    return _memo(summary_key(project, alert_set), lambda: read_summary(
        set_store.store_path(project, alert_set), alert_set.id,
        period_start=period.start if period is not None else None,
        answer_columns=answer_columns(project), split_of=split_of,
    ))


#: The set selector's "All periods" entry, and the ``set_id`` of its summary.
ALL_PERIODS = "__all__"


def all_summary_of(project, *, split_of=None) -> object:
    """One summary of every period's set (``period_sets``) as if they were one
    set, memoized on ``history_keys``. A store that cannot be read is left out
    and logged, as in ``history_of``. Blocking (parquet): a worker's call."""
    import pandas as pd

    def _read():
        frames, starts = [], []
        for period, alert_set in period_sets(project):
            try:
                frames.append(read_frame(set_store.store_path(project, alert_set), answer_columns(project)))
            except Exception:  # noqa: BLE001 - one unreadable store, not every period
                logger.exception("All periods skips set %s: its store could not be read", alert_set.id)
                continue
            starts.append(period.start)
        frame = pd.concat(frames, ignore_index=True) if frames else None
        # shortcut: look-back is only what falls before the first period; an
        # alert in a later period's look-back weeks counts as in period
        return summarize_alerts(frame, set_id=ALL_PERIODS, period_start=min(starts) if starts else None,
                                answer_columns=answer_columns(project), split_of=split_of)

    return _memo((ALL_PERIODS, *history_keys(project)), _read)


def history_of(project, *, split_of=None) -> tuple:
    """``(period, alert_set, summary)`` per period, oldest first.

    A period whose store cannot be read is left out and logged: one deleted
    file must not cost the whole trend, and the period simply has no column.
    """
    out = []
    for period, alert_set in period_sets(project):
        try:
            out.append((period, alert_set, summary_of(project, alert_set, split_of=split_of)))
        except Exception:  # noqa: BLE001 - one unreadable store, not the whole history
            logger.exception("the Overview history skips set %s: its store could not be read", alert_set.id)
    return tuple(out)


def nearby_radius(project, alert_set) -> float:
    """The distance the *Nearby alerts* card chains ``alert_set``'s alerts
    at: the grouping in force for it (its split's record once split), else
    the project's setting, so that while grouping is off the card still says
    what it would do."""
    g = grouping.effective(project, alert_set)
    return g.radius_m if g is not None else project.group_review.radius_m


def nearby_of(project, alert_set, alerts):
    """The *Nearby alerts* card's numbers for ``alerts`` (``alert_set``'s, as
    loaded): ``review_groups.neighbourhood_counts`` at ``nearby_radius``, in
    the grid CRS a split measures in. Blocking: call it from a worker thread."""
    return review_groups.neighbourhood_counts(alerts, nearby_radius(project, alert_set), area_crs(project))


def sum_nearby(counts):
    """Several sets' *Nearby alerts* counts added up (All periods); the
    largest group is the largest of any set. None without any."""
    counts = [c for c in counts if c is not None]
    if not counts:
        return None
    return replace(counts[-1], **{f: sum(getattr(c, f) for c in counts)
                                  for f in ("n_alerts", "n_units", "n_alone", "n_clustered", "n_groups", "n_grouped")},
                   largest=max(c.largest for c in counts))


def forget_summaries() -> None:
    """Empty the memo. For tests; nothing in the app needs it, because every
    change that matters changes a key."""
    _MEMO.clear()


# --- pixels and clusters -------------------------------------------------

@dataclass(frozen=True)
class PixelCounts:
    """The Pixels and Clusters tabs' numbers for one set (or the sum of
    several), off the project's artifact records: no file is read. A count
    the set's run did not record (a set integrated before it was) is None."""

    pixel_ha: float  # one grid pixel, in ha
    sources: tuple  # (source, before masks, kept by them), None where not counted
    masks: tuple  # (name, mode)
    analysable_px: Optional[int]
    too_small_px: Optional[int]
    over_cap_px: Optional[int]
    clusters: Optional[int]  # connected clusters, before the size filter
    clusters_kept: Optional[int]  # at least the minimum size (or kept by a growth link)

    @property
    def combined_px(self) -> Optional[int]:
        """The sources' pixels once combined: an overlap counts once."""
        parts = (self.analysable_px, self.too_small_px, self.over_cap_px)
        return None if None in parts else sum(parts)

    def total(self, index: int) -> Optional[int]:
        """The sources' column ``index`` (1 before masks, 2 kept) summed; None if any is."""
        values = [row[index] for row in self.sources]
        return None if not values or None in values else sum(values)


def pixels_of(project, alert_set) -> PixelCounts:
    """``alert_set``'s ``PixelCounts``: each source's pixels before and after
    the masks (its harmonized file's stats), the masks it was built with, and
    what Integrate counted in its combined raster."""
    sources = []
    for name in alert_set.sources:
        art = project.artifacts.get(harm_key(alert_set.period_id, name))
        st = (art.stats or {}) if art is not None else {}
        sources.append((name, st.get("alert_pixels_unmasked"), st.get("alert_pixels")))
    art = project.artifacts.get(combined_key(alert_set.id))
    st = (art.stats or {}) if art is not None else {}
    res = float(project.grid.resolution) if project.grid is not None else 0.0
    return PixelCounts(
        pixel_ha=res * res / 10_000.0, sources=tuple(sources),
        masks=tuple((m.get("name", ""), m.get("mode", "")) for m in alert_set.masks_used),
        analysable_px=st.get("analysable_px"), too_small_px=st.get("too_small_px"),
        over_cap_px=st.get("over_cap_px"), clusters=st.get("clusters"), clusters_kept=st.get("clusters_kept"))


def _add(values):
    values = list(values)
    return None if not values or None in values else sum(values)


def sum_pixels(counts) -> Optional[PixelCounts]:
    """Several sets' ``PixelCounts`` added up (All periods): a source's rows
    by name, the masks by name; a count any set lacks is None."""
    counts = list(counts)
    if not counts:
        return None
    by_source, masks = {}, {}
    for c in counts:
        for name, before, kept in c.sources:
            by_source.setdefault(name, []).append((before, kept))
        masks.update(dict(c.masks))
    return PixelCounts(
        pixel_ha=counts[0].pixel_ha,
        sources=tuple((name, _add(b for b, _ in rows), _add(k for _, k in rows))
                      for name, rows in sorted(by_source.items(), key=lambda kv: kv[0].casefold())),
        masks=tuple(sorted(masks.items())),
        **{f: _add(getattr(c, f) for c in counts)
           for f in ("analysable_px", "too_small_px", "over_cap_px", "clusters", "clusters_kept")})


def linked_of(project, alert_set) -> int:
    """How many of ``alert_set``'s alerts are linked to earlier loss, off a
    two-column read of its store, memoized on its summary key. Blocking."""
    import pyarrow.parquet as pq

    from gui.scripts.growth_index import growth_counts

    def _read():
        path = set_store.store_path(project, alert_set)
        columns = [c for c in (GROWS_FROM, BELOW_MIN) if c in pq.read_schema(path).names]
        if GROWS_FROM not in columns:
            return 0
        g = growth_counts(pq.read_table(path, columns=columns).to_pandas())
        return g.n_growth + g.n_growth_small

    return _memo(("linked", *summary_key(project, alert_set)), _read)


@dataclass(frozen=True)
class PeriodRow:
    """One row of the Files tab's *Summary of all periods*."""

    period_id: str
    n_alerts: int
    confirmed_ha: float
    n_reviewed: int
    source_px: Optional[int]
    clusters: Optional[int]
    groups: int
    linked: Optional[int]


def period_rows(project, history) -> tuple:
    """A ``PeriodRow`` per ``history`` entry (``history_of``'s), oldest first.
    Reads each store's growth links (``linked_of``): a worker's call. A store
    whose links cannot be read gives None, not a failed table."""
    out = []
    for period, alert_set, summary in history:
        try:
            linked = linked_of(project, alert_set)
        except Exception:  # noqa: BLE001 - one cell, not the table
            logger.exception("could not count the linked alerts of set %s", alert_set.id)
            linked = None
        px = pixels_of(project, alert_set)
        out.append(PeriodRow(period_id=period.id, n_alerts=summary.n_alerts,
                             confirmed_ha=status_value(summary, CONFIRMED_CODE, charts.MEASURE_AREA),
                             n_reviewed=summary.n_reviewed, source_px=px.total(1), clusters=px.clusters,
                             groups=alert_set.n_groups, linked=linked))
    return tuple(out)


#: The summary CSV's file, in the project folder.
SUMMARY_CSV_NAME = "periods_summary.csv"
#: The summary CSV's columns, in order (English, like every other column name).
SUMMARY_CSV_COLUMNS = ("period_start", "period_end", "alerts", "confirmed_ha", "reviewed", "source_px",
                       "clusters", "review_groups", "linked_to_earlier_loss")


def summary_csv(project, rows) -> str:
    """The Summary CSV's text: a header and one line per ``PeriodRow``; an
    unrecorded count is an empty cell."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(SUMMARY_CSV_COLUMNS)
    for row in rows:
        period = project.periods.get(row.period_id)
        start, end = ((period.start.isoformat(), period.end.isoformat()) if period is not None
                      else row.period_id.partition("_")[::2])
        writer.writerow([start, end, row.n_alerts, f"{row.confirmed_ha:.2f}", row.n_reviewed,
                         *("" if v is None else v for v in (row.source_px, row.clusters)), row.groups,
                         "" if row.linked is None else row.linked])
    return out.getvalue()


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

#: The fixed figures every dashboard holds. ``draw_figures`` returns these
#: keys plus one ``"q:<key>"`` per question it is given.
FIGURES = ("status", "history", "sizes", "timing", "sources")


def draw_figures(summary, series, *, measure: str, theme: dict, label_of, questions=()) -> dict:
    """Every figure of one dashboard, as inline SVG, keyed by its card.

    ``series`` is ``chart_trend``'s ``(label, SetSummary, under_review)``,
    oldest first. ``questions`` get one answer chart each, keyed
    ``"q:<key>"``, whose bars ``label_of(question, code)`` names. A figure
    that raises is ``None`` in the result and logged: the card that owns it
    falls back to its numbers in text, and no other card is affected (spec,
    Error handling).
    """
    jobs = {
        "status": lambda: charts.chart_status(summary, measure=measure, theme=theme),
        "history": lambda: charts.chart_trend(series, measure=measure, theme=theme),
        "sizes": lambda: charts.chart_sizes(summary, measure=measure, theme=theme),
        "timing": lambda: charts.chart_timing(summary, measure=measure, theme=theme),
        "sources": lambda: charts.chart_sources(summary, measure=measure, theme=theme),
    }
    for q in questions:
        jobs[f"q:{q.key}"] = lambda q=q: charts.chart_question(
            summary, q.key, measure=measure, theme=theme, label_of=lambda code, q=q: label_of(q, code))
    out = {}
    for name, draw in jobs.items():
        try:
            out[name] = draw()
        except Exception:  # noqa: BLE001 - one card's numbers stand in for its figure
            logger.exception("the Overview's %s figure could not be drawn", name)
            out[name] = None
    return out
