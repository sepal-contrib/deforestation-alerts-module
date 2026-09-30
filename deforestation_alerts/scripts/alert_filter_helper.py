import ee
import pandas as pd
import geopandas as gpd
from deforestation_alerts.message import cm
from pysepal.scripts import utils as su
from deforestation_alerts.scripts.report_builder import get_unique_alerts, format_list


def check_integer(text, exception_text):
    try:
        if isinstance(text, list):
            text = text[0]
        # If the input is 'All', return float(0)
        if text == cm.filter_tile.max_number_of_alerts_option1:
            return float(0)
        # Try to convert the text to a float
        return float(text)
    except ValueError:
        # Raise an exception if the text cannot be converted to a float
        raise Exception(exception_text)


def check_alert_filter_inputs(self):
    """Check inputs and raise error if inputs are not set or set incorrectly."""
    su.check_input(self.alert_source_select.v_model, "Select at least one alert source")
    check_integer(self.min_area_input.v_model, "Min area has to be a number")
    su.check_input(
        self.alert_selection_method_select.v_model,
        " Alert area selection method cannot be empty",
    )
    su.check_input(
        self.alert_sorting_select.v_model, "Alert sorting method cannot be empty"
    )
    if (
        self.alert_selection_method_select.v_model
        == cm.filter_tile.area_selection_method_label1
    ):
        su.check_input(self.drawn_item.to_json(), "No drawn polygons")
    su.check_input(self.number_of_alerts.v_model, "Number of alerts cannot be empty")
    max_n = check_integer(
        self.number_of_alerts.v_model, "Number of alerts has to be a number"
    )

    return (
        self.alert_source_select.v_model,
        self.min_area_input.v_model,
        self.alert_selection_method_select.v_model,
        self.alert_sorting_select.v_model,
        max_n,
        self.drawn_item.to_json(),
    )


def custom_reduce_image_collection(image_collection):
    """
    Reduces an ee.ImageCollection with custom reducers for specific bands.

    Args:
        image_collection (ee.ImageCollection): The input ImageCollection containing 'alerts' and 'date' bands.

    Returns:
        ee.Image: A single reduced image with the max of 'alerts' and min of 'date'.
    """
    # Define reducers for the bands
    sum_alerts_reducer = ee.Reducer.sum()
    min_date_reducer = ee.Reducer.min()

    # Apply the reducers to the ImageCollection
    reduced_alerts = image_collection.select("alert").reduce(sum_alerts_reducer)
    reduced_date = image_collection.select("date").reduce(min_date_reducer)

    # Combine the reduced results into a single image
    combined_image = reduced_alerts.rename("alert").addBands(
        reduced_date.rename("date")
    )

    return combined_image


def convert_to_geopandas(vectorized_gdf, custom_names=None):
    """Assemble alerts_gdf from the local-vectorize output.

    Input: gpd.GeoDataFrame with columns geometry(Polygon), alert_type_unique(list),
    count, alert_date_min, alert_date_max, area_ha (from vectorize_alert_raster).
    Output: the alerts_gdf the app consumes — active geometry = polygon (D2),
    plus the back-compat `bounding_box`, `point`, decoded `alert_sources`, and the
    empty user-review columns. Preserves the integer alert-code decoding.

    ``custom_names`` (``{slot: name}``) labels the user-defined custom alert
    sources in ``alert_sources``; unnamed slots decode as "Custom <n>".
    """
    gdf = vectorized_gdf.copy()

    # geometry stays the true polygon; derive back-compat shapes
    gdf["bounding_box"] = gdf.geometry.envelope          # D2 back-compat bbox
    gdf["point"] = gdf.geometry.centroid

    gdf["status"] = "Not reviewed"
    gdf["alert_sources"] = gdf["alert_type_unique"].apply(
        lambda x: format_list(get_unique_alerts(x, custom_names=custom_names))
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
