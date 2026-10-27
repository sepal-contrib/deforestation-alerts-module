"""Growth alerts (spec 2026-10-20): an alert in a later set that touches a
loss-verdict alert of an earlier set is stored with a ``grows_from`` link to
it. This module is the pure part: the link text, the alert kinds, the nodes
and parent candidates read from a store, the link graph and the area of a
group's history. It imports nothing from the UI."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.project import review_groups
from deforestation_alerts.project.models import AlertSet, Period
from deforestation_alerts.project.review_status import FALSE_POSITIVE, LOSS_VERDICTS, NOT_REVIEWED
from deforestation_alerts.project.store_schema import GROWS_FROM, REVIEW_GROUP

#: The column Integrate may write with an alert's kind.
KIND_COLUMN = "kind"
KIND_NEW = "new"
KIND_GROWTH = "growth"
KIND_GROWTH_SMALL = "growth_small"
KIND_NEW_SMALL = "new_small"
KINDS = (KIND_NEW, KIND_GROWTH, KIND_GROWTH_SMALL, KIND_NEW_SMALL)


class LinkRef(NamedTuple):
    set_id: str
    alert_id: int
    run_id: str


def run_id_of(s: AlertSet) -> str:
    """The id of one run of a set: a re-run builds a new AlertSet, so its links
    no longer resolve."""
    return s.created_at.strftime("%Y%m%d%H%M%S%f")


def node_key(set_id: str, alert_id: int) -> str:
    return f"{set_id}#{int(alert_id)}"


def format_link(ref: LinkRef) -> str:
    return f"{node_key(ref.set_id, ref.alert_id)}@{ref.run_id}"


def parse_link(text) -> Optional[LinkRef]:
    """``"<set_id>#<alert_id>@<run_id>"`` -> LinkRef, or None if malformed."""
    if not isinstance(text, str):
        return None
    head, at, run_id = text.rpartition("@")
    if not at or not run_id:
        return None
    set_id, hash_, alert = head.rpartition("#")
    if not hash_ or not set_id:
        return None
    try:
        return LinkRef(set_id, int(alert), run_id)
    except ValueError:
        return None


def links_of(cell) -> list[str]:
    """One ``grows_from`` cell as a list of links: None, NaN, ``""``, a list,
    tuple, array or a ``";"``-joined string; order kept, duplicates dropped."""
    if cell is None or cell is pd.NA:
        return []
    if isinstance(cell, str):
        items = cell.split(";")
    elif isinstance(cell, (list, tuple, np.ndarray, pd.Series)):
        items = list(cell)
    else:
        try:
            return [] if pd.isna(cell) else [str(cell)]
        except (TypeError, ValueError):
            return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item or item in out:
            continue
        out.append(item)
    return out


def _flag(value) -> bool:
    if value is None or value is pd.NA:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return bool(value)


def kind_of(links, below_min) -> str:
    small = _flag(below_min)
    if links:
        return KIND_GROWTH_SMALL if small else KIND_GROWTH
    return KIND_NEW_SMALL if small else KIND_NEW


def _geometry(value) -> Optional[BaseGeometry]:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = shapely.from_wkb(bytes(value))
    if not isinstance(value, BaseGeometry) or value.is_empty:
        return None
    return value


def parent_shape(row) -> Optional[BaseGeometry]:
    """The reviewer's drawn polygon if there is one, else the alert's own."""
    drawn = _geometry(row["alert_polygon"]) if "alert_polygon" in row else None
    if drawn is not None:
        return drawn
    return _geometry(row["geometry"]) if "geometry" in row else None


@dataclass(frozen=True)
class Node:
    set_id: str
    alert_id: int
    period_id: str
    period_start: date
    period_end: date
    status: str
    area_ha: float
    date_min: Optional[float]
    date_max: Optional[float]
    shape: Optional[BaseGeometry]
    links: tuple[str, ...]
    #: the row's ``review_group`` (its group's main id; None outside a group),
    #: group review (spec 2026-10-26 §7)
    review_group: Optional[int] = None

    @property
    def key(self) -> str:
        return node_key(self.set_id, self.alert_id)

    @property
    def is_member(self) -> bool:
        """A group's member: in a group, not its main."""
        return self.review_group is not None and self.review_group != self.alert_id

    def link_to(self, run_id: str) -> str:
        return format_link(LinkRef(self.set_id, self.alert_id, run_id))


