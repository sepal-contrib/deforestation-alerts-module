from datetime import date, datetime
from math import floor, ceil

import ee
import pandas as pd
import geopandas as gpd


from component.message import cm
from shapely.geometry import Point, Polygon
import json
from sepal_ui.scripts import utils as su


def check_integer(text, exception_text):
    try:
        # Try to convert the text to an integer
        return float(text)
    except ValueError:
        # Raise an exception if the text cannot be converted to an integer
        raise Exception(exception_text)


def check_alert_filter_inputs(self):
    """Check inputs and raise error if inputs are not set or set incorrectly."""
    su.check_input(self.card01.children[1].v_model, "Select at least one alert source")
    check_integer(self.card02.children[1].v_model, "Min area has to be a number")
    su.check_input(
        self.card03.children[1].v_model, " Alert area selection method cannot be empty"
    )
    su.check_input(
        self.card06.children[1].v_model, "Alert sorting method cannot be empty"
    )
    if self.card03.children[1].v_model == "Chose by drawn polygon":
        su.check_input(self.drawn_item.to_json(), "No drawn polygons")
    check_integer(
        self.card04.children[1].v_model, "Number of alerts has to be a number"
    )

    return (
        self.card01.children[1].v_model,
        self.card02.children[1].v_model,
        self.card03.children[1].v_model,
        self.card06.children[1].v_model,
        self.card04.children[1].v_model,
        self.drawn_item.to_json(),
    )


# def obtener_datos_gee_parcial(
#     aoi_grid,
#     alert_raster,
#     ee_reducer,
#     pixel_size,
#     min_alert_size_pixels,
#     max_elementos=20
# ):
#     def get_bb(feature, min_alert_size_pixels):
#         grid_geometry = ee.Feature(feature).geometry()
#         bounding_boxes = alert_raster.clip(grid_geometry).reduceToVectors(
#             ee_reducer,
#             grid_geometry,
#             pixel_size,
#             "bb",
#             True,
#             None,
#             None,
#             None,
#             True,
#             1e13,
#             1,
#         )
#         # Eliminar cluster con menos de x cantidad de pixels
#         bb_cleaned = bounding_boxes.filter(
#             ee.Filter.gte("count", min_alert_size_pixels)
#         )
#         return bb_cleaned

#     resultados = []

#     def procesar_elemento(elemento):
#         bb = get_bb(elemento, min_alert_size_pixels)
#         # Aquí puedes definir la función de conteo de elementos
#         conteo = bb.size().getInfo()
#         print(conteo)

#         # Si hay elementos, llamar a getInfo() para almacenar los datos
#         if conteo > 0:
#             info = bb.toList(conteo).getInfo()
#             resultados.extend(info)
#             print("conteo fue mayor a 0, reusltados es ", len(resultados))

#     # Procesar cada elemento en el FeatureCollection hasta que alcancemos max_elementos
#     elementos = aoi_grid.toList(aoi_grid.size())
#     for i in range(elementos.size().getInfo()):
#         if len(resultados) >= max_elementos:
#             # print( 'resultado es ', len(resultados))
#             break
#         elemento = ee.FeatureCollection(elementos.get(i))
#         procesar_elemento(elemento)
#     # print('Out of loop')
#     return resultados


# def obtener_datos_gee_total(
#     aoi, alert_raster, ee_reducer, pixel_size, min_alert_size_pixels, max_elementos, sorting
# ):
#     import time
#     print('GEE background started')
#     stbg = time.time()

#     bounding_boxes = alert_raster.reduceToVectors(
#         ee_reducer, aoi, pixel_size, "bb", True, None, None, None, True, 1e13, 1
#     )

#     # Eliminar cluster con menos de x cantidad de pixels
#     bb_cleaned = bounding_boxes.filter(ee.Filter.gte("count", min_alert_size_pixels))

#     # Calcular el numero de alertas detectadas
#     bb_number_elements = bb_cleaned.size().getInfo()

#     if bb_number_elements <= 5000:
#         number_export = bb_number_elements
#     else:
#         number_export = 5000

#     if max_elementos > 0:
#         number_export = max_elementos
#     else:
#         number_export = number_export

#     if sorting == "Prioritize bigger area alerts":
#         bb_sorted = bb_cleaned.sort("count", False)
#     if sorting == "Prioritize smaller area alerts":
#         bb_sorted = bb_cleaned.sort("count", True)
#     if sorting == "Prioritize recent alerts":
#         bb_sorted = bb_cleaned.sort("alert_date_max", False)
#     if sorting == "Prioritize older alerts":
#         bb_sorted = bb_cleaned.sort("alert_date_max", True)

#     bb_sorted_list = bb_sorted.toList(number_export).getInfo()
#     etbg = time.time()
#     print('GEE background finished', etbg - stbg)

#     return bb_sorted_list


def obtener_datos_gee_parcial_map(
    aoi_grid,
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
    elementos_filtrados = aoi_grid.limit(20).map(procesar_elemento)

    # Convert the resulting list of lists to a flat list
    resultados = ee.FeatureCollection(elementos_filtrados).flatten()

    # Sort and convert to list
    if sorting == "Prioritize bigger area alerts":
        bb_sorted = resultados.sort("count", False)
    if sorting == "Prioritize smaller area alerts":
        bb_sorted = resultados.sort("count", True)
    if sorting == "Prioritize recent alerts":
        bb_sorted = resultados.sort("alert_date_max", False)
    if sorting == "Prioritize older alerts":
        bb_sorted = resultados.sort("alert_date_max", True)

    resultados2 = bb_sorted.toList(max_elementos).getInfo()

    return resultados2


def obtener_datos_gee_total_v2(
    aoi,
    alert_raster,
    ee_reducer,
    pixel_size,
    min_alert_size_pixels,
    max_elementos,
    sorting,
):
    import time

    print("GEE background started")
    start_time = time.time()

    # Apply reducer and filter to minimum alert size
    bounding_boxes = alert_raster.reduceToVectors(
        ee_reducer, aoi, pixel_size, "bb", True, None, None, None, True, 1e13, 1
    ).filter(ee.Filter.gte("count", min_alert_size_pixels))

    # Calculate the export limit on GEE side
    max_elements = ee.Number(5000) if max_elementos <= 0 else ee.Number(max_elementos)
    num_elements = bounding_boxes.size().min(max_elements)

    # Sort and convert to list
    if sorting == "Prioritize bigger area alerts":
        bb_sorted = bounding_boxes.sort("count", False)
    if sorting == "Prioritize smaller area alerts":
        bb_sorted = bounding_boxes.sort("count", True)
    if sorting == "Prioritize recent alerts":
        bb_sorted = bounding_boxes.sort("alert_date_max", False)
    if sorting == "Prioritize older alerts":
        bb_sorted = bounding_boxes.sort("alert_date_max", True)

    sorted_bb_list = bb_sorted.toList(num_elements).getInfo()

    print("GEE background finished", time.time() - start_time)
    return sorted_bb_list


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
