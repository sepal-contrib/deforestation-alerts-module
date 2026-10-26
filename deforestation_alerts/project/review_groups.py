"""Review-time groups of alerts (spec 2026-10-26 group review, §4-§5, §8.2).

A group is alerts of one set reviewed together: one *main* alert, which holds
the drawn polygon, and its *members*, which hold copies of the review. The
store column ``review_group`` (pandas ``Int64``) holds the main's id on every
row of a group, the main's own row included. A single review saved while
grouping is effective holds its own id: a *group of one*, which reads as a
single row everywhere except ``pre_grouping``. ``<NA>`` everywhere else; a
store without the column has no groups.

The app selects rows by *position* (``app_state.actual_alert_id`` indexes
the RangeIndex); ``review_group`` holds *ids* (``alert_id``, or the position
in a store without it, as ``set_store.position_of`` reads them). Every
function here takes and returns positions and maps ids itself.

This module is the only code that decides group membership: the panel, the
save, the split, the merge and the outputs ask it. It lives in ``project``
because ``share`` and ``growth`` use it; it imports nothing from ``gui``. No
Solara, no Earth Engine.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from deforestation_alerts.project import review_status, store_schema

COLUMN = store_schema.REVIEW_GROUP
NOT_REVIEWED = review_status.NOT_REVIEWED

#: what a plan does: save one review over a group, or take a row out of its group
SAVE, REMOVE = "save", "remove"

#: how a row the plan rewrites changes, as the save's confirmation names it
OVERWRITTEN, MOVED, JOINED, REMOVED = "overwritten", "moved", "joined", "removed"


@dataclass(frozen=True)
class Affected:
    """A row a plan changes beyond a first review: what the confirmation lists."""

    position: int
    status: str  # its stored status before the plan
    change: str  # OVERWRITTEN, MOVED, JOINED or REMOVED
    group: Optional[int] = None  # the position of the main of the group it leaves or joins


@dataclass(frozen=True)
class GroupPlan:
    """What one save (or one removal) writes, decided at the click (§5.1)."""

    kind: str  # SAVE or REMOVE
    main: int  # position of the main alert
    members: tuple = ()  # positions of the members after the plan, sorted, main excluded
    detached: tuple = ()  # rows reset to unreviewed (§4.4)
    affected: tuple = ()  # ``Affected`` rows, by position

    @property
    def size(self) -> int:
        """The alerts the review is saved for: the main and its members."""
        return 1 + len(self.members)

    def positions(self) -> tuple:
        """Every row the plan writes."""
        return tuple(sorted({self.main, *self.members, *self.detached}))


# ---- identity -------------------------------------------------------------
def ids(gdf) -> np.ndarray:
    """Every row's id, by position: ``alert_id``, or the position (§4.1)."""
    if "alert_id" in gdf.columns:
        return pd.to_numeric(gdf["alert_id"]).to_numpy(dtype="int64")
    return np.arange(len(gdf), dtype="int64")


def _positions_by_id(gdf) -> dict:
    return {int(i): p for p, i in enumerate(ids(gdf))}


def group_values(gdf) -> pd.Series:
    """``review_group`` by position, as ``Int64`` (all ``<NA>`` without the column)."""
    n = len(gdf)
    if COLUMN not in gdf.columns:
        return pd.Series(pd.array([pd.NA] * n, dtype="Int64"), index=pd.RangeIndex(n))
    values = pd.to_numeric(pd.Series(gdf[COLUMN].to_numpy(dtype=object)), errors="coerce")
    return pd.Series(values.astype("Int64").to_numpy(), index=pd.RangeIndex(n), dtype="Int64")


def _rows_of(values: pd.Series, value) -> list:
    return [int(p) for p in np.flatnonzero(values.eq(value).fillna(False).to_numpy())]


def status_of(gdf, position) -> str:
    """The stored status; anything that is not a non-empty string is unreviewed."""
    value = gdf["status"].iloc[position] if "status" in gdf.columns else None
    return value if isinstance(value, str) and value else NOT_REVIEWED


def group_of(gdf, position) -> tuple:
    """``(main, members)`` of the group ``position`` is in, members sorted and
    the main left out; ``(position, ())`` for a row in no group or in a group
    of one."""
    values = group_values(gdf)
    value = values.iloc[position]
    if pd.isna(value):
        return position, ()
    main = _positions_by_id(gdf).get(int(value))
    if main is None:  # a store that breaks the invariant reads as no group
        return position, ()
    return main, tuple(p for p in _rows_of(values, value) if p != main)


def is_grouped(gdf, position) -> bool:
    """Whether ``position`` is in a group with members (a group of one is not)."""
    return bool(group_of(gdf, position)[1])


