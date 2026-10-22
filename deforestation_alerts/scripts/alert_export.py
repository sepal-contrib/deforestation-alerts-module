"""On-demand export of the alerts gdf to a user-chosen format.

The canonical store is GeoParquet (multi-geometry). Exports are generated from
the in-memory gdf, named with source + date range via the shared ``naming``
helper. A scope picks the rows by review status and is named in the file (no
suffix for all the alerts). The GeoPackage keeps only the polygon
``geometry``; CSV keeps all columns with geometries as WKT.

Exports are written into the set folder: a single-file format is built in a
private scratch directory, where GDAL may leave sidecars, and copied into place.
"""

from pathlib import Path

import geopandas as gpd

from deforestation_alerts.project.growth import links_of
from deforestation_alerts.project.review_status import CONFIRMED, reviewed_mask
from deforestation_alerts.project.store_schema import GROWS_FROM, SECONDARY_GEOM_COLUMNS
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_store import ALERT_GEOM_COLS, save_gdf_to_csv
from deforestation_alerts.scripts.naming import alert_artifact_name

EXPORT_FORMATS = {"gpkg": "gpkg", "csv": "csv"}
#: Which alerts an export holds. "reviewed" is any verdict (``reviewed_mask``),
#: the count the Overview's KPI strip uses; "all" adds no suffix to the name.
SCOPES = ("confirmed", "reviewed", "unreviewed", "all")
_SECONDARY_GEOM_COLS = list(SECONDARY_GEOM_COLUMNS)


def scope_rows(gdf, scope):
    """The rows of ``gdf`` the ``scope`` exports."""
    if scope not in SCOPES:
        raise ValueError(f"Unknown export scope: {scope!r}")
    if scope == "all":
        return gdf
    if scope == "confirmed":
        # without a status column nothing is confirmed (reviewed_mask is all False)
        mask = gdf["status"].eq(CONFIRMED) if "status" in gdf.columns else reviewed_mask(gdf)
        return gdf[mask.fillna(False).astype(bool)]
    reviewed = reviewed_mask(gdf)
    return gdf[reviewed if scope == "reviewed" else ~reviewed]


def scope_counts(gdf):
    """``{scope: number of rows}`` for every scope, in ``SCOPES`` order."""
    return {scope: len(scope_rows(gdf, scope)) for scope in SCOPES}


def export_name(sources, start, end, fmt, scope="all"):
    """The file name of the ``fmt`` export of ``scope``."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {fmt!r}")
    return alert_artifact_name("alerts", sources, start, end, EXPORT_FORMATS[fmt],
                               suffix="" if scope == "all" else scope)


def export_alerts_gdf(gdf, folder, fmt, sources, start_date, end_date, scope="all"):
    """Write the ``scope`` rows of ``gdf`` into ``folder`` as ``export_name(...)``; return its path."""
    name = export_name(sources, start_date, end_date, fmt, scope)
    rows = scope_rows(gdf, scope)
    if GROWS_FROM in rows.columns:  # a list-valued cell (a store's own) is written as the links joined by ";"
        rows = rows.assign(**{GROWS_FROM: [";".join(links_of(c)) for c in rows[GROWS_FROM]]})
    dest = Path(folder) / name
    with local_files.scratch_dir() as scratch:
        local = scratch / name
        if fmt == "csv":
            save_gdf_to_csv(rows, str(local), ALERT_GEOM_COLS)
        else:
            out = gpd.GeoDataFrame(rows).copy()
            if "geometry" in out.columns:
                out = out.set_geometry("geometry")
            out = out.drop(columns=[c for c in _SECONDARY_GEOM_COLS if c in out.columns])
            out = out.set_crs(epsg=4326, allow_override=True)
            out.to_file(str(local), driver="GPKG")
        local_files.copy_into(local, dest)
    return str(dest)
