"""The active alert set's files: its store, its Overview tiles, the centroid
fallback and a map hit test (spec §9.2, §9.3); and handing a loaded store to
the session (``publish_set_alerts``). Solara-free.

The tiles are out of date when ``alert_db.parquet`` is newer than
``alerts.pmtiles`` (spec §5.3: a review save rewrites the store). No pmtiles
artifact is recorded in the manifest (ruling #79): the two mtimes decide. So
the tiles are only ever built from the store on disk (``ensure_pmtiles``),
never from the alerts a session holds: during a re-run of the same set those
are the previous alerts, and tiles built from them would be newer than the
new store, so the mtime rule would keep them. A build is skipped while the
set's project is being written (an Integrate, a review Save) and retried
once the writer is done, and a build that finds the store rewritten under it
discards what it built.

A review Save writes the alerts a session holds back over the store. Those
alerts are stamped with the generation of the store they were read from
(``stamp_generation``: on load, when Integrate publishes a set it just wrote,
and after each Save), and a Save is refused when the store on disk is not that
generation any more (``generation_changed``): during a re-run of the same set
the store is swapped under the session, and the old alerts plus a review would
otherwise overwrite the new set, unnoticed (the set id and the manifest
record are the new set's).
"""

import logging
from pathlib import Path
from typing import Callable, Optional

from shapely.geometry import Point, mapping

from deforestation_alerts.engine import pmtiles as pmt
from deforestation_alerts.project import paths
from gui.store.project_writers import is_writing, writing
from gui.store.workflow_steps import active_set

logger = logging.getLogger("SEPALUI.deforestation_alerts")


class StoreBusy(RuntimeError):
    """The set's tiles were not (re)built: its project is being written, or
    its store was rewritten while the tiles were being built from it."""


def _store(p, s) -> Path:
    return paths.resolve(paths.project_dir(p.name), s.store_rel_path)


def set_folder(p, s) -> Path:
    """The set's own folder: its store, tiles and exports live there."""
    return _store(p, s).parent


def active_store_path(p) -> Optional[Path]:
    s = active_set(p)
    return _store(p, s) if s is not None else None


def load_active_alerts(p):
    """The active set's alerts, stamped with their store's generation, or None
    when there is no set or its store is gone."""
    from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet

    store = active_store_path(p)
    if store is None or not store.exists():
        return None
    # The generation is read before the store: a store replaced during the
    # read then reads as changed, and a Save of what was read is refused.
    generation = store_generation(store)
    gdf = load_gdf_from_parquet(store)
    gdf.attrs[STORE_GENERATION] = generation
    return gdf


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


#: ``gdf.attrs`` key: the generation of the store the alerts were read from
STORE_GENERATION = "store_generation"


def store_generation(store) -> Optional[tuple]:
    """Which write of ``store`` is on disk: ``(st_mtime_ns, st_size, st_ino)``,
    or None when the file is missing. Every writer (Integrate's swap, a review
    Save's upload) replaces the file, so a rewrite changes it."""
    try:
        st = Path(store).stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def stamp_generation(gdf, store) -> None:
    """Mark ``gdf`` as read from (or just written to) the ``store`` on disk
    now. pandas carries ``attrs`` through ``copy``, slicing and ``to_crs``."""
    if gdf is not None:
        gdf.attrs[STORE_GENERATION] = store_generation(store)


def generation_changed(gdf, store) -> bool:
    """True when ``gdf`` was stamped and ``store`` is no longer that
    generation: the alerts on hand are not what is on disk, so they must not
    be written back. Unstamped alerts (recipe-era callers, test fakes) are
    not checked."""
    stamped = gdf.attrs.get(STORE_GENERATION) if gdf is not None else None
    return stamped is not None and store_generation(store) != stamped


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


def ensure_pmtiles(p, s, on_wait: Optional[Callable[[], None]] = None) -> Optional[Path]:
    """The set's tiles, rebuilt first from the store on disk when they are
    stale; None without tippecanoe. Blocking (tippecanoe): call from a worker
    thread.

    Raises ``StoreBusy``, leaving the tiles as they are, when they are stale
    while the set's project is being written (``project_writers``: an
    Integrate swapping the store in, a review Save), or when the store was
    rewritten while the tiles were being built from it: either way the tiles
    would not match the store that ends up on disk. The caller shows what it
    can meanwhile and asks again once the writer is done. The build itself
    marks the project as being written (spec §12), after that check.

    ``on_wait``, when given, is called right before a ``StoreBusy`` is raised,
    while the build's own writer mark (if it took one) is still held. A caller
    that asks again once the project's writers are done (Overview) notes there
    that it has to: the release of that mark is what tells it the writers are
    done, and it comes before the exception reaches the caller.
    """
    from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet

    if not pmt.tippecanoe_available():
        return None
    pm = _pmtiles_path(p, s)
    if not pmtiles_stale(p, s):
        return pm
    if is_writing(p.name):
        _about_to_wait(on_wait)
        raise StoreBusy(f"project {p.name!r} is being written: the tiles of set {s.id!r} are not rebuilt now")
    with writing(p.name):
        store = _store(p, s)
        generation = store_generation(store)
        alerts = load_gdf_from_parquet(store)

        def _still_the_store_read():
            if store_generation(store) != generation:
                _about_to_wait(on_wait)
                raise StoreBusy(f"the store of set {s.id!r} was rewritten while its tiles were built")

        pmt.alerts_to_pmtiles(alerts, pm, guard=_still_the_store_read)
    return pm


def _about_to_wait(on_wait) -> None:
    if on_wait is not None:
        on_wait()


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
