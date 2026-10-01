"""On-demand export of the alerts gdf to a user-chosen format.

The canonical store is GeoParquet (multi-geometry). Exports are generated from
the in-memory gdf into the recipe folder, named with source + date range via the
shared ``naming`` helper. Single-geometry formats (GPKG / GeoJSON) export only
the polygon ``geometry``; CSV keeps all columns with geometries as WKT.

The recipe folder is a **remote** path in the user's SEPAL workspace, so every
export is written to a container-local scratch file first (GeoPandas/GDAL only
write local paths) and then uploaded through the ``Workspace`` adapter --
inventory rows a10 / a14-a16.
"""

from pathlib import PurePosixPath

import geopandas as gpd

from deforestation_alerts.scripts.alert_store import ALERT_GEOM_COLS, save_gdf_to_csv
from deforestation_alerts.scripts.naming import alert_artifact_name

# Map export format -> file extension. CSV is handled specially (WKT columns).
EXPORT_FORMATS = {"csv": "csv", "gpkg": "gpkg", "geojson": "geojson"}

# Secondary geometry columns dropped when exporting to single-geometry formats.
_SECONDARY_GEOM_COLS = ["bounding_box", "point", "alert_polygon"]


def export_alerts_gdf(gdf, workspace, folder, fmt, sources, start_date, end_date):
    """Write ``gdf`` to ``folder`` in ``fmt`` and return the REMOTE output path.

    ``fmt`` is one of ``EXPORT_FORMATS``. ``folder`` is a remote workspace path;
    the filename is ``alerts_<sources>_<start>_<end>.<ext>``.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}")
    ext = EXPORT_FORMATS[fmt]
    name = alert_artifact_name("alerts", sources, start_date, end_date, ext)
    remote_path = str(PurePosixPath(folder) / name)

    # Keep the real filename: GDAL dispatches its driver on the extension, and a
    # GPKG write may drop sidecars next to it -- `scratch_file` gives the file
    # its own private directory, so those cannot collide with another export.
    local_path = workspace.scratch_file(name=name)
    try:
        if fmt == "csv":
            save_gdf_to_csv(gdf, str(local_path), ALERT_GEOM_COLS)
        else:
            # GPKG / GeoJSON: single geometry only -> export the polygon, drop the rest.
            out = gpd.GeoDataFrame(gdf).copy()
            if "geometry" in out.columns:
                out = out.set_geometry("geometry")
            drop = [c for c in _SECONDARY_GEOM_COLS if c in out.columns]
            out = out.drop(columns=drop)
            out = out.set_crs(epsg=4326, allow_override=True)
            driver = "GPKG" if fmt == "gpkg" else "GeoJSON"
            out.to_file(str(local_path), driver=driver)
        return workspace.upload_from_scratch(local_path, remote_path)
    finally:
        workspace.discard_scratch(local_path)
