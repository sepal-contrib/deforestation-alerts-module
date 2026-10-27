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

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.project import review_status, store_schema
from deforestation_alerts.project.questions import is_answer_column

COLUMN = store_schema.REVIEW_GROUP
NOT_REVIEWED = review_status.NOT_REVIEWED

#: what a plan does: save one review over a group, or take a row out of its group
SAVE, REMOVE = "save", "remove"

#: how a row the plan rewrites changes, as the save's confirmation names it
OVERWRITTEN, MOVED, JOINED, REMOVED = "overwritten", "moved", "joined", "removed"

#: the image picks and the admin names a review writes, copied to members and cleared on a reset
IMAGE_COLUMNS = ("before_img", "before_img_info", "after_img", "after_img_info")
ADMIN_COLUMNS = ("admin1", "admin2", "admin3")


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
    return _group_in(group_values(gdf), _positions_by_id(gdf), position)


def _group_in(values, by_id, position) -> tuple:
    """``group_of`` over the table's ``group_values`` and id map, made once
    by a caller that asks for several rows."""
    value = values.iloc[position]
    if pd.isna(value):
        return position, ()
    main = by_id.get(int(value))
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
    main, current, chosen, groups = _choice(gdf, opened, selected)
    members, affected = set(), []
    # Other groups' mains first: their members are theirs, even when ticked too.
    for p_main, rows in _joined(main, chosen, groups):
        for q in rows:
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


def _choice(gdf, opened, selected) -> tuple:
    """``(main, its current members, chosen rows, {row: group_of(row)})`` of
    a save of ``opened`` with ``selected`` ticked; the table is read once."""
    values, by_id = group_values(gdf), _positions_by_id(gdf)
    main, current = _group_in(values, by_id, opened)
    chosen = {int(p) for p in selected} - {main}
    return main, current, chosen, {p: _group_in(values, by_id, p) for p in chosen}


def _joined(main, chosen, groups) -> list:
    """``(other main, its whole group)`` for every other group's main in
    ``chosen``, in order: such a tick brings its whole group (§4.5)."""
    out = []
    for p in sorted(chosen):
        p_main, p_members = groups[p]
        if p_main == p and p_members and p_main != main:
            out.append((p_main, (p_main, *p_members)))
    return out


def plan_size(gdf, opened, selected) -> int:
    """``plan_group(gdf, opened, selected).size`` without building the plan
    (no statuses read, no affected rows): what *Save for N alerts* counts."""
    main, _current, chosen, groups = _choice(gdf, opened, selected)
    members = set(chosen)
    for _main, rows in _joined(main, chosen, groups):
        members.update(rows)
    return 1 + len(members)


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


class GroupInvariantError(ValueError):
    """A table breaks the group invariant (§4.2). ``share.apply_return``
    refuses a merge with it (``share.merge_err_groups``); anywhere else it
    is a programmer error."""


def _broken(value, why: str):
    raise GroupInvariantError(f"group {value}: {why}")


def check_groups(gdf) -> None:
    """Check the group invariant (§4.2), raising ``GroupInvariantError``:
    every value is the id of a row that holds it, every group is of one
    reviewed status and, when the table has an ``evaluator`` column, of one
    evaluator. Groups of one are valid."""
    values = group_values(gdf)
    by_id = _positions_by_id(gdf)
    for value in pd.unique(values.dropna()):
        rows = _rows_of(values, value)
        main = by_id.get(int(value))
        if main is None or main not in rows:
            _broken(value, "its main is not in it")
        statuses = {status_of(gdf, p) for p in rows}
        if len(statuses) != 1 or NOT_REVIEWED in statuses:
            _broken(value, f"statuses {statuses}")
        if store_schema.EVALUATOR in gdf.columns:
            evaluators = {_evaluator(gdf, p) for p in rows}
            if len(evaluators) != 1:
                _broken(value, f"evaluators {evaluators}")


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


# ---- what a plan writes (§4.3-§4.5) ---------------------------------------
def _is_geometry(value) -> bool:
    return isinstance(value, BaseGeometry) and not value.is_empty


def _links(cell) -> list:
    # ``growth`` will read groups from this module: import it at call time.
    from deforestation_alerts.project.growth import links_of

    return links_of(cell)


