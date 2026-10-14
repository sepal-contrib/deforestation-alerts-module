"""Dealing a set's alerts between evaluators (spec §4.1). Pure: no files, no
Solara. ``gui/scripts/split_service.py`` reads and writes the store.

The store keeps each alert's evaluator in the ``evaluator`` column (``""`` =
unassigned); ``AlertSet.split`` is the record of the split that wrote it.
"""

import hashlib
import json
from typing import Sequence

import pandas as pd

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.review_status import is_reviewed

#: The store column the split writes.
EVALUATOR_COLUMN = "evaluator"
#: At most this many evaluators per split.
MAX_EVALUATORS = 50


class SplitError(KeyedError, ValueError):
    """A refused split. ``key`` is the i18n key of the message, ``params``
    its values: the step shows ``t(err.key, **err.params)`` (``error_text``
    renders it too: ``params`` is ``KeyedError.values``)."""

    def __init__(self, key: str, **params):
        super().__init__(key=key, **params)
        self.params = params


def normalize_names(lines: list[str]) -> list[str]:
    """The typed names, stripped, blanks dropped, in order. Refused: two
    names equal ignoring case, more than ``MAX_EVALUATORS``, or none."""
    names = [str(line).strip() for line in lines or []]
    names = [n for n in names if n]
    seen = set()
    for name in names:
        folded = name.casefold()
        if folded in seen:
            raise SplitError("evaluators.err.duplicate_name", name=name)
        seen.add(folded)
    if len(names) > MAX_EVALUATORS:
        raise SplitError("evaluators.err.too_many", max=MAX_EVALUATORS)
    if not names:
        raise SplitError("evaluators.err.no_names")
    return names


def count_names(n: int, prefix: str) -> list[str]:
    """``n`` numbered names: ``["<prefix> 1", ..., "<prefix> n"]``, 1..50."""
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_EVALUATORS:
        raise SplitError("evaluators.err.bad_count", max=MAX_EVALUATORS)
    return [f"{prefix} {i}" for i in range(1, n + 1)]


def deal(statuses: Sequence, evaluators: list[str], include_reviewed: bool) -> list[str]:
    """Each row's evaluator, in store row order (the set's sort order): the
    eligible rows (all of them, or only the unreviewed ones without
    ``include_reviewed``) get the names round-robin, eligible row ``k``
    ``evaluators[k % N]``; every other row ``""``."""
    if not evaluators:
        raise SplitError("evaluators.err.no_names")
    out, k = [], 0
    for status in statuses:
        if include_reviewed or not is_reviewed(status):
            out.append(evaluators[k % len(evaluators)])
            k += 1
        else:
            out.append("")
    return out


def assignments(gdf) -> "pd.Series":
    """The ``evaluator`` column as strings, ``""`` for a missing value; all
    ``""`` (index-aligned) for a store without the column."""
    if gdf is None:
        return pd.Series([], dtype=object)
    if EVALUATOR_COLUMN not in gdf.columns:
        return pd.Series([""] * len(gdf), index=gdf.index, dtype=object)
    col = gdf[EVALUATOR_COLUMN]
    return pd.Series(["" if pd.isna(v) else str(v) for v in col.tolist()], index=gdf.index, dtype=object)


def questions_digest(questions) -> str:
    """sha256 (hex) of the canonical JSON of ``[key, multiple, sorted option
    codes]`` per question (spec §5.1). Labels are left out: renaming a
    question or an option does not change what its answers mean."""
    canon = [[q.key, bool(q.multiple), sorted(o.code for o in q.options)] for q in questions]
    text = json.dumps(canon, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