@dataclass(frozen=True)
class Candidate:
    """A parent candidate: a node whose status is a loss verdict
    (``is_candidate``), with its set's run id. ``rows_of_set`` and a review
    package's snapshot pair other rows the same way, to resolve links."""

    node: Node
    run_id: str

    @property
    def key(self) -> str:
        return self.node.key

    @property
    def link(self) -> str:
        return self.node.link_to(self.run_id)


def _float(value) -> Optional[float]:
    if value is None or value is pd.NA or pd.isna(value):
        return None
    return float(value)


def _status(value) -> str:
    if value is None or value is pd.NA or value == "":
        return NOT_REVIEWED
    try:
        return NOT_REVIEWED if pd.isna(value) else value
    except (TypeError, ValueError):
        return value


def _groups_and_shapes(gdf, ids, drawn, own) -> tuple[list, list]:
    """Each row's ``review_group`` (None outside a group) and parent shape:
    the drawing, else the row's own, but none for a member of a group whose
    main has a drawing (the drawing is the group's loss, spec 2026-10-26 §7.1)."""
    n = len(gdf)
    shapes = [d if d is not None else g for d, g in zip(drawn, own)]
    if REVIEW_GROUP not in gdf.columns:
        return [None] * n, shapes
    values = review_groups.group_values(gdf)
    groups = [None if pd.isna(v) else int(v) for v in values]
    position = {int(i): p for p, i in enumerate(ids)}
    for p, (aid, group) in enumerate(zip(ids, groups)):
        main = position.get(group) if group is not None and group != int(aid) else None
        if main is not None and drawn[main] is not None:
            shapes[p] = None
    return groups, shapes


def nodes_of_set(s: AlertSet, period: Period, gdf) -> list[Node]:
    """Every row of ``gdf`` as a ``Node``. Column by column (``iterrows``
    builds a Series per row, a quarter of a second on 5,000 rows)."""
    n = len(gdf)

    def col(name):
        return list(gdf[name]) if name in gdf.columns else [None] * n

    ids = [int(aid) for aid in gdf["alert_id"]]
    drawn = [_geometry(v) for v in gdf["alert_polygon"]] if "alert_polygon" in gdf.columns else [None] * n
    own = [_geometry(v) for v in gdf["geometry"]] if "geometry" in gdf.columns else [None] * n
    links = [tuple(links_of(v)) for v in gdf[GROWS_FROM]] if GROWS_FROM in gdf.columns else [()] * n
    groups, shapes = _groups_and_shapes(gdf, ids, drawn, own)
    return [
        Node(set_id=s.id, alert_id=aid, period_id=period.id,
             period_start=period.start, period_end=period.end, status=_status(status),
             area_ha=_float(area) or 0.0, date_min=_float(dmin), date_max=_float(dmax),
             shape=shape, links=lk, review_group=group)
        for aid, status, area, dmin, dmax, shape, lk, group in zip(
            ids, col("status"), col("area_ha"), col("alert_date_min"), col("alert_date_max"),
            shapes, links, groups)
    ]


def is_candidate(n: Node) -> bool:
    """A parent candidate's rule: a loss verdict with a shape."""
    return n.status in LOSS_VERDICTS and n.shape is not None


def rows_of_set(s: AlertSet, period: Period, gdf) -> list[Candidate]:
    """Every row of a set with the set's run id, whatever its status (the
    review package's snapshot holds such rows, ``snapshot_rows``)."""
    run_id = run_id_of(s)
    return [Candidate(n, run_id) for n in nodes_of_set(s, period, gdf)]


def group_candidates(nodes: Sequence[Node]) -> list[Node]:
    """The parent candidates among ``nodes`` (``is_candidate``), each review
    group folded into its main (spec 2026-10-26 §7.2): one node with the
    main's key, status and links, the union of the group's shapes (a drawn
    group's members have none, ``nodes_of_set``: there it is the drawing) and
    the group's summed area. A row alone, a group of one and the members of a
    main that is not among ``nodes`` stay as they are. Node order."""
    folded: dict[str, list[Node]] = {}
    for n in nodes:
        if n.is_member:
            folded.setdefault(node_key(n.set_id, n.review_group), []).append(n)
    mains = {n.key for n in nodes if n.key in folded}
    out = []
    for n in nodes:
        if n.is_member and node_key(n.set_id, n.review_group) in mains:
            continue
        members = folded.get(n.key) if n.key in mains else None
        if members:
            rows = [n, *members]
            shapes = [m.shape for m in rows if m.shape is not None]
            shape = None if not shapes else shapes[0] if len(shapes) == 1 else shapely.union_all(shapes)
            n = replace(n, shape=shape, area_ha=float(sum(m.area_ha for m in rows)))
        if is_candidate(n):
            out.append(n)
    return out


