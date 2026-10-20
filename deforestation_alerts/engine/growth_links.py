"""Growth links at Integrate (spec 2026-10-20 §4.2).

The parent candidates are the loss-verdict alerts of the earlier sets, read
from their stores when Integrate runs (§4.4). Two stages use them: the touch
mask, burnt on the grid, lets ``vectorize_alerts`` keep a cluster under the
minimum size that reaches a parent; then each kept cluster is linked to every
parent within half a pixel of it (§4.1), and a small one that finds none is
dropped back to too small.
"""

from pathlib import Path
from typing import Optional, Sequence

import geopandas as gpd
from shapely import STRtree

from deforestation_alerts.engine.fingerprint import earlier_sets
from deforestation_alerts.project import paths
from deforestation_alerts.project.growth import Candidate, is_candidate, rows_of_set
from deforestation_alerts.project.models import GridSpec, Period, Project
from deforestation_alerts.project.store_schema import BELOW_MIN, GROWS_FROM_AUTO
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet


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
    alerts, as their stores hold them now. A set whose store is missing gives none."""
    return [c for c in project_rows(p, pdir, period) if is_candidate(c.node)]


def _grid_shapes(cands: Sequence[Candidate], grid: GridSpec) -> gpd.GeoSeries:
    """The candidates' shapes (stored in EPSG:4326) in the grid's CRS."""
    return gpd.GeoSeries([c.node.shape for c in cands], crs="EPSG:4326").to_crs(grid.epsg)


def burn_touch_mask(cands: Sequence[Candidate], grid: GridSpec, out_path: Path) -> Optional[Path]:
    """The 0/1 touch mask on ``grid`` (Int32, nodata 0): the parents buffered
    by half a pixel, burnt all-touched, so a cluster sharing an edge or a
    corner with one has a pixel on it. None when there is no candidate."""
    if not cands:
        return None
    # integrate imports this module; its burn is imported here, at call time
    from deforestation_alerts.engine.integrate import rasterize_footprint

    buffered = _grid_shapes(cands, grid).buffer(0.5 * grid.resolution)
    return rasterize_footprint(gpd.GeoDataFrame(geometry=buffered), grid, out_path, all_touched=True)


def link_parents(vgdf: gpd.GeoDataFrame, cands: Sequence[Candidate], grid: GridSpec) -> gpd.GeoDataFrame:
    """``vgdf`` (the vectorizer's clusters, grid CRS, with ``below_min``) with
    ``grows_from_auto``: the sorted links of every candidate within half a
    pixel. A ``below_min`` cluster with no link is dropped; the index is reset."""
    out = vgdf.copy()
    links: list[set[str]] = [set() for _ in range(len(out))]
    if cands and len(out):
        # the tree's "dwithin" predicate is shapely.dwithin(cluster, parent, distance)
        tree = STRtree(_grid_shapes(cands, grid).values)
        clusters, parents = tree.query(out.geometry.values, predicate="dwithin", distance=0.5 * grid.resolution)
        for i, j in zip(clusters.tolist(), parents.tolist()):
            links[i].add(cands[j].link)
    out[GROWS_FROM_AUTO] = [sorted(s) for s in links]
    keep = ~out[BELOW_MIN].astype(bool) | out[GROWS_FROM_AUTO].map(bool)
    return out[keep].reset_index(drop=True)
