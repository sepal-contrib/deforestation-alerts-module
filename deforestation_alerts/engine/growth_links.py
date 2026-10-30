"""Growth links at Integrate (spec 2026-10-20 §4.2).

The parent candidates are the loss-verdict alerts of the earlier sets, read
from their stores when Integrate runs (§4.4). Two stages use them: the touch
mask, burnt on the grid, lets ``vectorize_alerts`` keep a cluster under the
minimum size that reaches a parent; then each kept cluster is linked to every
parent within one pixel of it, edge to edge (§4.1), and a small one that finds
none is dropped back to too small.
"""

from pathlib import Path
from typing import Optional, Sequence

import geopandas as gpd
from shapely import STRtree

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project import paths
from deforestation_alerts.project.growth import Candidate, group_candidates, rows_of_set
from deforestation_alerts.project.models import GridSpec, Period, Project
from deforestation_alerts.project.store_schema import BELOW_MIN, GROWS_FROM_AUTO
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet

#: How far a parent reaches, in pixels of the grid, edge to edge: a cluster one
#: empty pixel away from it still grows from it (a diagonal gap is farther).
REACH_PIXELS = 1.0
#: The parents are stored in EPSG:4326 and come back to the grid with rounding
#: error, so a gap of exactly one pixel is measured with this slack.
_SLACK_PIXELS = 0.01


def project_rows(p: Project, pdir: Path, period: Period) -> list[Candidate]:
    """Every alert of every earlier set of ``period`` (``growth.rows_of_set``),
    as their stores hold them now. A set whose store is missing gives none."""
    out: list[Candidate] = []
    for s in earlier_sets(p, period):
        store = paths.resolve(pdir, s.store_rel_path)
        if not store.exists():
            continue
        out += rows_of_set(s, p.periods[s.period_id], load_gdf_from_parquet(store))
    return out


def project_candidates(p: Project, pdir: Path, period: Period) -> list[Candidate]:
    """The parent candidates of ``period``: every earlier set's loss-verdict
    alerts, as their stores hold them now, a review group as one candidate
    keyed to its main (``growth.group_candidates``). A set whose store is
    missing gives none."""
    rows = project_rows(p, pdir, period)
    run_ids = {c.node.set_id: c.run_id for c in rows}
    return [Candidate(n, run_ids[n.set_id]) for n in group_candidates([c.node for c in rows])]


def _grid_shapes(cands: Sequence[Candidate], grid: GridSpec) -> gpd.GeoSeries:
    """The candidates' shapes (stored in EPSG:4326) in the grid's CRS."""
    return gpd.GeoSeries([c.node.shape for c in cands], crs="EPSG:4326").to_crs(grid.epsg)


def burn_touch_mask(cands: Sequence[Candidate], grid: GridSpec, out_path: Path) -> Optional[Path]:
    """The 0/1 touch mask on ``grid`` (Int32, nodata 0): the parents buffered
    by half a pixel more than their reach, burnt all-touched, so a cluster
    within reach of one has a pixel on it. The mask may hold a few pixels just
    out of reach (a diagonal gap): ``link_parents`` measures exactly and drops
    the small clusters they kept. None when there is no candidate."""
    if not cands:
        return None
    # integrate imports this module; its burn is imported here, at call time
    from deforestation_alerts.engine.integrate import rasterize_footprint

    buffered = _grid_shapes(cands, grid).buffer((REACH_PIXELS + 0.5) * grid.resolution)
    return rasterize_footprint(gpd.GeoDataFrame(geometry=buffered), grid, out_path, all_touched=True)


def link_parents(vgdf: gpd.GeoDataFrame, cands: Sequence[Candidate], grid: GridSpec) -> gpd.GeoDataFrame:
    """``vgdf`` (the vectorizer's clusters, grid CRS, with ``below_min``) with
    ``grows_from_auto``: the sorted links of every candidate within
    ``REACH_PIXELS`` of it. A ``below_min`` cluster with no link is dropped;
    the index is reset."""
    out = vgdf.copy()
    links: list[set[str]] = [set() for _ in range(len(out))]
    if cands and len(out):
        # the tree's "dwithin" predicate is shapely.dwithin(cluster, parent, distance)
        tree = STRtree(_grid_shapes(cands, grid).values)
        reach = (REACH_PIXELS + _SLACK_PIXELS) * grid.resolution
        clusters, parents = tree.query(out.geometry.values, predicate="dwithin", distance=reach)
        for i, j in zip(clusters.tolist(), parents.tolist()):
            links[i].add(cands[j].link)
    out[GROWS_FROM_AUTO] = [sorted(s) for s in links]
    keep = ~out[BELOW_MIN].astype(bool) | out[GROWS_FROM_AUTO].map(bool)
    return out[keep].reset_index(drop=True)
