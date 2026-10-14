"""Everything the Overview dashboard counts, read from a set's alert store.

Solara-free and ``gui``-free (architecture contract #7): ``summarize_alerts``
takes a DataFrame, so every branch is unit-testable without a store, a project
or a render harness, and ``read_summary`` only adds the projected parquet read.

Two details of the store shape drive this module:

* ``alert_date_min`` is a **decimal year** (``year + doy/1000``, written by
  ``engine/vectorize.yyyyddd_to_decimal``), not a date.
* the answer columns (one per question, spec D9) and ``admin1`` are written
  per alert **during Analysis review**, so ``by_question`` and ``by_admin``
  cover reviewed alerts only. An answer holds several comma-joined codes, and
  an alert's full area counts once under each code it names: splitting it
  between the answers would invent a number the review never recorded.

Which columns are read is the caller's ``answer_columns`` (``{question key:
store column}``), and answers are split by a caller-supplied ``split_of`` (the
GUI passes ``gui.scripts.questions.codes`` of the question, which also reads
legacy label stores back to codes) because this package must never import
from ``gui/``. A question whose column the store lacks reads as unanswered.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from deforestation_alerts.project.review_status import CONFIRMED, NOT_REVIEWED, STATUS_CODES
from deforestation_alerts.scripts.alert_codes import parse_formatted_string

#: The only columns the dashboard reads. A projected parquet read of these is
#: ~36x cheaper than loading the geometry too, which is what makes recomputing
#: on every set switch cheaper than caching the result.
#: The answer columns are read on top of these, as the caller asks.
SUMMARY_COLUMNS = ["alert_id", "status", "area_ha", "alert_date_min",
                   "alert_sources", "admin1"]

#: Upper edges of the size histogram, in hectares; the last bin is open.
SIZE_EDGES = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0)

#: A reviewed alert that recorded no answer to a question (any question, not
#: only Loss driver: the name predates the questionnaire).
UNKNOWN_DRIVER = "unknown"
#: A reviewed alert whose ``admin1`` was never written. Empty on purpose: the
#: label is a translation, and this module resolves none.
UNATTRIBUTED = ""
#: An alert whose ``alert_sources`` is empty.
UNKNOWN_SOURCE = ""

_NOT_REVIEWED = STATUS_CODES[NOT_REVIEWED]
_CONFIRMED = STATUS_CODES[CONFIRMED]
_SEP = ", "  # gui.scripts.questions.SEP


@dataclass(frozen=True)
class StatusBreakdown:
    """One review status's share of a set."""

    code: int
    n: int
    area_ha: float
    median_ha: float


@dataclass(frozen=True)
class SetSummary:
    """Everything the dashboard draws for one alert set.

    ``by_status`` is in status-code order and always carries one entry per code,
    so a chart or a table can index it without checking. The stacking order that
    keeps Confirmed and False positive apart is a palette decision and lives in
    ``gui.scripts.legend_specs.STACK_ORDER``; the charts apply it.
    """

    set_id: str
    n_alerts: int
    area_ha: float
    n_reviewed: int
    by_status: tuple
    size_bins: tuple          # (lo, hi, n, area_ha)
    by_week: tuple            # (Monday, area_ha, n, in_period)
    by_question: tuple        # ((key, ((code, area_ha, n), ...)), ...), in question order;
    #                           each breakdown area-descending
    by_source: tuple          # (combination, area_ha, n), area-descending
    by_admin: tuple           # (admin1, area_ha, confirmed_ha, n), confirmed-descending
    largest: Optional[tuple]  # (alert_id, area_ha, admin1, date | None)

    def breakdown(self, key: str) -> tuple:
        """The ``(code, area_ha, n)`` rows of question ``key``; ``()`` for a
        key that was not asked for."""
        return next((rows for k, rows in self.by_question if k == key), ())


