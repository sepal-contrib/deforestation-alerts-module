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


def obtener_datos_gee_parcial_map(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
    grid_size,
    max_retries=3,
):
    def get_bb(feature):
        grid_geometry = ee.Feature(feature).geometry()
        bounding_boxes = alert_raster.clip(grid_geometry).reduceToVectors(
            reducer=ee_reducer,
            geometry=grid_geometry,
            scale=pixel_size,
            geometryType="bb",
            eightConnected=True,
            maxPixels=1e13,
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

    # Function to apply distinct to the 'list' property of each feature
    def apply_distinct(fc, property_name, new_property_name):
        """
        Apply distinct to a list property in each feature of a FeatureCollection.

        Args:
            fc (ee.FeatureCollection): The input FeatureCollection.
            property_name (str): The name of the property containing the list.
            new_property_name (str): The name of the new property to store the distinct list.

        Returns:
            ee.FeatureCollection: A new FeatureCollection with distinct values in the new property.
        """
        fc2 = fc.map(
            lambda feature: feature.set(
                new_property_name, ee.List(feature.get(property_name)).distinct()
            )
        )
        return fc2.map(
            lambda feature: ee.Feature(feature.geometry()).copyProperties(
                source=feature, exclude=[property_name]
            )
        )

    # Use `map()` to batch-process elements in `aoi_grid`, limiting to `max_elementos`
    if max_elementos == 0 or max_elementos > 30:
        max_elementos = 30
    else:
        max_elementos = max_elementos

    aoi_grid = aoi.geometry().coveringGrid("EPSG:4326", grid_size)
    aoi_grid_size = aoi_grid.size().getInfo()
    
    # Initial limit value and increment step
    limit_value = 40
    increment_step = 20

    while True:
        # Ensure limit_value does not exceed total_elements
        if limit_value > aoi_grid_size:
            limit_value = aoi_grid_size
    
        elementos_filtrados = aoi_grid.limit(limit_value).map(procesar_elemento)
        resultados_pre = ee.FeatureCollection(elementos_filtrados).flatten()
    
        # Get the number of elements in the FeatureCollection
        num_elements = resultados_pre.size().getInfo()
        #print(f"Quick run found {num_elements} alerts, with {limit_value}/{aoi_grid_size} cells")

        if num_elements >= max_elementos or limit_value == aoi_grid_size:
            resultados = resultados_pre
            break
            
        # Increase the limit value by the increment step and try again
        limit_value += increment_step

    resultados2 = apply_distinct(resultados, "alert_type_list", "alert_type_unique")
    
    # Sort and convert to list
    if sorting == cm.filter_tile.alert_sorting_method_label1:
        bb_sorted = resultados2.sort("count", False)
    if sorting == cm.filter_tile.alert_sorting_method_label2:
        bb_sorted = resultados2.sort("count", True)
    if sorting == cm.filter_tile.alert_sorting_method_label3:
        bb_sorted = resultados2.sort("alert_date_max", False)
    if sorting == cm.filter_tile.alert_sorting_method_label4:
        bb_sorted = resultados2.sort("alert_date_max", True)

    resultados3 = bb_sorted.toList(max_elementos)

    return evaluate_with_retry(resultados3, max_retries)


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
        reducer=ee_reducer,
        geometry=aoi,
        scale=pixel_size,
        geometryType="bb",
        eightConnected=True,
        maxPixels=1e13,
    ).filter(ee.Filter.gte("count", min_alert_size_pixels))

    # Function to apply distinct to the 'list' property of each feature
    def apply_distinct(fc, property_name, new_property_name):
        """
        Apply distinct to a list property in each feature of a FeatureCollection.

        Args:
            fc (ee.FeatureCollection): The input FeatureCollection.
            property_name (str): The name of the property containing the list.
            new_property_name (str): The name of the new property to store the distinct list.

        Returns:
            ee.FeatureCollection: A new FeatureCollection with distinct values in the new property.
        """
        fc2 = fc.map(
            lambda feature: feature.set(
                new_property_name, ee.List(feature.get(property_name)).distinct()
            )
        )
        return fc2.map(
            lambda feature: ee.Feature(feature.geometry()).copyProperties(
                source=feature, exclude=[property_name]
            )
        )

    bounding_boxes_2 = apply_distinct(
        bounding_boxes, "alert_type_list", "alert_type_unique"
    )

    # Calculate the export limit on GEE side
    max_elements = ee.Number(5000) if max_elementos <= 0 else ee.Number(max_elementos)
    num_elements = bounding_boxes_2.size().min(max_elements)

    # Sort and convert to list
    if sorting == cm.filter_tile.alert_sorting_method_label1:
        bb_sorted = bounding_boxes_2.sort("count", False)
    if sorting == cm.filter_tile.alert_sorting_method_label2:
        bb_sorted = bounding_boxes_2.sort("count", True)
    if sorting == cm.filter_tile.alert_sorting_method_label3:
        bb_sorted = bounding_boxes_2.sort("alert_date_max", False)
    if sorting == cm.filter_tile.alert_sorting_method_label4:
        bb_sorted = bounding_boxes_2.sort("alert_date_max", True)

    sorted_bb_list = bb_sorted.toList(num_elements)

    return sorted_bb_list


