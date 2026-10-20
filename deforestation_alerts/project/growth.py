"""Growth alerts (spec 2026-10-20): an alert in a later set that touches a
loss-verdict alert of an earlier set is stored with a ``grows_from`` link to
it. This module is the pure part: the link text, the alert kinds, the nodes
and parent candidates read from a store, the link graph and the area of a
group's history. It imports nothing from the UI."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.project.models import AlertSet, Period
from deforestation_alerts.project.review_status import FALSE_POSITIVE, LOSS_VERDICTS, NOT_REVIEWED
from deforestation_alerts.project.store_schema import GROWS_FROM

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

    @property
    def key(self) -> str:
        return node_key(self.set_id, self.alert_id)

    def link_to(self, run_id: str) -> str:
        return format_link(LinkRef(self.set_id, self.alert_id, run_id))


@dataclass(frozen=True)
class Candidate:
    """A parent candidate: a node whose status is a loss verdict."""

    node: Node
    run_id: str

    @property
    def key(self) -> str:
        return self.node.key

    @property
    def link(self) -> str:
        return self.node.link_to(self.run_id)


def _number(row, name) -> Optional[float]:
    if name not in row:
        return None
    value = row[name]
    if value is None or value is pd.NA or pd.isna(value):
        return None
    return float(value)


def nodes_of_set(s: AlertSet, period: Period, gdf) -> list[Node]:
    has_links = GROWS_FROM in gdf.columns
    nodes = []
    for _, row in gdf.iterrows():
        status = row["status"] if "status" in row else None
        nodes.append(Node(
            set_id=s.id, alert_id=int(row["alert_id"]), period_id=period.id,
            period_start=period.start, period_end=period.end,
            status=NOT_REVIEWED if status is None or status is pd.NA or pd.isna(status) or status == "" else status,
            area_ha=_number(row, "area_ha") or 0.0,
            date_min=_number(row, "alert_date_min"), date_max=_number(row, "alert_date_max"),
            shape=parent_shape(row),
            links=tuple(links_of(row[GROWS_FROM])) if has_links else (),
        ))
    return nodes


def candidates_of_set(s: AlertSet, period: Period, gdf) -> list[Candidate]:
    run_id = run_id_of(s)
    return [Candidate(n, run_id) for n in nodes_of_set(s, period, gdf)
            if n.status in LOSS_VERDICTS and n.shape is not None]


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

    def component(self, key: str) -> frozenset[str]:
        if self._is_fp(key):
            return frozenset()
        if key not in self._nodes:
            return frozenset({key})
        seen, todo = {key}, [key]
        while todo:
            for nxt in self._adj[todo.pop()]:
                if nxt not in seen and not self._is_fp(nxt):
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