def decimal_year_to_date(value) -> Optional[date]:
    """The calendar date of a decimal year, or ``None`` when unusable.

    ``engine/vectorize.yyyyddd_to_decimal`` stores ``year + doy/1000``, so the
    fraction is the day of year over 1000 -- never over 365.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or not 1900.0 <= v <= 2200.0:  # NaN, 0, or a sentinel
        return None
    year = int(v)
    doy = max(1, int(round((v - year) * 1000)))
    try:
        return date(year, 1, 1) + timedelta(days=doy - 1)
    except (ValueError, OverflowError):
        return None


def summarize_alerts(frame, *, set_id: str = "", period_start: Optional[date] = None,
                     answer_columns: Optional[dict] = None,
                     split_of: Optional[Callable] = None) -> SetSummary:
    """Summarise an alert frame. Never raises on a missing column or value.

    Parameters
    ----------
    frame
        The alerts, or ``None``. Only ``SUMMARY_COLUMNS`` and the answer
        columns are read; a column that is absent is treated as empty.
    set_id
        Carried onto the summary, so a stale one is recognisable.
    period_start
        The active period's first day. Weeks before the week it falls in are
        marked ``in_period=False``: they are look-back alerts, and the chart
        de-emphasises them so they cannot read as this period's own signal.
    answer_columns
        ``{question key: store column}``, in question order: one breakdown
        per entry, in that order. None or empty means no breakdown.
    split_of
        ``(key, stored answer)`` -> list of codes. Defaults to a plain split
        on ``", "``; the GUI passes the question's ``questions.codes``.
    """
    if frame is None or len(frame) == 0:
        return _zeroed(set_id)

    split = split_of or (lambda _key, value: _split_answer(value))
    n = len(frame)
    area = _area(frame, n)
    code = _code(frame, n)
    reviewed = [c != _NOT_REVIEWED for c in code]

    return SetSummary(
        set_id=set_id,
        n_alerts=int(n),
        area_ha=float(sum(area)),
        n_reviewed=int(sum(reviewed)),
        by_status=_by_status(area, code),
        size_bins=_size_bins(area),
        by_week=_by_week(_column(frame, "alert_date_min", n), area, period_start),
        by_question=tuple((key, _by_answer(_column(frame, col, n), area, reviewed,
                                           lambda v, k=key: split(k, v)))
                          for key, col in (answer_columns or {}).items()),
        by_source=_by_source(_column(frame, "alert_sources", n), area),
        by_admin=_by_admin(_column(frame, "admin1", n), area, code, reviewed),
        largest=_largest(frame, area, n),
    )


def read_summary(store_path, set_id: str, *, period_start: Optional[date] = None,
                 answer_columns: Optional[dict] = None,
                 split_of: Optional[Callable] = None) -> SetSummary:
    """``summarize_alerts`` of a set's store, read column-projected.

    Blocking (parquet): call it from a worker thread. It raises when the store
    cannot be read -- the dashboard then shows its "files are missing" text and
    no charts, rather than a broken axis.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    names = set(pq.read_schema(store_path).names)
    wanted = SUMMARY_COLUMNS + list((answer_columns or {}).values())
    columns = [c for c in dict.fromkeys(wanted) if c in names]
    frame = pd.read_parquet(store_path, columns=columns) if columns else pd.DataFrame()
    return summarize_alerts(frame, set_id=set_id, period_start=period_start,
                            answer_columns=answer_columns, split_of=split_of)


# ---- internals ------------------------------------------------------------


def _zeroed(set_id: str) -> SetSummary:
    return SetSummary(set_id=set_id, n_alerts=0, area_ha=0.0, n_reviewed=0,
                      by_status=tuple(StatusBreakdown(code=c, n=0, area_ha=0.0, median_ha=0.0)
                                      for c in sorted(set(STATUS_CODES.values()))),
                      size_bins=(), by_week=(), by_question=(), by_source=(), by_admin=(), largest=None)


def _text(value) -> str:
    """A cell as text: ``""`` for None, NaN or NaT."""
    import pandas as pd

    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            if pd.isna(value):
                return ""
        except (TypeError, ValueError):
            pass
    return str(value)


def _split_answer(value) -> list:
    return [part.strip() for part in _text(value).split(_SEP) if part.strip()]


def _column(frame, name: str, n: int) -> list:
    return list(frame[name]) if name in frame.columns else [None] * n


