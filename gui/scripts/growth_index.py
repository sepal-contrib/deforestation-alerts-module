"""The GUI's view of growth links (spec 2026-10-20 §5.3, §6.2–6.3). Solara-free.

The Analysis "Earlier alerts" section, the History row, the Overview card,
exports and the Integrate Replace warning all read the project's sets through
here. Each set's nodes (``growth.nodes_of_set``) and parent candidates are read
once and kept, one entry per (project, set id), until the set's run id, store
generation or grid epsg changes (without a size cap: every call walks every
set, so an LRU smaller than the project would reload them all); the candidates' shapes are also kept in the grid CRS so
distances are cheap per alert.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal, NamedTuple, Optional

import geopandas as gpd
import shapely
import shapely.ops
from pyproj import Transformer

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project import paths, review_copy
from deforestation_alerts.project.growth import (
    Candidate,
    LinkGraph,
    Node,
    build_graph,
    history_area,
    is_candidate,
    kind_of,
    links_of,
    node_key,
    nodes_of_set,
    parse_link,
    read_snapshot,
    run_id_of,
)
from deforestation_alerts.project.review_status import FALSE_POSITIVE
from deforestation_alerts.project.store_schema import BELOW_MIN, GROWS_FROM, GROWS_FROM_AUTO
from deforestation_alerts.scripts.alert_store import store_generation
from gui.scripts import set_store

#: Earlier periods' colours, newest earlier period first (spec §6.3): bright
#: enough for satellite imagery, dark enough for SEPAL Light, distinct from the
#: current alert's gold.
PERIOD_PALETTE: tuple[str, ...] = (
    "#00e5ff",  # cyan
    "#ff9100",  # orange
    "#f50057",  # magenta
    "#76ff03",  # lime
    "#2979ff",  # blue
    "#d500f9",  # violet
    "#1de9b6",  # teal
    "#ffc400",  # amber
)
#: The colour every period older than the palette's eight shares: a blue-grey,
#: not the legends' "Not reviewed" grey (tests/test_legend_specs.py).
OLDER_GREY = "#b0bec5"

State = Literal["auto", "linked", "via", "none", "broken"]


def period_colors(period_ids_newest_first: Sequence[str]) -> dict[str, str]:
    return {pid: PERIOD_PALETTE[i] if i < len(PERIOD_PALETTE) else OLDER_GREY
            for i, pid in enumerate(period_ids_newest_first)}


class _Entry(NamedTuple):
    nodes: tuple            # every row of the set, as growth.Node
    cands: tuple            # the loss-verdict rows with a shape, as growth.Candidate
    metric: Optional[list]  # the candidates' shapes in the grid CRS (None without a grid)


class _Snapshot(NamedTuple):
    rows: tuple             # every row of the package snapshot (growth.read_snapshot)
    cands: tuple            # those offered to tick (growth.is_candidate)
    metric: Optional[list]  # the candidates' shapes in the grid CRS (None without a grid)


#: ``{(project name, set id): (key, entry)}``; ``key`` is what the entry was read for.
_cache: "dict[tuple, tuple[tuple, _Entry]]" = {}
#: ``{project name: (key, snapshot)}``: a review copy's earlier alerts.
_snapshots: "dict[str, tuple[tuple, _Snapshot]]" = {}
_lock = threading.Lock()
_NO_SNAPSHOT = _Snapshot((), (), None)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _snapshots.clear()


def _grid_epsg(p):
    return p.grid.epsg if getattr(p, "grid", None) is not None else None


def _to_crs(shapes, crs) -> list:
    if not shapes:
        return []
    return list(gpd.GeoSeries(list(shapes), crs="EPSG:4326").to_crs(crs))


def _build(p, s, gdf) -> _Entry:
    period = p.periods.get(s.period_id)
    if gdf is None or period is None or len(gdf) == 0:
        return _Entry((), (), [] if _grid_epsg(p) else None)
    nodes = tuple(nodes_of_set(s, period, gdf))
    run_id = run_id_of(s)
    # growth.candidates_of_set's rule, from the nodes already read (one pass over the rows)
    cands = tuple(Candidate(n, run_id) for n in nodes if is_candidate(n))
    epsg = _grid_epsg(p)
    return _Entry(nodes, cands, _to_crs([c.node.shape for c in cands], epsg) if epsg else None)


def _entry(p, s, load) -> _Entry:
    slot = (p.name, s.id)
    key = (run_id_of(s), set_store.store_generation(set_store.store_path(p, s)), _grid_epsg(p))
    with _lock:
        hit = _cache.get(slot)
        if hit is not None and hit[0] == key:
            return hit[1]
    entry = _build(p, s, load(p, s))
    with _lock:
        _cache[slot] = (key, entry)
        # sets removed from the project are dropped with their entries
        for gone in [k for k in _cache if k[0] == p.name and k[1] not in p.alert_sets]:
            del _cache[gone]
    return entry


def _snapshot(p) -> _Snapshot:
    """A review copy's package snapshot (spec §6.6), read once per write of
    its file (and grid epsg); empty without one."""
    path = paths.project_dir(p.name) / paths.EARLIER_SNAPSHOT
    key = (store_generation(path), _grid_epsg(p))
    if key[0] is None:
        return _NO_SNAPSHOT
    with _lock:
        hit = _snapshots.get(p.name)
        if hit is not None and hit[0] == key:
            return hit[1]
    rows = tuple(read_snapshot(path))
    cands = tuple(c for c in rows if is_candidate(c.node))
    epsg = _grid_epsg(p)
    snap = _Snapshot(rows, cands, _to_crs([c.node.shape for c in cands], epsg) if epsg else None)
    with _lock:
        _snapshots[p.name] = (key, snap)
    return snap


def snapshot_rows(p) -> list[Candidate]:
    """Every row of a review copy's package snapshot, each with its set's run
    id at packaging time, whatever its status: the earlier nodes of the copy's
    link graph. ``[]`` without a snapshot."""
    return list(_snapshot(p).rows)


def snapshot_candidates(p) -> list[Candidate]:
    """A review copy's parent candidates, from its package snapshot (spec
    §6.6); ``[]`` without one (a package from before the snapshot)."""
    return list(_snapshot(p).cands)


def _earlier(p, set_id: str) -> list:
    cur = p.alert_sets.get(set_id)
    period = p.periods.get(cur.period_id) if cur is not None else None
    return earlier_sets(p, period) if period is not None else []


def _candidate_pairs(p, set_id: str, load) -> list[tuple[Candidate, object]]:
    """``(candidate, its shape in the grid CRS or None)``, both read from the
    same cache entries (an entry rebuilt meanwhile cannot mismatch them)."""
    if review_copy.read_only(p):
        snap = _snapshot(p)
        return list(zip(snap.cands, snap.metric) if snap.metric is not None else ((c, None) for c in snap.cands))
    pairs = []
    for s in _earlier(p, set_id):
        e = _entry(p, s, load)
        pairs.extend(zip(e.cands, e.metric) if e.metric is not None else ((c, None) for c in e.cands))
    return pairs


def candidates_for(p, set_id: str, *, load=set_store.load_set_alerts) -> list[Candidate]:
    """The parent candidates of ``set_id``: the loss-verdict alerts of every
    set of an earlier period, or a review copy's snapshot."""
    return [c for c, _ in _candidate_pairs(p, set_id, load)]


