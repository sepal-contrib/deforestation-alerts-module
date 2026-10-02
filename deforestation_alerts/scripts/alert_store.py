"""The alerts store: an alert set's ``alert_db.parquet``, and the CSV export.

The parquet store is written through ``local_files.write_into`` (a unique
``.part`` renamed into place). ``save_gdf_to_csv`` stays a pure local
serializer on purpose: its caller (the export path in ``alert_export.py``)
decides where the file goes.
"""

import geopandas as gpd
import pandas as pd

# Geometry columns the alerts gdf may carry. Used to coerce secondary geometry
# columns to GeoSeries before a GeoParquet write and to encode the CSV export.
ALERT_GEOM_COLS = ["geometry", "bounding_box", "point", "alert_polygon"]


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
    the one its alerts came from; if it raises, nothing is written. Returns
    ``str(path)``.
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
    with local_files.write_into(path) as part:
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
