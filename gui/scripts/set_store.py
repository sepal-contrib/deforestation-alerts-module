"""The active alert set's files: its store, its Overview tiles, the centroid
fallback and a map hit test (spec §9.2, §9.3); and handing a loaded store to
the session (``publish_set_alerts``). Solara-free.

The tiles are out of date when ``alert_db.parquet`` is newer than
``alerts.pmtiles`` (spec §5.3: a review save rewrites the store). No pmtiles
artifact is recorded in the manifest (ruling #79): the two mtimes decide. So
tiles are only ever built from the set's own alerts (``alerts_belong_to``).
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
    from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet

    store = active_store_path(p)
    if store is None or not store.exists():
        return None
    return load_gdf_from_parquet(workspace, str(store))


def publish_set_alerts(app_state, name: str, s, gdf) -> bool:
    """Hand set ``s``'s loaded alerts to Analysis, at the set's saved review
    position, while ``s`` is still project ``name``'s active set. Returns
    False, touching nothing, once the project was switched or closed or
    another set became active.

    ``actual_alert_id`` goes to -1 first, as Integrate's publish does:
    Analysis re-selects only from -1, and ``view_actual_alert`` follows only
    ``actual_alert_id``, so the same index in another set would otherwise keep
    showing the previous set's alert.
    """
    live = app_state.project.value
    if live is None or live.name != name or live.active_set != s.id:
        return False
    app_state.actual_alert_id.set(-1)
    app_state.alerts_gdf.set(gdf)
    saved = live.alert_sets[s.id].last_alert_index
    app_state.actual_alert_id.set(min(saved, len(gdf) - 1) if gdf is not None and len(gdf) else -1)
    return True


def _pmtiles_path(p, s) -> Path:
    return paths.pmtiles_path(set_folder(p, s))


def pmtiles_stale(p, s) -> bool:
    """True when the set's tiles are missing or older than its store."""
    pm = _pmtiles_path(p, s)
    if not pm.exists():
        return True
    store = _store(p, s)
    return store.exists() and store.stat().st_mtime > pm.stat().st_mtime


def alerts_belong_to(gdf, s) -> bool:
    """False when ``gdf`` holds alerts of another set than ``s``.

    The engine writes each alert's ``set_id`` into the store (integrate.py).
    No alerts, and alerts without that column (a store written before it, the
    legacy feeds), hold nothing of another set, so they pass.
    ``alerts_gdf`` and the project's active set are separate reactives, and
    Integrate records a new set before it publishes that set's alerts: this is
    how Overview tells that the alerts on hand are not the active set's yet.
    """
    if gdf is None or "set_id" not in gdf.columns:
        return True
    return bool((gdf["set_id"] == s.id).all())


def holds_set(gdf, s) -> bool:
    """True when ``gdf`` are set ``s``'s alerts: at least one row, and every
    row carries ``s``'s id. Unlike ``alerts_belong_to``, nothing, no rows or
    rows without a ``set_id`` hold no set."""
    return gdf is not None and "set_id" in gdf.columns and len(gdf) > 0 and alerts_belong_to(gdf, s)


def ensure_pmtiles(p, s, alerts) -> Optional[Path]:
    """The set's tiles, rebuilt from ``alerts`` first when stale; None without
    tippecanoe. Blocking (tippecanoe): call from a worker thread.

    Raises ValueError, writing nothing, when ``alerts`` hold another set's
    alerts: tiles built from them would be newer than this set's store, so
    ``pmtiles_stale`` would keep them as current.
    """
    if not alerts_belong_to(alerts, s):
        raise ValueError(f"these alerts are not set {s.id!r}'s: its tiles are not built from them")
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