def _with_links(nodes, alert_id, links):
    """``nodes`` with alert ``alert_id``'s links replaced by ``links`` (draft ticks)."""
    if alert_id is None or links is None:
        return nodes
    return [replace(n, links=tuple(links_of(list(links)))) if n.alert_id == int(alert_id) else n
            for n in nodes]


def _parts(p, set_id, gdf_current, load, alert_id=None, links=None) -> tuple[list[Node], dict[str, str]]:
    """The graph's nodes and run ids. In a review copy the earlier ones are
    the package snapshot's (every row it holds, so a link to a since false
    positive or unreviewed parent resolves as in the creator's project), each
    set with the run id it had at packaging time."""
    cur = p.alert_sets.get(set_id)
    period = p.periods.get(cur.period_id) if cur is not None else None
    nodes: list[Node] = []
    run_ids = {s.id: run_id_of(s) for s in p.alert_sets.values()}
    if review_copy.read_only(p):
        for c in _snapshot(p).rows:
            nodes.append(c.node)
            run_ids.setdefault(c.node.set_id, c.run_id)
    for s in p.alert_sets.values():
        if s.id != set_id:
            nodes.extend(_entry(p, s, load).nodes)
    current = nodes_of_set(cur, period, gdf_current) if gdf_current is not None and period is not None else []
    nodes.extend(_with_links(current, alert_id, links))
    return nodes, run_ids


def _graph(p, set_id, gdf_current, load, alert_id=None, links=None) -> LinkGraph:
    return build_graph(*_parts(p, set_id, gdf_current, load, alert_id, links))


def graph_for(p, set_id: str, gdf_current, *, load=set_store.load_set_alerts) -> LinkGraph:
    """The link graph of the whole project: ``set_id``'s live rows, every other
    set's stored rows (later and parallel sets too, so a history grows
    forwards) and, in a review copy, the package snapshot's rows."""
    return _graph(p, set_id, gdf_current, load)


