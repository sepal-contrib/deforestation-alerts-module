"""On-demand export of the alerts gdf to a user-chosen format.

The canonical store is GeoParquet (multi-geometry). Exports are generated from
the in-memory gdf into the recipe folder, named with source + date range via the
shared ``naming`` helper. Single-geometry formats (GPKG / GeoJSON) export only
the polygon ``geometry``; CSV keeps all columns with geometries as WKT.
"""

import os

import geopandas as gpd

from deforestation_alerts.scripts.naming import alert_artifact_name
from deforestation_alerts.scripts.recipe_helper import save_gdf_to_csv, ALERT_GEOM_COLS

# Map export format -> file extension. CSV is handled specially (WKT columns).
EXPORT_FORMATS = {"csv": "csv", "gpkg": "gpkg", "geojson": "geojson"}

# Secondary geometry columns dropped when exporting to single-geometry formats.
_SECONDARY_GEOM_COLS = ["bounding_box", "point", "alert_polygon"]


def export_alerts_gdf(gdf, folder, fmt, sources, start_date, end_date):
    """Write ``gdf`` to ``folder`` in ``fmt`` and return the output path.

    ``fmt`` is one of ``EXPORT_FORMATS``. Filename is
    ``alerts_<sources>_<start>_<end>.<ext>``.
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}")
    ext = EXPORT_FORMATS[fmt]
    name = alert_artifact_name("alerts", sources, start_date, end_date, ext)
    path = os.path.join(folder, name)

    if fmt == "csv":
        save_gdf_to_csv(gdf, path, ALERT_GEOM_COLS)
        return path

    # GPKG / GeoJSON: single geometry only -> export the polygon, drop the rest.
    out = gpd.GeoDataFrame(gdf).copy()
    if "geometry" in out.columns:
        out = out.set_geometry("geometry")
    drop = [c for c in _SECONDARY_GEOM_COLS if c in out.columns]
    out = out.drop(columns=drop)
    out = out.set_crs(epsg=4326, allow_override=True)
    driver = "GPKG" if fmt == "gpkg" else "GeoJSON"
    out.to_file(path, driver=driver)
    return path
