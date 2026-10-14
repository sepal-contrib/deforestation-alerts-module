"""The Analysis filter (spec §8.2, §8.4): which alerts match one attribute's
values, and where the navigation goes among them. Pure: no widget, no reactive.

A filter is one column and a set of values, any of which matches (OR). The
values are text: what the column holds, ``""`` for an empty cell. Three
columns read differently:

- a question's column matches by option code (``gui.scripts.questions.codes``):
  a row answered "Logging, Mining" matches a filter on either code, and is
  counted once under each;
- ``status`` reads every unreviewed row (empty, NaN, ``"Not reviewed"``,
  ``review_status.is_reviewed``) as ``"Not reviewed"``;
- ``evaluator`` (the split's column) reads a store without it as all
  unassigned (``""``).

Positions are row positions of the full alerts table, as ``actual_alert_id``
and ``last_alert_index`` are: a filter never renumbers the alerts, so Save,
the per-alert file names and the saved position are unchanged by it.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from geopandas.array import GeometryDtype

from deforestation_alerts.project.review_status import STATUS_CODES
from deforestation_alerts.project.questions import column as question_column
from deforestation_alerts.project.questions import is_answer_column
from deforestation_alerts.project.review_status import NOT_REVIEWED, is_reviewed, reviewed_mask
from deforestation_alerts.project.split import EVALUATOR_COLUMN
from gui.i18n import t
from gui.scripts import questions as answers
from gui.scripts.legend_specs import STATUS_CLASSES

STATUS_COLUMN = "status"

#: An attribute with more distinct values than this is not offered (an id,
#: a measure): a list of hundreds of values is no filter.
MAX_VALUES = 50

#: Columns never offered: ids, the alert's own measures and dates, its
#: sources, and the image picks a save stores.
_EXCLUDED = frozenset({"alert_id", "set_id", "alert_sources", "alert_type_unique", "count", "area_ha"})

#: status code -> the legend key that names it (as ``context`` derives it)
_STATUS_KEYS = {code: key for code, _, key in STATUS_CLASSES}


@dataclass(frozen=True)
class AlertFilter:
    """One attribute and the values that match (any of them)."""

    column: str
    values: frozenset[str]


@dataclass(frozen=True)
class ColumnChoice:
    """An attribute the filter offers: its store column and its label;
    ``question_key`` when it is a question's answer column."""

    column: str
    label: str
    question_key: Optional[str] = None


def _missing(x) -> bool:
    return x is None or (pd.api.types.is_scalar(x) and bool(pd.isna(x)))


def _text(x) -> Optional[str]:
    """A cell as filter text: ``""`` when empty, None when it is not text, a
    bool or an integer (a list, an array, a float, a geometry)."""
    if _missing(x):
        return ""
    if isinstance(x, (bool, np.bool_)):
        return str(bool(x))
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, str):
        return x
    return None


def _status_text(x) -> str:
    return str(x) if is_reviewed(x) else NOT_REVIEWED


def _question_of(col: str, questions) -> Optional[object]:
    """The current question whose answer column is ``col``, or None."""
    return next((q for q in questions or [] if question_column(q) == col), None)


def _row_codes(series, q) -> list:
    """Each row's answer codes (a set; empty when unanswered). Decoded once
    per distinct stored text: a column holds few distinct answers."""
    seen = {}
    out = []
    for x in series:
        if _missing(x):
            out.append(frozenset())
            continue
        key = str(x)
        if key not in seen:
            seen[key] = frozenset(answers.codes(q, x))
        out.append(seen[key])
    return out


def _column_texts(gdf, col: str) -> list:
    """Each row's text in a column that is not a question's."""
    n = len(gdf)
    if col not in gdf.columns:
        return [NOT_REVIEWED if col == STATUS_COLUMN else ""] * n
    if col == STATUS_COLUMN:
        return [_status_text(x) for x in gdf[col]]
    out = []
    for x in gdf[col]:
        text = _text(x)
        out.append(text if text is not None else str(x))
    return out


def _offerable(name, series) -> bool:
    """An attribute column the filter may offer: text, bool or integer
    cells, 2..``MAX_VALUES`` distinct non-empty values, not excluded."""
    if not isinstance(name, str) or name in _EXCLUDED or is_answer_column(name):
        return False
    if name.endswith("_img") or name.endswith("_img_info") or name.startswith("alert_date_"):
        return False
    dtype = series.dtype
    if isinstance(dtype, GeometryDtype):
        return False
    if not (pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_integer_dtype(dtype)
            or isinstance(dtype, pd.StringDtype) or dtype == object):
        return False
    distinct = set()
    for x in series:
        text = _text(x)
        if text is None:
            return False
        if text:
            distinct.add(text)
            if len(distinct) > MAX_VALUES:
                return False
    return len(distinct) >= 2


def filter_columns(gdf, questions) -> list:
    """The attributes the filter offers for ``gdf``, in this order: the
    evaluator and the status (when the table has them), each current
    question's answer column the table has (labelled with the question), then
    every other text, bool or integer column with 2..50 distinct values
    (labelled with its name). A deleted question's column is not offered."""
    if gdf is None:
        return []
    cols = list(gdf.columns)
    out = []
    if EVALUATOR_COLUMN in cols:
        out.append(ColumnChoice(EVALUATOR_COLUMN, t("analysis_tile.filter.col_evaluator")))
    if STATUS_COLUMN in cols:
        out.append(ColumnChoice(STATUS_COLUMN, t("analysis_tile.facts.status")))
    taken = {EVALUATOR_COLUMN, STATUS_COLUMN}
    for q in questions or []:
        col = question_column(q)
        if col in cols and col not in taken:
            out.append(ColumnChoice(col, answers.question_label(q), q.key))
            taken.add(col)
    for col in cols:
        if col not in taken and _offerable(col, gdf[col]):
            out.append(ColumnChoice(col, col))
    return out