def candidates_of_set(s: AlertSet, period: Period, gdf) -> list[Candidate]:
    run_id = run_id_of(s)
    return [Candidate(n, run_id) for n in group_candidates(nodes_of_set(s, period, gdf))]


class LinkGraph:
    def __init__(self, nodes: Iterable[Node], run_ids: Mapping[str, str]):
        self._nodes = {n.key: n for n in nodes}
        self._run_ids = run_ids
        self._adj: dict[str, set[str]] = {k: set() for k in self._nodes}
        for key, n in self._nodes.items():
            for link in n.links:
                if self.is_broken(link):
                    continue
                target = node_key(*parse_link(link)[:2])
                self._adj[key].add(target)
                self._adj[target].add(key)
            if n.is_member:
                # a group is one history, links or none (spec 2026-10-26 §7.1)
                main = node_key(n.set_id, n.review_group)
                if main in self._nodes:
                    self._adj[key].add(main)
                    self._adj[main].add(key)

    def node(self, key: str) -> Optional[Node]:
        return self._nodes.get(key)

    def is_broken(self, link: str) -> bool:
        ref = parse_link(link)
        if ref is None or ref.set_id not in self._run_ids or self._run_ids[ref.set_id] != ref.run_id:
            return True
        return node_key(ref.set_id, ref.alert_id) not in self._nodes

    def _is_fp(self, key: str) -> bool:
        n = self._nodes.get(key)
        return n is not None and n.status == FALSE_POSITIVE

    def component(self, key: str, without: Optional[str] = None) -> frozenset[str]:
        """The history of ``key``; ``without``: a node left out, as if it
        were not in the graph."""
        if self._is_fp(key):
            return frozenset()
        if key not in self._nodes or key == without:
            return frozenset({key})
        seen, todo = {key}, [key]
        while todo:
            for nxt in self._adj[todo.pop()]:
                if nxt not in seen and nxt != without and not self._is_fp(nxt):
                    seen.add(nxt)
                    todo.append(nxt)
        return frozenset(seen)


def build_graph(nodes: Iterable[Node], run_ids: Mapping[str, str]) -> LinkGraph:
    return LinkGraph(nodes, run_ids)


@dataclass(frozen=True)
class HistoryArea:
    n: int
    confirmed_ha: float
    unreviewed_ha: float
    first_period_start: Optional[date]


def _union(shapes: list[BaseGeometry], crs: str):
    if not shapes:
        return None
    import geopandas as gpd

    projected = gpd.GeoSeries(shapes, crs="EPSG:4326").to_crs(crs)
    return shapely.union_all(projected.values)


def history_area(nodes: Iterable[Node], crs: str) -> HistoryArea:
    members = [n for n in nodes if n.status != FALSE_POSITIVE]
    confirmed = _union([n.shape for n in members if n.status in LOSS_VERDICTS and n.shape is not None], crs)
    unrev = _union([n.shape for n in members if n.status == NOT_REVIEWED and n.shape is not None], crs)
    if unrev is not None and confirmed is not None:
        unrev = unrev.difference(confirmed)
    return HistoryArea(
        n=len(members),
        confirmed_ha=0.0 if confirmed is None else confirmed.area / 10_000,
        unreviewed_ha=0.0 if unrev is None else unrev.area / 10_000,
        first_period_start=min((n.period_start for n in members), default=None),
    )


# ---- the review package's earlier alerts (spec §6.6) --------------------------

#: The snapshot's columns besides its geometry (the parent shape, EPSG:4326).
SNAPSHOT_COLUMNS = ("set_id", "alert_id", "run_id", "period_id", "period_start", "period_end", "status",
                    "area_ha", "date_min", "date_max", GROWS_FROM, REVIEW_GROUP)
#: the snapshot columns an older package may lack (read as their default)
_SNAPSHOT_OPTIONAL = (REVIEW_GROUP,)