@dataclass(frozen=True)
class EarlierItem:
    cand: Candidate
    distance_m: float
    state: State
    via: Optional[str]
    color: str


@dataclass(frozen=True)
class EarlierView:
    items: tuple[EarlierItem, ...]
    n_within: int
    n_linked: int
    periods: tuple[tuple[str, date], ...]  # newest first


def _row_shape(gdf, alert_id):
    if gdf is None or len(gdf) == 0:
        return None
    rows = gdf[gdf["alert_id"] == int(alert_id)]
    if rows.empty:
        return None
    g = rows.iloc[0].geometry
    return None if g is None or g.is_empty else g


def _metric_crs(p, shape):
    epsg = _grid_epsg(p)
    if epsg is not None or shape is None:
        return epsg
    return gpd.GeoSeries([shape], crs="EPSG:4326").estimate_utm_crs()


def _distances(pairs, shape, crs) -> dict:
    """``{candidate link: metres}`` from ``shape`` (EPSG:4326) to each candidate
    of ``pairs`` (``_candidate_pairs``; a missing metric shape is projected here)."""
    if shape is None or crs is None or not pairs:
        return {}
    # a Transformer is not thread-safe: one per call, never cached
    here = shapely.ops.transform(Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform, shape)
    todo = [c.node.shape for c, m in pairs if m is None]
    projected = iter(_to_crs(todo, crs))
    metric = [m if m is not None else next(projected) for _, m in pairs]
    return {c.link: float(d) for (c, _), d in zip(pairs, shapely.distance(metric, here))}


def _via(nodes, run_ids, current: str, seeds: Sequence[str]) -> dict:
    """``{member key: seed key}``: which of ``seeds`` (the current alert's
    neighbours) each member of its history hangs from, the current alert left out."""
    rest = build_graph([n for n in nodes if n.key != current], run_ids)
    out: dict[str, str] = {}
    for seed in seeds:
        for key in rest.component(seed):
            out.setdefault(key, seed)
    return out


def earlier_view(p, set_id: str, gdf_current, alert_id: int, links: Sequence[str], auto: Sequence[str],
                 radius_m: float, *, load=set_store.load_set_alerts) -> EarlierView:
    """The "Earlier alerts" list of one alert (spec §6.2): candidates within
    ``radius_m`` of its shape, every alert named by ``links`` (the draft ticks)
    and the history members reached through another link, newest period first,
    then nearest."""
    links = links_of(list(links))
    auto = set(links_of(list(auto)))
    pairs = _candidate_pairs(p, set_id, load)
    cands = [c for c, _ in pairs]
    nodes, run_ids = _parts(p, set_id, gdf_current, load, alert_id, links)
    graph = build_graph(nodes, run_ids)
    shape = _row_shape(gdf_current, alert_id)
    crs = _metric_crs(p, shape)
    dist = _distances(pairs, shape, crs)
    by_link = {c.link: c for c in cands}

    items: dict[str, tuple] = {}  # link -> (candidate, state, via)
    valid: list[str] = []
    for link in links:
        ref = parse_link(link)
        if ref is None:
            continue
        key = node_key(ref.set_id, ref.alert_id)
        node = graph.node(key)
        if node is None:
            continue  # its set is gone: nothing to show
        broken = graph.is_broken(link) or node.status == FALSE_POSITIVE
        cand = by_link.get(link) or Candidate(node, ref.run_id)
        items[cand.link] = (cand, "broken" if broken else "auto" if link in auto else "linked", None)
        if not broken:
            valid.append(key)

    current = node_key(set_id, alert_id)
    members = graph.component(current)
    if len(members) > 1:
        nodes_by_key = {k: graph.node(k) for k in members if graph.node(k) is not None}
        # neighbours: the linked parents first, then later alerts linking to this one
        children = [k for k, n in nodes_by_key.items()
                    if any(not graph.is_broken(lk) and node_key(*parse_link(lk)[:2]) == current for lk in n.links)]
        hangs = _via(nodes, run_ids, current, valid + children)
        for c in cands:
            if c.key in members and c.link not in items and c.key not in valid:
                items[c.link] = (c, "via", hangs.get(c.key))
    for c in cands:
        if c.link not in items and dist.get(c.link, float("inf")) <= radius_m:
            items[c.link] = (c, "none", None)

    # the palette order comes from the candidates only, so a period keeps its
    # colour from one alert to the next; a broken or FP item of another period is grey
    periods = sorted({(c.node.period_id, c.node.period_start) for c in cands},
                     key=lambda pp: (pp[1], pp[0]), reverse=True)
    colors = period_colors([pid for pid, _ in periods])
    out = []
    for link, (c, state, via) in items.items():
        d = dist.get(link)
        if d is None and c.node.shape is not None and shape is not None and crs is not None:
            d = float(shapely.distance(*_to_crs([c.node.shape, shape], crs)))
        out.append(EarlierItem(c, float("inf") if d is None else d, state, via, colors.get(c.node.period_id, OLDER_GREY)))
    out.sort(key=lambda it: (-it.cand.node.period_start.toordinal(), it.distance_m))
    return EarlierView(
        items=tuple(out),
        n_within=sum(1 for it in out if it.state != "broken" and it.distance_m <= radius_m),
        n_linked=sum(1 for it in out if it.state in ("auto", "linked")),
        periods=tuple(periods),
    )