def pre_grouping(gdf, position) -> bool:
    """Whether the row was reviewed before grouping was on: a review with no
    ``review_group`` (§4.2)."""
    return status_of(gdf, position) != NOT_REVIEWED and bool(pd.isna(group_values(gdf).iloc[position]))


# ---- plans ----------------------------------------------------------------
def plan_group(gdf, opened, selected) -> GroupPlan:
    """The save of the review on screen for ``opened`` and the ticked rows
    ``selected`` (positions). The main is ``opened``'s group's main; another
    group's main brings its whole group (JOINED), another group's member
    moves alone (MOVED; a group it leaves with only its main keeps that main
    as a group of one), a reviewed single is OVERWRITTEN, and rows of the
    main's group left unticked are detached (REMOVED)."""
    main, current = group_of(gdf, opened)
    chosen = {int(p) for p in selected} - {main}
    groups = {p: group_of(gdf, p) for p in chosen}
    members, affected = set(), []
    # Other groups' mains first: their members are theirs, even when ticked too.
    for p in sorted(chosen):
        p_main, p_members = groups[p]
        if p_main == p and p_members and p_main != main:
            for q in (p_main, *p_members):
                members.add(q)
                affected.append(Affected(q, status_of(gdf, q), JOINED, p_main))
    for p in sorted(chosen - members):
        p_main, p_members = groups[p]
        members.add(p)
        if p_main == main:
            continue  # already in this group
        if not p_members:
            if status_of(gdf, p) != NOT_REVIEWED:
                affected.append(Affected(p, status_of(gdf, p), OVERWRITTEN))
            continue
        affected.append(Affected(p, status_of(gdf, p), MOVED, p_main))
    detached = sorted(set(current) - members)
    affected += [Affected(p, status_of(gdf, p), REMOVED, main) for p in detached]
    return GroupPlan(SAVE, main, tuple(sorted(members)), tuple(detached),
                     tuple(sorted(affected, key=lambda a: a.position)))


def keep_plan(gdf, position) -> GroupPlan:
    """``plan_group`` with the stored membership: what a save writes when it
    was given no plan (a caller that predates groups)."""
    return plan_group(gdf, position, group_of(gdf, position)[1])


def plan_remove(gdf, position) -> GroupPlan:
    """*Remove from group* for a member (§4.4, §5.4). Raises ``ValueError``
    for a main or a row in no group."""
    main, members = group_of(gdf, position)
    if position == main or position not in members:
        raise ValueError(f"the alert at position {position} is not a member of a group")
    return GroupPlan(REMOVE, main, tuple(p for p in members if p != position), (position,),
                     (Affected(position, status_of(gdf, position), REMOVED, main),))


def check_groups(gdf) -> None:
    """Assert the group invariant (§4.2): every value is the id of a row that
    holds it, every group is of one reviewed status and, when the table has
    an ``evaluator`` column, of one evaluator. Groups of one are valid."""
    values = group_values(gdf)
    by_id = _positions_by_id(gdf)
    for value in pd.unique(values.dropna()):
        rows = _rows_of(values, value)
        main = by_id.get(int(value))
        assert main is not None and main in rows, f"group {value}: its main is not in it"
        statuses = {status_of(gdf, p) for p in rows}
        assert len(statuses) == 1 and NOT_REVIEWED not in statuses, f"group {value}: statuses {statuses}"
        if store_schema.EVALUATOR in gdf.columns:
            evaluators = {_evaluator(gdf, p) for p in rows}
            assert len(evaluators) == 1, f"group {value}: evaluators {evaluators}"


# ---- evaluators (§8.2) ----------------------------------------------------
def _evaluator(gdf, position) -> str:
    """The row's ``evaluator``; ``""`` for an unassigned (or non-string) value."""
    column = store_schema.EVALUATOR
    value = gdf[column].iloc[position] if column in gdf.columns else None
    return value if isinstance(value, str) else ""


def _plan_rows(plan) -> tuple:
    return tuple(sorted({plan.main, *plan.members, *plan.detached}))


def mixed_evaluators(gdf, plan) -> bool:
    """Whether the rows a plan touches (main, members, detached) carry more
    than one ``evaluator`` value; unassigned (``""``) is a value of its own."""
    if store_schema.EVALUATOR not in gdf.columns:
        return False
    return len({_evaluator(gdf, p) for p in _plan_rows(plan)}) > 1


def foreign_rows(gdf, plan, evaluator: str) -> tuple:
    """The positions a plan touches that are not assigned to ``evaluator``,
    sorted: what a review copy may not write."""
    return tuple(p for p in _plan_rows(plan) if _evaluator(gdf, p) != evaluator)
