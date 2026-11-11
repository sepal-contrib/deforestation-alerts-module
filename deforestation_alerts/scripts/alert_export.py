"""On-demand export of the alerts gdf to a user-chosen format.

The canonical store is GeoParquet (multi-geometry). Exports are generated from
the in-memory gdf, named with source + date range via the shared ``naming``
helper. A scope picks the rows by review status and is named in the file (no
suffix for all the alerts). The exported ``geometry`` is the one the reviewer
approved (``reviewed_geometry``), named by ``geometry_source``; the cluster
Integrate detected stays beside it: a ``<stem>_detected`` layer in the
GeoPackage, a ``detected_geometry`` WKT column in the CSV.

Exports are written into the set folder: a single-file format is built in a
private scratch directory, where GDAL may leave sidecars, and copied into place.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.project import review_groups
from deforestation_alerts.project.growth import links_of
from deforestation_alerts.project.review_status import CONFIRMED, reviewed_mask
from deforestation_alerts.project.store_schema import (
    DETECTED_AREA,
    DETECTED_GEOMETRY,
    GEOMETRY_SOURCE,
    GROWS_FROM,
    SECONDARY_GEOM_COLUMNS,
)
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


#: what ``geometry_source`` says the exported geometry is
DRAWN, DETECTED, GROUP = "drawn", "detected", "group"


def _drawn(value) -> bool:
    return isinstance(value, BaseGeometry) and not value.is_empty


def reviewed_geometry(gdf) -> tuple:
    """``(geometry, geometry_source)`` per row of the whole store (index of
    ``gdf``): the row's own drawing (``drawn``); none for a member of a group
    whose main has a drawing, which covers it (``group``); else its cluster
    (``detected``). Never a box."""
    drawings = gdf["alert_polygon"] if "alert_polygon" in gdf.columns else pd.Series([None] * len(gdf))
    has = np.array([_drawn(g) for g in drawings], dtype=bool)
    values = review_groups.group_values(gdf)
    by_id = {int(i): p for p, i in enumerate(review_groups.ids(gdf))}
    grouped = review_groups.grouped_mask(gdf)
    geoms, sources = list(gdf.geometry), [DETECTED] * len(gdf)
    for p in range(len(gdf)):
        main = by_id.get(int(values.iloc[p])) if grouped[p] else None
        if has[p]:
            geoms[p], sources[p] = drawings.iloc[p], DRAWN
        elif main is not None and has[main]:
            # Spec §2: kept even when the main falls outside the export's scope, so
            # such a member exports with no shape. A group Save gives every member
            # the main's verdict, so the scopes do not split a group in practice.
            geoms[p], sources[p] = None, GROUP
    return (gpd.GeoSeries(geoms, index=gdf.index, crs=gdf.crs or "EPSG:4326"),
            pd.Series(sources, index=gdf.index))


def detected_area(gdf) -> pd.Series:
    """Each row's ``detected_area_ha`` (index of ``gdf``); a store from before
    the column, or a row without it, takes what ``review_groups.own_area``
    would: its stored ``area_ha`` when positive and nothing is drawn, else its
    cluster measured -- all such rows at once, in one UTM zone for the store."""
    def column(name):
        return (pd.to_numeric(gdf[name], errors="coerce").to_numpy(dtype=float, copy=True) if name in gdf.columns
                else np.full(len(gdf), np.nan))

    values = column(DETECTED_AREA)
    missing = np.isnan(values)
    if missing.any():
        stored = column("area_ha")
        drawings = gdf["alert_polygon"] if "alert_polygon" in gdf.columns else [None] * len(gdf)
        drawn = np.array([_drawn(g) for g in drawings], dtype=bool)
        keep = missing & ~drawn & (stored > 0)  # NaN > 0 is False
        values[keep] = stored[keep]
        measure = missing & ~keep
        if measure.any():
            geoms = gpd.GeoSeries(gdf.geometry.to_numpy()[measure], crs=gdf.crs or "EPSG:4326")
            values[measure] = geoms.to_crs(geoms.estimate_utm_crs()).area.to_numpy() / 10_000.0
    return pd.Series(values, index=gdf.index)


def export_alerts_gdf(gdf, folder, fmt, sources, start_date, end_date, scope="all"):
    """Write the ``scope`` rows of ``gdf`` into ``folder`` as ``export_name(...)``; return its path."""
    name = export_name(sources, start_date, end_date, fmt, scope)
    # over the whole store: a member's main may be out of the scope
    geometry, source = reviewed_geometry(gdf)
    area = detected_area(gdf)
    rows = scope_rows(gdf, scope)
    if GROWS_FROM in rows.columns:  # a list-valued cell (a store's own) is written as the links joined by ";"
        rows = rows.assign(**{GROWS_FROM: [";".join(links_of(c)) for c in rows[GROWS_FROM]]})
    detected = rows.geometry
    rows = gpd.GeoDataFrame(rows).copy()
    rows[DETECTED_AREA] = area.loc[rows.index]
    rows[GEOMETRY_SOURCE] = source.loc[rows.index]
    rows["geometry"] = geometry.loc[rows.index]
    dest = Path(folder) / name
    with local_files.scratch_dir() as scratch:
        local = scratch / name
        if fmt == "csv":
            out = pd.DataFrame(rows)
            out.insert(out.columns.get_loc("geometry") + 1, DETECTED_GEOMETRY, detected)
            save_gdf_to_csv(out, str(local), [*ALERT_GEOM_COLS, DETECTED_GEOMETRY])
        else:
            out = rows.set_geometry("geometry")
            out = out.drop(columns=[c for c in _SECONDARY_GEOM_COLS if c in out.columns])
            out = out.set_crs(epsg=4326, allow_override=True)
            stem = Path(name).stem
            out.to_file(str(local), driver="GPKG", layer=stem)
            keep = [c for c in ("alert_id", "set_id", DETECTED_AREA) if c in rows.columns]
            clusters = gpd.GeoDataFrame(rows[keep], geometry=detected.to_numpy(), crs="EPSG:4326")
            clusters.to_file(str(local), driver="GPKG", layer=f"{stem}_detected", mode="a")
        local_files.copy_into(local, dest)
    return str(dest)
