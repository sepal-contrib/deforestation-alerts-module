import time
import json
import ee
import pandas as pd
import geopandas as gpd
from datetime import date, datetime
from math import floor, ceil
from component.message import cm
from shapely.geometry import Point, Polygon
from sepal_ui.scripts import utils as su


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


def evaluate_with_retry(ee_object, max_retries=5, delay=3):
    """
    Evaluates a Google Earth Engine object and retries if a computation times out.

    Args:
        ee_object: The Earth Engine object to evaluate (e.g., a FeatureCollection or Image).
        max_retries: Maximum number of retries if a computation times out.
        delay: Delay (in seconds) between retries.

    Returns:
        The result of the computation as a JSON object.

    Raises:
        Exception: If all retries fail, the exception from the last attempt is raised.
    """
    attempt = 0
    while attempt < max_retries:
        try:
            # Evaluate the Earth Engine object
            return ee_object.getInfo()
        except ee.ee_exception.EEException as e:
            # Check if the exception is a timeout
            if "Computation timed out" in str(e):
                attempt += 1
                print(
                    f"Attempt {attempt} failed due to timeout. Retrying in {delay} seconds..."
                )
                time.sleep(delay)
            else:
                # Re-raise the exception if it's not a timeout
                raise
    raise Exception(f"All {max_retries} attempts failed due to timeout.")


def custom_reduce_image_collection(image_collection):
    """
    Reduces an ee.ImageCollection with custom reducers for specific bands.

    Args:
        image_collection (ee.ImageCollection): The input ImageCollection containing 'alerts' and 'date' bands.

    Returns:
        ee.Image: A single reduced image with the max of 'alerts' and min of 'date'.
    """
    # Define reducers for the bands
    max_alerts_reducer = ee.Reducer.max()
    min_date_reducer = ee.Reducer.min()

    # Apply the reducers to the ImageCollection
    reduced_alerts = image_collection.select("alert").reduce(max_alerts_reducer)
    reduced_date = image_collection.select("date").reduce(min_date_reducer)

    # Combine the reduced results into a single image
    combined_image = reduced_alerts.rename("alert").addBands(
        reduced_date.rename("date")
    )

    return combined_image


def obtener_datos_gee_parcial_map(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
):
    def get_bb(feature):
        grid_geometry = ee.Feature(feature).geometry()
        bounding_boxes = alert_raster.clip(grid_geometry).reduceToVectors(
            ee_reducer,
            grid_geometry,
            pixel_size,
            "bb",
            True,
            None,
            None,
            None,
            True,
            1e13,
            1,
        )
        # Filter clusters with at least `min_alert_size_pixels`
        bb_cleaned = bounding_boxes.filter(
            ee.Filter.gte("count", min_alert_size_pixels)
        )
        return bb_cleaned

    def procesar_elemento(elemento):
        bb = get_bb(elemento)
        conteo = bb.size()  # Keep this as an EE object

        # Only fetch info if we have elements
        return ee.Algorithms.If(conteo.gte(1), bb, ee.FeatureCollection([]))

    # Use `map()` to batch-process elements in `aoi_grid`, limiting to `max_elementos`
    if max_elementos == 0 or max_elementos > 30:
        max_elementos = 30
    else:
        max_elementos = max_elementos

    aoi_grid = aoi.geometry().coveringGrid("EPSG:4326", 50000)

    elementos_filtrados = aoi_grid.limit(20).map(procesar_elemento)

    # Convert the resulting list of lists to a flat list
    resultados = ee.FeatureCollection(elementos_filtrados).flatten()

    # Sort and convert to list
    if sorting == cm.filter_tile.alert_sorting_method_label1:
        bb_sorted = resultados.sort("count", False)
    if sorting == cm.filter_tile.alert_sorting_method_label2:
        bb_sorted = resultados.sort("count", True)
    if sorting == cm.filter_tile.alert_sorting_method_label3:
        bb_sorted = resultados.sort("alert_date_max", False)
    if sorting == cm.filter_tile.alert_sorting_method_label4:
        bb_sorted = resultados.sort("alert_date_max", True)

    resultados2 = bb_sorted.toList(max_elementos)

    return evaluate_with_retry(resultados2)


def obtener_datos_gee_total_v2(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
):
    # Apply reducer and filter to minimum alert size
    bounding_boxes = alert_raster.reduceToVectors(
        ee_reducer, aoi, pixel_size, "bb", True, None, None, None, True, 1e13, 1
    ).filter(ee.Filter.gte("count", min_alert_size_pixels))

    # Calculate the export limit on GEE side
    max_elements = ee.Number(5000) if max_elementos <= 0 else ee.Number(max_elementos)
    num_elements = bounding_boxes.size().min(max_elements)

    # Sort and convert to list
    if sorting == cm.filter_tile.alert_sorting_method_label1:
        bb_sorted = bounding_boxes.sort("count", False)
    if sorting == cm.filter_tile.alert_sorting_method_label2:
        bb_sorted = bounding_boxes.sort("count", True)
    if sorting == cm.filter_tile.alert_sorting_method_label3:
        bb_sorted = bounding_boxes.sort("alert_date_max", False)
    if sorting == cm.filter_tile.alert_sorting_method_label4:
        bb_sorted = bounding_boxes.sort("alert_date_max", True)

    sorted_bb_list = bb_sorted.toList(num_elements)

    return evaluate_with_retry(sorted_bb_list)


# Function to convert list of polygons to geodataframe
def convert_to_geopandas(polygon_features):
    # Convert polygon features into GeoDataFrame and calculate centroids
    combined_features = []

    for feature in polygon_features:
        polygon = Polygon(feature["geometry"]["coordinates"][0])

        # Extract properties and add relevant information
        properties = feature["properties"]
        properties["gee_id"] = feature["id"]
        properties["bounding_box"] = polygon
        properties["point"] = polygon.centroid  # Add centroid as 'point'

        combined_features.append(properties)

    # Create GeoDataFrame
    gdf = gpd.GeoDataFrame(combined_features, geometry="point")

    # Add additional columns with default values
    gdf["status"] = "Not reviewed"
    gdf["alert_sources"] = pd.Series(dtype="object")
    gdf["before_img"] = pd.Series(dtype="object")
    gdf["before_img_info"] = pd.Series(dtype="object")
    gdf["after_img"] = pd.Series(dtype="object")
    gdf["after_img_info"] = pd.Series(dtype="object")
    gdf["alert_polygon"] = pd.Series(dtype="object")
    gdf["area_ha"] = pd.Series(dtype="float")
    gdf["description"] = pd.Series(dtype="object")
    gdf["admin1"] = pd.Series(dtype="object")
    gdf["admin2"] = pd.Series(dtype="object")
    gdf["admin3"] = pd.Series(dtype="object")

    # Save gdf to file
    # gdf.set_crs(epsg = '4326', allow_override=True, inplace=True).to_file(gpkg_name, driver='GPKG')

    # Check result
    return gdf