def snapshot_rows(pool: Sequence[Candidate], seeds: Iterable[str]) -> list[Candidate]:
    """What a review package carries of the earlier sets, ``pool`` (every row
    of each, ``rows_of_set``): the parent candidates, then every row named by a
    link, followed from ``seeds`` (the shared set's links) and from the rows
    taken, whatever its status, so a copy resolves the links it holds as the
    creator's project does (a false positive shows broken, an unreviewed one
    counts as unreviewed). A row taken or named brings every row of its
    review group (spec 2026-10-26 §7.2): a drawn group's members, which have
    no shape, travel with their main, so the copy's graph is the creator's.
    Pool order within each part."""
    by_key = {r.key: r for r in pool}
    groups: dict[str, list[str]] = {}
    for r in pool:
        if r.node.review_group is not None:
            groups.setdefault(node_key(r.node.set_id, r.node.review_group), []).append(r.key)

    def _mates(r) -> list[str]:
        if r.node.review_group is None:
            return []
        mates = groups.get(node_key(r.node.set_id, r.node.review_group), [])
        return mates if len(mates) >= 2 else []

    def _key(link) -> Optional[str]:
        ref = parse_link(link)
        return node_key(ref.set_id, ref.alert_id) if ref is not None else None

    taken = {r.key for r in pool if is_candidate(r.node)}
    todo = [_key(lk) for lk in seeds]
    for r in pool:
        if r.key in taken:
            todo += [_key(lk) for lk in r.node.links] + _mates(r)
    named: set[str] = set()
    while todo:
        key = todo.pop()
        if key is None or key in taken or key in named or key not in by_key:
            continue
        named.add(key)
        r = by_key[key]
        todo += [_key(lk) for lk in r.node.links] + _mates(r)
    return [r for r in pool if r.key in taken] + [r for r in pool if r.key in named]


def write_snapshot(rows: Sequence[Candidate], path: Path) -> Path:
    """``rows`` as GeoParquet at ``path``: one row each (``SNAPSHOT_COLUMNS``,
    the links ``";"``-joined), its parent shape the geometry."""
    import geopandas as gpd

    def _floats(values):
        return pd.Series([np.nan if v is None else float(v) for v in values], dtype="float64")

    frame = gpd.GeoDataFrame({
        "set_id": [r.node.set_id for r in rows], "alert_id": [int(r.node.alert_id) for r in rows],
        "run_id": [r.run_id for r in rows], "period_id": [r.node.period_id for r in rows],
        "period_start": [r.node.period_start.isoformat() for r in rows],
        "period_end": [r.node.period_end.isoformat() for r in rows],
        "status": [r.node.status for r in rows], "area_ha": _floats(r.node.area_ha for r in rows),
        "date_min": _floats(r.node.date_min for r in rows), "date_max": _floats(r.node.date_max for r in rows),
        GROWS_FROM: [";".join(r.node.links) for r in rows],
        REVIEW_GROUP: pd.array([pd.NA if r.node.review_group is None else int(r.node.review_group)
                                for r in rows], dtype="Int64"),
    }, geometry=gpd.GeoSeries([r.node.shape for r in rows], crs="EPSG:4326"))
    frame.to_parquet(path, index=False)
    return Path(path)


def read_snapshot(path) -> list[Candidate]:
    """The rows ``write_snapshot`` wrote, each a ``Candidate`` (its node and
    its set's run id) whatever its status: only those ``is_candidate`` accepts
    are offered to tick. Raises on a file that is not such a snapshot (a
    missing column is a KeyError); one without ``review_group`` (an older
    package) reads as no groups."""
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    for name in _SNAPSHOT_OPTIONAL:
        if name not in frame.columns:
            frame[name] = pd.array([pd.NA] * len(frame), dtype="Int64")
    out = []
    for row, shape in zip(frame[list(SNAPSHOT_COLUMNS)].itertuples(index=False), frame.geometry):
        status = row.status if isinstance(row.status, str) and row.status else NOT_REVIEWED
        node = Node(
            set_id=str(row.set_id), alert_id=int(row.alert_id), period_id=str(row.period_id),
            period_start=date.fromisoformat(str(row.period_start)),
            period_end=date.fromisoformat(str(row.period_end)), status=status,
            area_ha=_float(row.area_ha) or 0.0, date_min=_float(row.date_min), date_max=_float(row.date_max),
            shape=_geometry(shape), links=tuple(links_of(row.grows_from)),
            review_group=None if pd.isna(row.review_group) else int(row.review_group),
        )
        out.append(Candidate(node, str(row.run_id)))
    return out
