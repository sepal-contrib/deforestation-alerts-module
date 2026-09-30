"""The active alert set's files: its store, its Overview tiles, the centroid
fallback and a map hit test (spec §9.2, §9.3). Solara-free.

The tiles are out of date when ``alert_db.parquet`` is newer than
``alerts.pmtiles`` (spec §5.3: a review save rewrites the store). No pmtiles
artifact is recorded in the manifest (ruling #79): the two mtimes decide.
"""

from pathlib import Path
from typing import Optional

from shapely.geometry import Point, mapping

from deforestation_alerts.engine import pmtiles as pmt
from deforestation_alerts.project import paths
from gui.store.workflow_steps import active_set


def _store(p, s) -> Path:
    return paths.resolve(paths.project_dir(p.name), s.store_rel_path)


def set_folder(p, s) -> Path:
    """The set's own folder: its store, tiles and exports live there."""
    return _store(p, s).parent


def active_store_path(p) -> Optional[Path]:
    s = active_set(p)
    return _store(p, s) if s is not None else None


def load_active_alerts(p, workspace):
    """The active set's alerts, or None when there is no set or its store is gone."""
    from deforestation_alerts.scripts.recipe_helper import load_gdf_from_parquet

    store = active_store_path(p)
    if store is None or not store.exists():
        return None
    return load_gdf_from_parquet(workspace, str(store))


def _pmtiles_path(p, s) -> Path:
    return paths.pmtiles_path(set_folder(p, s))


def pmtiles_stale(p, s) -> bool:
    """True when the set's tiles are missing or older than its store."""
    pm = _pmtiles_path(p, s)
    if not pm.exists():
        return True
    store = _store(p, s)
    return store.exists() and store.stat().st_mtime > pm.stat().st_mtime


def ensure_pmtiles(p, s, alerts) -> Optional[Path]:
    """The set's tiles, rebuilt from ``alerts`` first when stale; None without
    tippecanoe. Blocking (tippecanoe): call from a worker thread.
    """
    if not pmt.tippecanoe_available():
        return None
    pm = _pmtiles_path(p, s)
    if pmtiles_stale(p, s):
        pmt.alerts_to_pmtiles(alerts, pm)
    return pm


def centroid_geojson(gdf) -> dict:
    """The alerts as WGS84 centroid points, for the map when the tiles cannot
    be shown (spec §9.2, §12). Each point keeps ``alert_id`` and the
    ``status_code`` the tile style colours by.
    """
    features = []
    if gdf is not None and len(gdf):
        g = gdf.to_crs(epsg=4326) if gdf.crs is not None else gdf
        ids = g["alert_id"] if "alert_id" in g.columns else range(len(g))
        statuses = g["status"] if "status" in g.columns else [None] * len(g)
        for geom, alert_id, status in zip(g.geometry, ids, statuses):
            if geom is None or geom.is_empty:
                continue
            features.append({
                "type": "Feature",
                "geometry": mapping(geom.centroid),
                "properties": {"alert_id": int(alert_id), "status_code": pmt.STATUS_CODES.get(status, 0)},
            })
    return {"type": "FeatureCollection", "features": features}


def hit_alert(gdf, lon: float, lat: float, tolerance_deg: float = 0.005) -> Optional[int]:
    """Positional index of the alert under (lon, lat), else of the nearest one
    within ``tolerance_deg`` degrees, else None.
    """
    if gdf is None or len(gdf) == 0:
        return None
    pt = Point(lon, lat)
    idx = list(gdf.sindex.query(pt, predicate="intersects"))
    if idx:
        return int(idx[0])
    near = gdf.sindex.nearest(pt, max_distance=tolerance_deg, return_all=False)
    return int(near[1][0]) if near.shape[1] else None
