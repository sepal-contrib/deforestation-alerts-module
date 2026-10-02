"""On-demand export of the alerts gdf to a user-chosen format.

The canonical store is GeoParquet (multi-geometry). Exports are generated from
the in-memory gdf, named with source + date range via the shared ``naming``
helper. Single-geometry formats (GPKG / GeoJSON) export only the polygon
``geometry``; CSV keeps all columns with geometries as WKT.

Exports are written into the set folder: a single-file format is built in a
private scratch directory, where GDAL may leave sidecars, and copied into place.
"""

from pathlib import Path

import geopandas as gpd

from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_store import ALERT_GEOM_COLS, save_gdf_to_csv
from deforestation_alerts.scripts.naming import alert_artifact_name

EXPORT_FORMATS = {"csv": "csv", "gpkg": "gpkg", "geojson": "geojson"}
_SECONDARY_GEOM_COLS = ["bounding_box", "point", "alert_polygon"]


def export_alerts_gdf(gdf, folder, fmt, sources, start_date, end_date):
    """Write ``gdf`` into ``folder`` as ``alerts_<sources>_<start>_<end>.<ext>``; return its path."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}")
    name = alert_artifact_name("alerts", sources, start_date, end_date, EXPORT_FORMATS[fmt])
    dest = Path(folder) / name
    with local_files.scratch_dir() as scratch:
        local = scratch / name
        if fmt == "csv":
            save_gdf_to_csv(gdf, str(local), ALERT_GEOM_COLS)
        else:
            out = gpd.GeoDataFrame(gdf).copy()
            if "geometry" in out.columns:
                out = out.set_geometry("geometry")
            out = out.drop(columns=[c for c in _SECONDARY_GEOM_COLS if c in out.columns])
            out = out.set_crs(epsg=4326, allow_override=True)
            out.to_file(str(local), driver="GPKG" if fmt == "gpkg" else "GeoJSON")
        local_files.copy_into(local, dest)
    return str(dest)
