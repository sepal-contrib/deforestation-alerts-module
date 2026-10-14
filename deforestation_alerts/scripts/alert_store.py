"""The alerts store: an alert set's ``alert_db.parquet``, and the CSV export.

The parquet store is written through ``local_files.write_into`` (a unique
``.part`` renamed into place). ``save_gdf_to_csv`` stays a pure local
serializer on purpose: its caller (the export path in ``alert_export.py``)
decides where the file goes.

Several writers replace one store: a review Save, an evaluator split and a
merge (spec §4.4). Each checks in its ``guard`` that the store is still the
generation it read, and the check and the replace are atomic with respect to
each other because every write holds the store's lock (``store_lock``) from
before the serialization until the file is in place.
"""

import os
import threading
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

from deforestation_alerts.project.store_schema import GEOM_COLUMNS

# Geometry columns the alerts gdf may carry. Used to coerce secondary geometry
# columns to GeoSeries before a GeoParquet write and to encode the CSV export.
ALERT_GEOM_COLS = list(GEOM_COLUMNS)


def store_generation(store) -> Optional[tuple]:
    """Which write of ``store`` is on disk: ``(st_mtime_ns, st_size, st_ino)``,
    or None when the file is missing. Every writer (Integrate's swap, a review
    Save's ``write_into``) replaces the file, so a rewrite changes it."""
    try:
        st = Path(store).stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


#: One lock per store path (``os.path.realpath``), made on first use and kept
#: for the session, as ``scene_cache._lock_for`` does per project folder.
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def store_lock(path) -> threading.RLock:
    """The lock every write of the store at ``path`` holds (spec §4.4).

    Re-entrant: a writer that reads the store under it (a split, a merge)
    then writes through ``save_gdf_to_parquet``, which takes it again. A
    same-process lock: it serializes this app's threads, not two processes.
    """
    key = os.path.realpath(os.fspath(path))
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = _STORE_LOCKS[key] = threading.RLock()
        return lock


def save_gdf_to_csv(gdf, path, geometry_cols):
    """Write a gdf to a LOCAL CSV, encoding each named geometry column as WKT.

    Its one caller is the export path (``alert_export.py``), which writes it
    in a private scratch folder and copies it into the set folder.
    """
    out = pd.DataFrame(gdf).copy()
    for col in geometry_cols:
        if col in out.columns:
            # Treat NaN (an unset geometry column reindexed to floats) like None,
            # so it encodes to "" rather than blowing up on ``g.wkt``.
            out[col] = out[col].apply(
                lambda g: g.wkt
                if g is not None and not (isinstance(g, float) and pd.isna(g))
                else ""
            )
    out.to_csv(path, index=False)
    return path


def save_gdf_to_parquet(gdf, path, *, guard=None):
    """Write the alerts gdf to GeoParquet at ``path`` (all geometry columns kept).

    The known secondary geometry columns (``bounding_box``, ``point``,
    ``alert_polygon``) are coerced to GeoSeries so they are written as
    geometry; the active geometry stays ``geometry``. ``guard``, when given,
    runs once the file is serialized and right before it replaces ``path``:
    a review Save checks there that the store it is about to replace is still
    the one its alerts came from; if it raises, nothing is written. The
    store's lock (``store_lock``) is held from before the serialization until
    the file is in place, so no other writer's guard runs between this
    guard and this replace. Returns ``str(path)``.
    """
    from deforestation_alerts.scripts import local_files

    out = gpd.GeoDataFrame(gdf).copy()
    for col in ALERT_GEOM_COLS:
        if col in out.columns and not isinstance(out[col], gpd.GeoSeries):
            vals = [
                g if g is not None and not (isinstance(g, float) and pd.isna(g)) else None
                for g in out[col].tolist()
            ]
            out[col] = gpd.GeoSeries(vals, index=out.index, crs="EPSG:4326")
    if "geometry" in out.columns:
        out = out.set_geometry("geometry")
    with store_lock(path), local_files.write_into(path) as part:
        out.to_parquet(part)
        if guard is not None:
            guard()
    return str(path)


def load_gdf_from_parquet(path):
    """Read a GeoParquet alerts store; ``geometry`` is the active geometry."""
    gdf = gpd.read_parquet(path)
    if "geometry" in gdf.columns:
        gdf = gdf.set_geometry("geometry")
    return gdf