def _area(frame, n: int) -> list:
    """``area_ha`` as non-negative floats; anything unusable counts as zero."""
    out = []
    for value in _column(frame, "area_ha", n):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        out.append(v if v == v and v > 0.0 else 0.0)
    return out


def _code(frame, n: int) -> list:
    """``status`` as codes; an unknown or missing status falls to 0."""
    return [STATUS_CODES.get(_text(value), _NOT_REVIEWED) for value in _column(frame, "status", n)]


def _median(values: list) -> float:
    from statistics import median

    return float(median(values)) if values else 0.0


def _by_status(area: list, code: list) -> tuple:
    out = []
    for c in sorted(set(STATUS_CODES.values())):
        mine = [a for a, k in zip(area, code) if k == c]
        out.append(StatusBreakdown(code=c, n=len(mine), area_ha=float(sum(mine)), median_ha=_median(mine)))
    return tuple(out)


def _size_bins(area: list) -> tuple:
    edges = (0.0,) + tuple(SIZE_EDGES) + (float("inf"),)
    out = []
    for lo, hi in zip(edges, edges[1:]):
        mine = [a for a in area if lo <= a < hi]
        out.append((float(lo), float(hi), len(mine), float(sum(mine))))
    return tuple(out)


def _by_week(dates: list, area: list, period_start: Optional[date]) -> tuple:
    weeks = {}
    for value, a in zip(dates, area):
        d = decimal_year_to_date(value)
        if d is None:
            continue  # a source harmonized without a date band: skipped, not fatal
        monday = d - timedelta(days=d.weekday())
        ha, n = weeks.get(monday, (0.0, 0))
        weeks[monday] = (ha + a, n + 1)
    first = period_start - timedelta(days=period_start.weekday()) if period_start is not None else None
    return tuple((w, ha, n, first is None or w >= first) for w, (ha, n) in sorted(weeks.items()))


def _ranked(table: dict) -> tuple:
    """``{key: (area, n)}`` as area-descending rows, ties broken by name."""
    return tuple((k, ha, n) for k, (ha, n) in sorted(table.items(), key=lambda kv: (-kv[1][0], kv[0])))


def _by_answer(answers: list, area: list, reviewed: list, split: Callable) -> tuple:
    table = {}
    for value, a, keep in zip(answers, area, reviewed):
        if not keep:
            continue  # answers are recorded by review only
        for c in (split(value) or [UNKNOWN_DRIVER]):
            ha, n = table.get(c, (0.0, 0))
            table[c] = (ha + a, n + 1)  # the alert's FULL area, once per code it names
    return _ranked(table)


def _by_source(sources: list, area: list) -> tuple:
    table = {}
    for value, a in zip(sources, area):
        parts = sorted(p.strip() for p in parse_formatted_string(_text(value)) if p.strip())
        key = " + ".join(parts) if parts else UNKNOWN_SOURCE
        ha, n = table.get(key, (0.0, 0))
        table[key] = (ha + a, n + 1)
    return _ranked(table)


def _by_admin(names: list, area: list, code: list, reviewed: list) -> tuple:
    table = {}
    for value, a, c, keep in zip(names, area, code, reviewed):
        if not keep:
            continue  # admin1 is recorded by review only
        key = _text(value).strip() or UNATTRIBUTED
        ha, confirmed, n = table.get(key, (0.0, 0.0, 0))
        table[key] = (ha + a, confirmed + (a if c == _CONFIRMED else 0.0), n + 1)
    return tuple((k, ha, confirmed, n) for k, (ha, confirmed, n)
                 in sorted(table.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[0])))


def _largest(frame, area: list, n: int) -> Optional[tuple]:
    if not area:
        return None
    i = max(range(n), key=lambda j: area[j])
    row = frame.iloc[i]
    ids = _column(frame, "alert_id", n)
    try:
        alert_id = int(ids[i])
    except (TypeError, ValueError):
        alert_id = i
    return (alert_id, float(area[i]), _text(row.get("admin1")).strip(),
            decimal_year_to_date(row.get("alert_date_min")))
