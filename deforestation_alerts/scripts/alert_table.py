"""The alerts table the app reviews, assembled from the vectorizer's output."""

import pandas as pd

from deforestation_alerts.project.review_status import NOT_REVIEWED

from deforestation_alerts.scripts.alert_codes import format_list, get_unique_alerts


def convert_to_geopandas(vectorized_gdf, custom_names=None, digit_map=None):
    """Assemble alerts_gdf from the local-vectorize output.

    Input: gpd.GeoDataFrame with columns geometry(Polygon), alert_type_unique(list),
    count, alert_date_min, alert_date_max, area_ha (from vectorize_alert_raster).
    Output: the alerts_gdf the app consumes — active geometry = polygon (D2),
    plus the back-compat `bounding_box`, `point`, decoded `alert_sources`, and the
    empty user-review columns. Preserves the integer alert-code decoding.

    ``custom_names`` (``{slot: name}``) labels the user-defined custom alert
    sources in ``alert_sources``; unnamed slots decode as "Custom <n>".
    ``digit_map`` (``{digit: name}``, an alert set's own) replaces both when
    given: see ``alert_codes.get_unique_alerts``.
    """
    gdf = vectorized_gdf.copy()

    # geometry stays the true polygon; derive back-compat shapes
    gdf["bounding_box"] = gdf.geometry.envelope          # D2 back-compat bbox
    gdf["point"] = gdf.geometry.centroid

    gdf["status"] = NOT_REVIEWED
    gdf["alert_sources"] = gdf["alert_type_unique"].apply(
        lambda x: format_list(get_unique_alerts(x, custom_names=custom_names, digit_map=digit_map))
    )
    gdf["before_img"] = pd.Series([], dtype="str")
    gdf["before_img_info"] = pd.Series([], dtype="object")
    gdf["after_img"] = pd.Series([], dtype="str")
    gdf["after_img_info"] = pd.Series([], dtype="object")
    gdf["alert_polygon"] = pd.Series([], dtype="object")
    if "area_ha" not in gdf.columns:
        gdf["area_ha"] = pd.Series([], dtype="float")
    gdf["description"] = pd.Series([], dtype="str")
    gdf["admin1"] = pd.Series([], dtype="str")
    gdf["admin2"] = pd.Series([], dtype="str")
    gdf["admin3"] = pd.Series([], dtype="str")

    return gdf
