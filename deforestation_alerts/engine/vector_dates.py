"""Date column detection and parsing for vector alert sources (spec §5.2).

A CSV, GeoPackage or shapefile alert carries one date per feature. ``detect``
guesses the column's date order (one of ``models.DATE_ORDERS``) from its
values, ``parse`` turns the column into engine dates (int ``yyyyddd``, 0 where
unreadable) and ``contradicts`` says whether a stored order no longer fits a
re-read column.

``detect`` and ``parse`` share ``_read``, which reads one value under every
order it could mean, so the evidence rule and the parsing cannot drift apart.
Only a day/month value (``03/04/2026``) is order-dependent: a value valid only
as d/m is evidence for ``dmy``, only as m/d for ``mdy``, under neither it is
skipped and never evidence. Two-digit years and month names are out of scope.
"""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from deforestation_alerts.engine.catalogue import yyyyddd

_YEAR_FIRST = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_COMPACT = re.compile(r"^\d{8}$")
_DAY_MONTH = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")
_TIME_TAIL = re.compile(r"[ T].*$")


@dataclass(frozen=True)
class DateReading:
    """What ``detect`` found. ``order`` is None when the column is ambiguous,
    in conflict, or has no readable value; blanks count in neither tally."""

    order: Optional[str]
    ambiguous: bool
    conflict: bool
    valid: int
    skipped: int


def _ymd(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except (ValueError, OverflowError):
        return None


def _is_blank(v) -> bool:
    if v is None or v is pd.NaT or v is pd.NA:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, float):
        return math.isnan(v)
    return False


def _read(v) -> dict:
    """``v`` under every order it could mean: ``{order: date or None}``.

    An empty dict is an unreadable value. Orders absent from the dict do not
    apply to the value's shape (a year-first value has no ``dmy`` reading of
    its own, ``parse`` accepts it under both).
    """
    if isinstance(v, datetime):
        return {"native": v.date()}
    if isinstance(v, date):
        return {"native": v}
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        if isinstance(v, (float, np.floating)) and not math.isfinite(v):
            return {}
        n = int(v)
        return {"yyyymmdd": _ymd(n // 10000, n // 100 % 100, n % 100)}
    if not isinstance(v, str):
        return {}
    s = _TIME_TAIL.sub("", v.strip())
    if m := _YEAR_FIRST.match(s):
        return {"iso": _ymd(*map(int, m.groups()))}
    if _COMPACT.match(s):
        return {"yyyymmdd": _ymd(int(s[:4]), int(s[4:6]), int(s[6:]))}
    if m := _DAY_MONTH.match(s):
        a, b, y = map(int, m.groups())
        return {"dmy": _ymd(y, b, a), "mdy": _ymd(y, a, b)}
    return {}


def detect(values: pd.Series) -> DateReading:
    """Guess the date order of ``values`` (rules in the module docstring)."""
    reads = [_read(v) for v in values if not _is_blank(v)]
    total = len(reads)

    if any("dmy" in r for r in reads):
        evidence = {"dmy": 0, "mdy": 0}
        valid = 0
        for r in reads:
            if "dmy" in r:
                ok = [k for k in ("dmy", "mdy") if r[k]]
                if len(ok) == 1:
                    evidence[ok[0]] += 1
            else:
                ok = [k for k, d in r.items() if d and k in ("iso", "native")]
            valid += bool(ok)
        order = ambiguous = conflict = None
        if evidence["dmy"] and evidence["mdy"]:
            conflict, ambiguous = True, False
        elif evidence["dmy"] or evidence["mdy"]:
            order = "dmy" if evidence["dmy"] else "mdy"
            conflict = ambiguous = False
        else:
            conflict, ambiguous = False, valid > 0
        return DateReading(order, ambiguous, conflict, valid, total - valid)

    counts = {}
    for r in reads:
        for k, d in r.items():
            counts[k] = counts.get(k, 0) + bool(d)
    best = max(counts, key=counts.get, default=None)
    if best is None or not counts[best]:
        return DateReading(None, False, False, 0, total)
    return DateReading(best, False, False, counts[best], total - counts[best])


def parse(values: pd.Series, order: str) -> np.ndarray:
    """``values`` as engine dates (int64 ``yyyyddd``), 0 where unreadable.

    ``dmy``/``mdy`` also take year-first values, ``iso`` only year-first ones,
    ``yyyymmdd`` integers, floats and 8-digit text, ``native`` real dates.
    """
    accepted = {"dmy": ("dmy", "iso"), "mdy": ("mdy", "iso")}.get(order, (order,))
    out = np.zeros(len(values), dtype=np.int64)
    for i, v in enumerate(values):
        if _is_blank(v):
            continue
        r = _read(v)
        d = next((r[k] for k in accepted if r.get(k)), None)
        if d:
            out[i] = yyyyddd(d)
    return out


def contradicts(values: pd.Series, order: str) -> bool:
    """True when ``order`` no longer fits ``values`` (a re-read column)."""
    if order in ("dmy", "mdy"):
        other = "mdy" if order == "dmy" else "dmy"
        for v in values:
            r = _read(v) if not _is_blank(v) else {}
            if "dmy" in r and r[other] and not r[order]:
                return True
        return False
    if order == "native":
        return any("native" not in _read(v) for v in values if not _is_blank(v))
    return False
