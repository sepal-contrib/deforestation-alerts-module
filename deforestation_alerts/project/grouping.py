"""The group-review setting and the rules that read it (spec 2026-10-26):
which grouping applies to a set, what is locked, and the radius text parser.
Pure: no I/O, no GUI."""

import math
from dataclasses import dataclass
from typing import Optional

MIN_RADIUS_M, MAX_RADIUS_M = 10.0, 5000.0


@dataclass(frozen=True)
class Grouping:
    radius_m: float
    include_reviewed: bool


@dataclass(frozen=True)
class GroupLock:
    option: bool
    radius: bool
    include: bool
    reason: Optional[str]  # None | "groups" | "split"
    n_groups: int


def _clamp(radius_m: float) -> float:
    return min(max(radius_m, MIN_RADIUS_M), MAX_RADIUS_M)


def effective(p, s) -> Optional[Grouping]:
    """The grouping in force for set ``s``: its split record once split, else
    the project's setting. ``None`` means grouping is off."""
    if p is None or s is None:
        return None
    if s.split is not None:
        if s.split.group_radius_m is None:
            return None
        return Grouping(_clamp(s.split.group_radius_m), s.split.group_include_reviewed)
    g = p.group_review
    if not g.enabled:
        return None
    return Grouping(_clamp(g.radius_m), g.include_reviewed)


def lock_state(p, s) -> GroupLock:
    """What the Evaluators step may no longer change: a split locks all three
    controls; saved groups in any set lock the option and the radius."""
    if s is not None and s.split is not None:
        return GroupLock(True, True, True, "split", s.n_groups)
    n = sum(x.n_groups for x in p.alert_sets.values())
    if n > 0:
        return GroupLock(True, True, False, "groups", n)
    return GroupLock(False, False, False, None, 0)


def parse_radius(text) -> Optional[float]:
    """Metres from the typed text (comma decimals allowed); ``None`` when it is
    not a finite number within 10-5000."""
    try:
        value = float(str(text).strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(value) or not MIN_RADIUS_M <= value <= MAX_RADIUS_M:
        return None
    return value
