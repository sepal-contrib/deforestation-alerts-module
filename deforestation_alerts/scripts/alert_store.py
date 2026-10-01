"""The alerts store: an alert set's ``alert_db.parquet``, and the CSV export.

The parquet readers and writers take the ``Workspace`` adapter and stage
through ``Workspace.scratch_file`` / ``download_to_scratch`` /
``upload_from_scratch``, because pyarrow and GeoPandas only open local files.
``save_gdf_to_csv`` stays a pure local serializer on purpose: its caller (the
export path in ``alert_export.py``) owns the staging.
"""

import geopandas as gpd
import pandas as pd

# Geometry columns the alerts gdf may carry. Used to coerce secondary geometry
# columns to GeoSeries before a GeoParquet write and to encode the CSV export.
ALERT_GEOM_COLS = ["geometry", "bounding_box", "point", "alert_polygon"]


def save_gdf_to_csv(gdf, path, geometry_cols):
    """Write a gdf to a LOCAL CSV, encoding each named geometry column as WKT.

    Local-only, because pandas writes local files. Its one caller is the export
    path (``alert_export.py``), which owns staging the result into the user's
    workspace.
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


def save_gdf_to_parquet(gdf, workspace, remote_path):
    """Write the alerts gdf to GeoParquet in the user's SEPAL workspace.

    GeoParquet supports multiple geometry columns; coerce the known secondary
    geometry columns (`bounding_box`, `point`, `alert_polygon`) to GeoSeries so
    they are written as geometry (not opaque objects). The active geometry stays
    `geometry`.

    Args:
        gdf: the alerts GeoDataFrame.
        workspace (Workspace): the session's user-file adapter.
        remote_path (str): REMOTE path of the store, e.g.
            ``workspace.results_dir(recipe, "alert_db.parquet")``.

    Returns:
        str: the remote path written.
    """
    out = gpd.GeoDataFrame(gdf).copy()
    for col in ALERT_GEOM_COLS:
        if col in out.columns and not isinstance(out[col], gpd.GeoSeries):
            # An unset geometry column (e.g. `alert_polygon` before the user draws
            # one) reindexes to NaN floats on a multi-row frame; map those to None
            # so GeoSeries accepts them as missing geometry instead of raising on
            # the float dtype.
            vals = [
                g if g is not None and not (isinstance(g, float) and pd.isna(g)) else None
                for g in out[col].tolist()
            ]
            out[col] = gpd.GeoSeries(vals, index=out.index, crs="EPSG:4326")
    if "geometry" in out.columns:
        out = out.set_geometry("geometry")
    # pyarrow writes a local file; stage it, then ship it to the user's
    # workspace and hand back the REMOTE path that is the store. The staged copy
    # is dropped immediately: this runs once per alert save, so keeping it would
    # pile up a full copy of the store in container tmp per save.
    local = workspace.scratch_file(".parquet")
    try:
        out.to_parquet(local)
        return workspace.upload_from_scratch(local, remote_path)
    finally:
        workspace.discard_scratch(local)


def load_gdf_from_parquet(workspace, remote_path):
    """Read a GeoParquet alerts store out of the user's SEPAL workspace.

    GeoPandas/pyarrow only open local files, so the store is staged first, then
    the staged copy is discarded (the gdf is fully in memory by then);
    `geometry` is made the active geometry on the way back.
    """
    local = workspace.download_to_scratch(remote_path)
    try:
        gdf = gpd.read_parquet(local)
    finally:
        workspace.discard_scratch(local)
    if "geometry" in gdf.columns:
        gdf = gdf.set_geometry("geometry")
    return gdf