def polygon_area_ha(geom, crs=None, *, source="EPSG:4326") -> float:
    """The area of ``geom`` (in ``source``) in hectares, measured in ``crs``
    (``state.area_crs``: the grid's, metric), or in its estimated UTM zone."""
    series = gpd.GeoSeries([geom], crs=source)
    return float(series.to_crs(crs or series.estimate_utm_crs()).area.iloc[0]) / 10_000.0


def own_area(gdf, p, crs=None) -> float:
    """The row's Integrate area (§4.3): its stored ``area_ha`` while that is
    still Integrate's (no drawing stored, a positive number), else the area
    of its Integrate geometry in ``crs``."""
    stored = gdf["area_ha"].iloc[p] if "area_ha" in gdf.columns else None
    stored = pd.to_numeric(pd.Series([stored], dtype=object), errors="coerce").iloc[0]
    drawn = gdf["alert_polygon"].iloc[p] if "alert_polygon" in gdf.columns else None
    if not _is_geometry(drawn) and pd.notna(stored) and stored > 0:
        return float(stored)
    return polygon_area_ha(gdf.geometry.iloc[p], crs, source=gdf.crs or "EPSG:4326")


def reset_values(gdf, p, crs=None) -> dict:
    """The cells of a row taken out of a review (§4.4): unreviewed, every
    answer column ``""``, no picks, no admin names, no drawing, its Integrate
    area, no group and, in a store with growth columns, its automatic links."""
    values = {"status": NOT_REVIEWED}
    values.update({c: "" for c in gdf.columns if is_answer_column(c)})
    values.update({c: None for c in (*IMAGE_COLUMNS, *ADMIN_COLUMNS) if c in gdf.columns})
    values["alert_polygon"] = None
    values["area_ha"] = own_area(gdf, p, crs)
    auto = store_schema.GROWS_FROM_AUTO
    if store_schema.GROWS_FROM in gdf.columns or auto in gdf.columns:
        values[store_schema.GROWS_FROM] = _links(gdf[auto].iloc[p]) if auto in gdf.columns else []
    values[COLUMN] = pd.NA
    return values


def plan_updates(gdf, plan, review=None, *, drawing=None, crs=None, links=None) -> dict:
    """``{position: {column: value}}``, every cell ``plan`` writes (§4.3-§4.5).

    ``review`` is the reviewer's values for the main (status, the current
    questions' answer columns, the image picks, the admin names); a REMOVE
    plan takes none and changes only its detached row. ``drawing`` is the
    drawn polygon (EPSG:4326, the union), or None; its area is measured in
    ``crs``. ``links``, when not None, are the group's ``grows_from`` and go
    to the main and every member. A SAVE writes ``review_group`` = the main's
    id on the main (a group of one when it has no members) and its members."""
    updates = {}
    if plan.kind == SAVE:
        review = dict(review or {})
        drawn = drawing if _is_geometry(drawing) else None
        main_id = int(ids(gdf)[plan.main])
        updates[plan.main] = {
            **review,
            "alert_polygon": drawn,
            "area_ha": polygon_area_ha(drawn, crs) if drawn is not None else own_area(gdf, plan.main, crs),
            COLUMN: main_id,
        }
        # Answers to deleted questions: what the main stored, on every member.
        archived = {c: gdf[c].iloc[plan.main] for c in gdf.columns if is_answer_column(c) and c not in review}
        for p in plan.members:
            updates[p] = {
                **review,
                **archived,
                "alert_polygon": None,
                "area_ha": 0.0 if drawn is not None else own_area(gdf, p, crs),
                COLUMN: main_id,
            }
        if links is not None:
            for p in (plan.main, *plan.members):
                updates[p][store_schema.GROWS_FROM] = list(links)
    for p in plan.detached:
        updates[p] = reset_values(gdf, p, crs)
    return updates


def ensure_column(gdf) -> None:
    """Make ``review_group`` an ``Int64`` column of ``gdf`` in place: added
    (all ``<NA>``) when missing, cast when stored as object or float."""
    if COLUMN not in gdf.columns or str(gdf[COLUMN].dtype) != "Int64":
        gdf[COLUMN] = pd.array(group_values(gdf).to_numpy(), dtype="Int64")


def bounds(gdf, positions) -> tuple:
    """``(minx, miny, maxx, maxy)`` around the rows' boxes (their geometry
    without a ``bounding_box`` column): where the admin lookup looks."""
    column = "bounding_box" if "bounding_box" in gdf.columns else gdf.geometry.name
    return tuple(float(v) for v in gpd.GeoSeries(list(gdf[column].iloc[list(positions)])).total_bounds)