def _empty_label(col: str) -> str:
    return t("analysis_tile.filter.unassigned") if col == EVALUATOR_COLUMN else t("analysis_tile.filter.empty_value")


def value_choices(gdf, choice: ColumnChoice, questions) -> list:
    """``[(value, label, count)]`` of ``choice``'s column, every value that
    occurs at least once.

    A question's column lists option codes, in the question's option order,
    then typed answers (sorted), then the unanswered rows (``""``): a row is
    counted once under each of its codes. The status lists the statuses in
    the legend's order. Any other column lists its distinct values (sorted),
    then the empty cells. Labels are in the current language: an option's
    label, a status's legend label, "Unassigned" for an empty evaluator,
    "(empty)" for another empty cell; anything else as stored."""
    if gdf is None or not len(gdf):
        return []
    col = choice.column
    q = _question_of(col, questions)
    if q is not None and col in gdf.columns:
        counts = Counter()
        for row in _row_codes(gdf[col], q):
            counts.update(row or {""})
        by_code = {o.code: o for o in q.options}
        order = [o.code for o in q.options if o.code in counts]
        order += sorted((c for c in counts if c not in by_code and c), key=str.casefold)
        order += [""] if "" in counts else []
        return [(c, answers.option_label(by_code[c]) if c in by_code else (c or _empty_label(col)), counts[c])
                for c in order]
    counts = Counter(_column_texts(gdf, col))
    if col == STATUS_COLUMN:
        known = [s for s in STATUS_CODES if s in counts]
        rest = sorted((s for s in counts if s not in STATUS_CODES), key=str.casefold)
        return [(s, t(_STATUS_KEYS[STATUS_CODES[s]]) if s in STATUS_CODES else s, counts[s]) for s in known + rest]
    order = sorted((x for x in counts if x), key=str.casefold) + ([""] if "" in counts else [])
    return [(x, x or _empty_label(col), counts[x]) for x in order]


def match_mask(gdf, f: Optional[AlertFilter], questions) -> np.ndarray:
    """One bool per row of ``gdf``: whether the row matches ``f``. Every row
    matches no filter; no row matches a filter of no values."""
    n = 0 if gdf is None else len(gdf)
    if f is None:
        return np.ones(n, dtype=bool)
    if n == 0:
        return np.zeros(0, dtype=bool)
    q = _question_of(f.column, questions)
    if q is not None and f.column in gdf.columns:
        empty = "" in f.values
        return np.array([bool(row & f.values) if row else empty for row in _row_codes(gdf[f.column], q)],
                        dtype=bool)
    return np.array([x in f.values for x in _column_texts(gdf, f.column)], dtype=bool)


def next_match(mask, pos: int) -> Optional[int]:
    """The first matching position after ``pos``, or None (an end)."""
    hits = np.flatnonzero(np.asarray(mask, dtype=bool))
    after = hits[hits > pos]
    return int(after[0]) if len(after) else None


def prev_match(mask, pos: int) -> Optional[int]:
    """The last matching position before ``pos``, or None (an end)."""
    hits = np.flatnonzero(np.asarray(mask, dtype=bool))
    before = hits[hits < pos]
    return int(before[-1]) if len(before) else None


def _todo(gdf, mask) -> np.ndarray:
    """Matching and not reviewed (``review_status.reviewed_mask``)."""
    return np.asarray(mask, dtype=bool) & ~reviewed_mask(gdf).to_numpy(dtype=bool)


def first_unreviewed(gdf, mask) -> Optional[int]:
    """The first matching position not reviewed yet, or None."""
    if gdf is None or not len(gdf):
        return None
    hits = np.flatnonzero(_todo(gdf, mask))
    return int(hits[0]) if len(hits) else None


def next_unreviewed_match(gdf, mask, pos: int) -> Optional[int]:
    """The first matching, unreviewed position after ``pos``, wrapping round
    to the start, never ``pos`` itself; None when there is none (as
    ``context.next_unreviewed`` does over every alert)."""
    if gdf is None or not len(gdf):
        return None
    todo = _todo(gdf, mask)
    n = len(todo)
    start = max(pos, -1)
    for step in range(1, n + 1):
        i = (start + step) % n
        if i != pos and todo[i]:
            return i
    return None


def start_position(gdf, f: Optional[AlertFilter], last_alert_index: int, questions) -> int:
    """Where Analysis opens a set's alerts (spec §8.4), and where a filter
    applied on screen moves to (``last_alert_index`` is then the current
    position): -1 without alerts; no filter, ``last_alert_index`` clamped to
    the table; a filter, ``last_alert_index`` when it matches, else the first
    unreviewed match, else the first match, else ``last_alert_index``
    (nothing matches: the reviewer stays where they were)."""
    if gdf is None or not len(gdf):
        return -1
    here = max(0, min(int(last_alert_index), len(gdf) - 1))
    if f is None:
        return here
    mask = match_mask(gdf, f, questions)
    if mask[here]:
        return here
    for target in (first_unreviewed(gdf, mask), next_match(mask, -1)):
        if target is not None:
            return target
    return here