def obtener_datos_gee_parcial_map_2(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
    grid_size,
):
    def get_bb(feature):
        grid_geometry = ee.Feature(feature).geometry()
        bounding_boxes = alert_raster.clip(grid_geometry).reduceToVectors(
            reducer=ee_reducer,
            geometry=grid_geometry,
            scale=pixel_size,
            geometryType="bb",
            eightConnected=True,
            maxPixels=1e13,
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

    aoi_grid = aoi.geometry().coveringGrid("EPSG:4326", grid_size)

    elementos_filtrados = aoi_grid.map(procesar_elemento)

    # Convert the resulting list of lists to a flat list
    resultados = ee.FeatureCollection(elementos_filtrados).flatten()

    # Function to apply distinct to the 'list' property of each feature
    def apply_distinct(fc, property_name, new_property_name):
        """
        Apply distinct to a list property in each feature of a FeatureCollection.

        Args:
            fc (ee.FeatureCollection): The input FeatureCollection.
            property_name (str): The name of the property containing the list.
            new_property_name (str): The name of the new property to store the distinct list.

        Returns:
            ee.FeatureCollection: A new FeatureCollection with distinct values in the new property.
        """
        fc2 = fc.map(
            lambda feature: feature.set(
                new_property_name, ee.List(feature.get(property_name)).distinct()
            )
        )
        return fc2.map(
            lambda feature: ee.Feature(feature.geometry()).copyProperties(
                source=feature, exclude=[property_name]
            )
        )

    resultados2 = apply_distinct(resultados, "alert_type_list", "alert_type_unique")

    # Sort and convert to list
    if sorting == cm.filter_tile.alert_sorting_method_label1:
        bb_sorted = resultados2.sort("count", False)
    if sorting == cm.filter_tile.alert_sorting_method_label2:
        bb_sorted = resultados2.sort("count", True)
    if sorting == cm.filter_tile.alert_sorting_method_label3:
        bb_sorted = resultados2.sort("alert_date_max", False)
    if sorting == cm.filter_tile.alert_sorting_method_label4:
        bb_sorted = resultados2.sort("alert_date_max", True)

    # Use `map()` to batch-process elements in `aoi_grid`, limiting to `max_elementos`
    if max_elementos == 0:
        max_elementos = bb_sorted.size()
    else:
        max_elementos = max_elementos

    resultados3 = bb_sorted.toList(max_elementos)

    return resultados3


def obtener_datos_gee_total_v3(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
):
    # Attempt to run the total method first
    try:
        return evaluate_with_retry(
            obtener_datos_gee_total_v2(
                aoi,
                alert_raster,
                ee_reducer,
                pixel_size,
                min_alert_size_pixels,
                max_elementos,
                sorting,
            ),
            max_retries=1,
        )
    except Exception as e:
        print(
            "Initial call to obtener_datos_gee_total_v2 failed. Retrying with obtener_datos_gee_parcial_map..."
        )

    # Try running with grid size 150,000
    try:
        return evaluate_with_retry(
            obtener_datos_gee_parcial_map_2(
                aoi,
                alert_raster,
                ee_reducer,
                pixel_size,
                min_alert_size_pixels,
                max_elementos,
                sorting,
                150000,
            ),
            max_retries=1,
        )
    except Exception as e:
        print(
            "Call to obtener_datos_gee_parcial_map with grid size 150,000 failed. Retrying with grid size 100,000..."
        )

    # Try running with grid size 100,000
    try:
        return evaluate_with_retry(
            obtener_datos_gee_parcial_map_2(
                aoi,
                alert_raster,
                ee_reducer,
                pixel_size,
                min_alert_size_pixels,
                max_elementos,
                sorting,
                100000,
            ),
            max_retries=1,
        )
    except Exception as e:
        print(
            "Call to obtener_datos_gee_parcial_map with grid size 100,000 failed. Retrying with grid size 40,000..."
        )

    # Try running with grid size 40,000
    try:
        return evaluate_with_retry(
            obtener_datos_gee_parcial_map_2(
                aoi,
                alert_raster,
                ee_reducer,
                pixel_size,
                min_alert_size_pixels,
                max_elementos,
                sorting,
                40000,
            ),
            max_retries=1,
        )
    except Exception as e:
        print(
            "Call to obtener_datos_gee_parcial_map with grid size 40,000 failed. Retrying with grid size 20,000..."
        )

    # Try running with grid size 100,000
    return evaluate_with_retry(
        obtener_datos_gee_parcial_map_2(
            aoi,
            alert_raster,
            ee_reducer,
            pixel_size,
            min_alert_size_pixels,
            max_elementos,
            sorting,
            20000,
        ),
        max_retries=1,
    )


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
    gdf["alert_sources"] = pd.Series([],dtype="object")
    gdf["before_img"] = pd.Series([],dtype="str")
    gdf["before_img_info"] = pd.Series([],dtype="object")
    gdf["after_img"] = pd.Series([],dtype="str")
    gdf["after_img_info"] = pd.Series([],dtype="object")
    gdf["alert_polygon"] = pd.Series([],dtype="object")
    gdf["area_ha"] = pd.Series([],dtype="float")
    gdf["description"] = pd.Series([],dtype="str")
    gdf["admin1"] = pd.Series([],dtype="str")
    gdf["admin2"] = pd.Series([],dtype="str")
    gdf["admin3"] = pd.Series([],dtype="str")

    # Save gdf to file
    # gdf.set_crs(epsg = '4326', allow_override=True, inplace=True).to_file(gpkg_name, driver='GPKG')

    #Drop unnecessary columns
    gdf.drop('label', axis=1, inplace=True)

    # Check result
    return gdf