def n_groups(gdf) -> int:
    """The groups with members: ``review_group`` values held by two rows or more."""
    counts = group_values(gdf).dropna().value_counts()
    return int((counts >= 2).sum())


# ---- what the outputs read (§9) -------------------------------------------
def group_shape(gdf, p) -> Optional[BaseGeometry]:
    """The group's outline: its main's drawing, else the union of the
    group's Integrate geometries (the row's own for a single row)."""
    main, members = group_of(gdf, p)
    drawn = gdf["alert_polygon"].iloc[main] if "alert_polygon" in gdf.columns else None
    if _is_geometry(drawn):
        return drawn
    if not members:
        return gdf.geometry.iloc[main]
    return shapely.union_all(gdf.geometry.iloc[[main, *members]].to_numpy())


def _sources(values) -> str:
    parts = (part.strip() for value in values if isinstance(value, str) for part in value.split(";"))
    return "; ".join(dict.fromkeys(part for part in parts if part))


def group_record(gdf, p) -> tuple:
    """``(row, outline)`` the per-alert outputs describe (§9). For a grouped
    row: the main's row with the group's summed ``area_ha``, its first and
    last detection dates and its distinct sources (the main's first). A
    single row is itself. Either row carries ``group_size``, ``group_alerts``
    (the 1-based alert numbers, comma separated) and, in a store with links,
    ``grows_from`` as the main's links joined by ``";"``. The outline is
    ``group_shape``."""
    main, members = group_of(gdf, p)
    rows = [main, *members]
    row = gdf.iloc[main].copy()
    if members:
        part = gdf.iloc[rows]
        if "area_ha" in part.columns:
            row["area_ha"] = float(pd.to_numeric(part["area_ha"], errors="coerce").fillna(0.0).sum())
        if "alert_date_min" in part.columns:
            row["alert_date_min"] = pd.to_numeric(part["alert_date_min"], errors="coerce").min()
        if "alert_date_max" in part.columns:
            row["alert_date_max"] = pd.to_numeric(part["alert_date_max"], errors="coerce").max()
        if "alert_sources" in part.columns:
            row["alert_sources"] = _sources(part["alert_sources"])
    if store_schema.GROWS_FROM in gdf.columns:
        row[store_schema.GROWS_FROM] = ";".join(_links(gdf[store_schema.GROWS_FROM].iloc[main]))
    row["group_size"] = len(rows)
    row["group_alerts"] = ", ".join(str(q + 1) for q in sorted(rows))
    return row, group_shape(gdf, p)


# ---- the nearby search (§6.1) ---------------------------------------------
@dataclass(frozen=True)
class NearbyRow:
    """One row of the Nearby list."""

    position: int
    distance_m: Optional[float]  # edge to edge from the opened group; None for the group's own rows
    status: str
    group: Optional[int]  # the position of the main of its group, None when in none
    blocked: bool  # another evaluator's (or, in a copy, not the copy's): it cannot be ticked
    evaluator: str  # "" when the set has no evaluators
    area_ha: float  # its Integrate area (``own_area``)
    date_min: Optional[float]  # its ``alert_date_min``


@dataclass(frozen=True)
class NearbyResult:
    rows: tuple = ()  # the group's own rows and the selected rows first, then the candidates, nearest first
    more: int = 0  # candidates within the distance left out by ``limit``


#: the last keyed projection, one immutable ``(key, n_rows, geometries)`` tuple replaced whole
_METRIC_CACHE: tuple = (None, 0, None)


def _metric(gdf, crs, cache_key) -> np.ndarray:
    """The rows' Integrate geometries in ``crs`` (or their estimated UTM zone)
    as an array. ``cache_key`` (the set id and its store generation), when not
    None, keeps the last projection for the next search; a call without a key
    neither reads nor clears it."""
    global _METRIC_CACHE
    key = None if cache_key is None else (cache_key, str(crs))
    cached = _METRIC_CACHE  # read once: another thread may replace it
    if key is not None and cached[0] == key and cached[1] == len(gdf):
        return cached[2]
    series = gdf.geometry if gdf.crs is not None else gdf.geometry.set_crs("EPSG:4326")
    geoms = series.to_crs(crs or series.estimate_utm_crs()).to_numpy()
    if key is not None:
        _METRIC_CACHE = (key, len(gdf), geoms)
    return geoms