@dataclass(frozen=True)
class HistorySummary:
    n: int
    confirmed_ha: float
    unreviewed_ha: float
    since: Optional[date]


def history_summary(p, set_id: str, gdf_current, alert_id: int, links: Sequence[str], *,
                    load=set_store.load_set_alerts) -> Optional[HistorySummary]:
    """The History row (spec §6.1) of one alert, with ``links`` (the draft
    ticks) in place of its stored ones; None when it has no history."""
    graph = _graph(p, set_id, gdf_current, load, alert_id, links)
    members = graph.component(node_key(set_id, alert_id))
    nodes = [graph.node(k) for k in members if graph.node(k) is not None]
    if len(nodes) < 2:
        return None
    shapes = [n.shape for n in nodes if n.shape is not None]
    crs = _grid_epsg(p) or (_metric_crs(p, shapes[0]) if shapes else "EPSG:3857")
    area = history_area(nodes, crs)
    return HistorySummary(area.n, area.confirmed_ha, area.unreviewed_ha, area.first_period_start)


def with_growth_columns(p, set_id: str, gdf, *, load=set_store.load_set_alerts):
    """A copy of ``gdf`` (the rows of ``set_id``) ready to export (spec §6.7):
    ``kind``, ``grows_from`` as the stored links joined by ``";"`` (broken ones
    too: the file records what is stored) and ``history_area_ha`` (the
    ``confirmed_ha`` of the row's history, 0 for a row alone); the working
    columns ``grows_from_auto`` and ``below_min`` are left out. The graph is
    built once and each history's area computed once, whatever its size."""
    out = gdf.copy()
    graph = graph_for(p, set_id, gdf, load=load)
    links = [links_of(cell) for cell in (out[GROWS_FROM] if GROWS_FROM in out.columns else [None] * len(out))]
    small = out[BELOW_MIN] if BELOW_MIN in out.columns else [None] * len(out)
    areas: dict[frozenset, float] = {}  # one area per history

    def _area(alert_id) -> float:
        members = graph.component(node_key(set_id, alert_id))
        if members not in areas:
            nodes = [graph.node(k) for k in members if graph.node(k) is not None]
            if len(nodes) < 2:
                areas[members] = 0.0
            else:
                shapes = [n.shape for n in nodes if n.shape is not None]
                crs = _grid_epsg(p) or (_metric_crs(p, shapes[0]) if shapes else "EPSG:3857")
                areas[members] = history_area(nodes, crs).confirmed_ha
        return areas[members]

    out["kind"] = [kind_of(lk, flag) for lk, flag in zip(links, small)]
    out[GROWS_FROM] = [";".join(lk) for lk in links]
    out["history_area_ha"] = [_area(a) for a in out["alert_id"]]
    return out.drop(columns=[c for c in (GROWS_FROM_AUTO, BELOW_MIN) if c in out.columns])


def parent_windows(view: EarlierView) -> tuple[tuple[float, float], ...]:
    """The detection windows of the linked parents (spec §6.5), merged when they overlap."""
    spans = sorted((it.cand.node.date_min, it.cand.node.date_max) for it in view.items
                   if it.state in ("auto", "linked")
                   and it.cand.node.date_min is not None and it.cand.node.date_max is not None)
    merged: list[list[float]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return tuple((lo, hi) for lo, hi in merged)


def links_into(p, set_id: str, *, load=set_store.load_set_alerts) -> int:
    """Rows of the other sets holding a valid link into ``set_id`` (its
    current run): what a re-run of ``set_id`` would break (spec §5.3)."""
    target = p.alert_sets.get(set_id)
    if target is None:
        return 0
    run_id = run_id_of(target)

    def _into(link) -> bool:
        ref = parse_link(link)
        return ref is not None and ref.set_id == set_id and ref.run_id == run_id

    return sum(1 for s in p.alert_sets.values() if s.id != set_id
               for n in _entry(p, s, load).nodes if any(_into(lk) for lk in n.links))

