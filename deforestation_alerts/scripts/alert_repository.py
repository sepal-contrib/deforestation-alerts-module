"""Pure gdf merge for the alert review state. Extracted from analysis_tile's
create_gdf_full; returns NEW GeoDataFrames (no shared-state mutation), which
removes the cross-thread race.

Service layer (spec §4.2): no solara, no GEE."""

import geopandas as gpd
import pandas as pd


def merge_reviewed_into_full(current_gdf, full_gdf, actual_alert_id):
    """Combine reviewed/active rows from `current_gdf` with a freshly downloaded
    `full_gdf`. Pure port of create_gdf_full's merge branch (analysis_tile
    868-896): keep rows from current that are reviewed OR at actual_alert_id,
    then append fresh rows whose `bounding_box` isn't already represented.
    Returns a new, index-reset GeoDataFrame; never mutates the inputs."""
    if current_gdf is None:
        return full_gdf.reset_index(drop=True).copy()

    mask = (current_gdf["status"] != "Not reviewed") | (
        current_gdf.index == actual_alert_id
    )
    reviewed = current_gdf[mask]
    unique_values = reviewed["bounding_box"].unique()
    if len(unique_values) == 0:
        filtered_total = full_gdf
    else:
        filtered_total = full_gdf[
            ~full_gdf["bounding_box"].isin(unique_values)
        ].reset_index(drop=True)
    combined = gpd.GeoDataFrame(
        pd.concat([reviewed, filtered_total]), crs=current_gdf.crs
    ).reset_index(drop=True)
    return combined