def _date_min(gdf, p) -> Optional[float]:
    if "alert_date_min" not in gdf.columns:
        return None
    value = pd.to_numeric(pd.Series([gdf["alert_date_min"].iloc[p]], dtype=object), errors="coerce").iloc[0]
    return None if pd.isna(value) else float(value)


def nearby(gdf, main, radius_m, crs=None, *, include=(), offer_reviewed=False, evaluator=None,
           limit=50, cache_key=None) -> NearbyResult:
    """The Nearby list of the group whose main is ``main`` (spec §6.1).

    Distances are edge to edge from the union of the group's Integrate
    polygons (the main and its stored members), in ``crs``. Listed first: the
    group's own rows (``distance_m`` None) and every row of ``include`` (the
    selection, whatever its distance); then the nearest candidates within
    ``radius_m``, at most ``limit`` (ties by position), ``more`` counting the
    ones left out. Candidates are unreviewed rows, reviewed rows that are in
    a group, and (``offer_reviewed``) reviews from before grouping. The main
    is never listed. A row is ``blocked`` when its evaluator is not the
    main's or, in a copy (``evaluator`` given), not ``evaluator``."""
    values = group_values(gdf)
    by_id = _positions_by_id(gdf)
    own_rows = [] if pd.isna(values.iloc[main]) else [p for p in _rows_of(values, values.iloc[main]) if p != main]
    origin = [main, *own_rows]
    geoms = _metric(gdf, crs, cache_key)
    source = shapely.union_all(geoms[origin])
    radius = max(float(radius_m), 0.0)
    selected = sorted({int(p) for p in include} - {main} - set(own_rows))
    unreviewed = np.array([status_of(gdf, p) == NOT_REVIEWED for p in range(len(gdf))], dtype=bool)
    grouped = values.notna().to_numpy()
    offered = unreviewed | grouped | offer_reviewed
    near = shapely.STRtree(geoms).query(source, predicate="dwithin", distance=radius)
    skip = {main, *own_rows, *selected}
    cands = [int(p) for p in near if int(p) not in skip and offered[int(p)]]
    far = selected
    distance = {p: float(d) for p, d in zip(cands + far, shapely.distance(geoms[cands + far], source))} \
        if cands or far else {}
    cands = sorted(cands, key=lambda p: (distance[p], p))
    own = _evaluator(gdf, main)

    def _row(p, measured=True):
        value = values.iloc[p]
        group = None if pd.isna(value) else by_id.get(int(value))
        mine = _evaluator(gdf, p)
        blocked = mine != own or (evaluator is not None and mine != evaluator)
        return NearbyRow(p, distance[p] if measured else None, status_of(gdf, p), group, blocked, mine,
                         own_area(gdf, p, crs), _date_min(gdf, p))

    rows = [_row(p, False) for p in own_rows]
    rows += [_row(p) for p in sorted(selected, key=lambda p: (distance[p], p))]
    rows += [_row(p) for p in cands[:limit]]
    return NearbyResult(tuple(rows), max(len(cands) - limit, 0))


def neighbourhoods(gdf, radius_m, crs=None, *, eligible=None) -> list:
    """The connected components (single linkage) of the ``eligible`` positions
    (all when None) whose polygons are within ``radius_m`` of each other,
    edge to edge in ``crs``: sorted position tuples ordered by their first
    position. The eligible rows of a stored group (two rows or more) are one
    unit."""
    positions = sorted(range(len(gdf)) if eligible is None else {int(p) for p in eligible})
    parent = {p: p for p in positions}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        parent[max(find(a), find(b))] = min(find(a), find(b))

    values = group_values(gdf)
    by_id = _positions_by_id(gdf)
    first = {}
    for p in positions:
        value = values.iloc[p]
        if not pd.isna(value) and int(value) in by_id:
            union(first.setdefault(int(value), p), p)
    if len(positions) > 1:
        geoms = _metric(gdf, crs, None)[positions]
        pairs = shapely.STRtree(geoms).query(geoms, predicate="dwithin", distance=max(float(radius_m), 0.0))
        for a, b in pairs.T:
            union(positions[int(a)], positions[int(b)])
    components = {}
    for p in positions:
        components.setdefault(find(p), []).append(p)
    return sorted(tuple(c) for c in components.values())
