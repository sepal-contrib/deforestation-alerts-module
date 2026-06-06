import pandas as pd
import geopandas as gpd
import ee
import geemap
import os
import pathlib
import zipfile
import ast
import numpy as np

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from operator import itemgetter
from ipyleaflet import GeoData
from shapely.geometry import shape


def convert_julian_to_date(julian_date):
    # Split the input string into year and julian day
    julian_date_str = "%.3f" % julian_date
    year_str, julian_str = julian_date_str.split(".")
    year = int(year_str)

    # Calculate the date by adding the julian day to the beginning of the year
    date = datetime(year, 1, 1) + timedelta(days=int(julian_str) - 1)

    # Return the formatted date string
    return date.strftime("%Y-%m-%d")


def process_decimal_date(decimal_date):
    # Convert the decimal date to an integer representing the year
    year = int(decimal_date)

    # Calculate the fractional part representing the day of the year
    day_of_year = (decimal_date - year) * 365

    # Convert this to a date
    date = datetime(year, 1, 1) + timedelta(days=day_of_year)

    return date.strftime("%Y-%m-%d")


def is_future_date(target_date_str):
    """
    Check if the given date string is in the future compared to today's date.

    Parameters:
        target_date_str (str): The target date in the format 'YYYY-MM-DD'.

    Returns:
        bool: True if the target date is greater than today's date, False otherwise.
    """
    today = datetime.now().date()
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    return target_date > today


def get_planet_dates(input_date1, input_date2):
    # Ensure input_date is a datetime object
    if isinstance(input_date1, str):
        input_date1 = datetime.strptime(input_date1, "%Y-%m-%d")

    if isinstance(input_date2, str):
        input_date2 = datetime.strptime(input_date2, "%Y-%m-%d")

    # 1. First day of initial detection date
    first_day_current_month = input_date1.replace(day=1)

    # 2. Second day of the next month from initial detection date
    next_month = (
        first_day_current_month + relativedelta(months=+1) + relativedelta(days=+1)
    )

    # 3. First day of 5 month before
    prev_month5 = first_day_current_month + relativedelta(months=-5)

    # 4. First day of final detection date
    first_day_current_month2 = input_date2.replace(day=1)

    # 5. Second day of 3 month after last detection date
    next_3month = (
        first_day_current_month2 + relativedelta(months=3) + relativedelta(days=+1)
    )
    # 6. First day of 1 month before first detection date
    prev_2month = first_day_current_month + relativedelta(months=-2)

    return [
        prev_month5.strftime("%Y-%m-%d"),
        next_month.strftime("%Y-%m-%d"),
        first_day_current_month.strftime("%Y-%m-%d"),
        next_3month.strftime("%Y-%m-%d"),
        prev_2month.strftime("%Y-%m-%d"),
    ]


def get_sentinel2_dates(input_date1, input_date2):
    # Ensure input_date is a datetime object
    if isinstance(input_date1, str):
        input_date1 = datetime.strptime(input_date1, "%Y-%m-%d")

    if isinstance(input_date2, str):
        input_date2 = datetime.strptime(input_date2, "%Y-%m-%d")

    # 1. First day of initial detection date
    first_day_current_month = input_date1.replace(day=1)

    # 2. Second day of the next month from initial detection date
    next_month = (
        first_day_current_month + relativedelta(months=+1) + relativedelta(days=+1)
    )

    # 4. First day of 3 month before
    prev_month3 = first_day_current_month + relativedelta(months=-3)

    # 5. First day of final detection date
    first_day_current_month2 = input_date2.replace(day=1)

    # 5. Second day of 3 month after last detection date
    next_3month = (
        first_day_current_month2 + relativedelta(months=2) + relativedelta(days=+1)
    )
    # 6. First day of 1 month before first detection date
    prev_1month = first_day_current_month2 + relativedelta(months=-1)

    return [
        prev_month3.strftime("%Y-%m-%d"),
        input_date1.strftime("%Y-%m-%d"),
        prev_1month.strftime("%Y-%m-%d"),
        next_3month.strftime("%Y-%m-%d"),
    ]


def scalePlanet(image):
    rgb = image.select(["B", "G", "R"])
    nir = image.select(["N"])

    expression1 = "min(2540, rgb) / 10"
    expression2 = "min(2540, nir / 3.937) / 10"

    rgb_rscl = rgb.expression(expression1, {"rgb": rgb}).toUint8()
    nir_rscl = (
        nir.expression(expression2, {"nir": nir}).toUint8().select(["constant"], ["N"])
    )

    result = rgb_rscl.addBands(nir_rscl)
    return result


def scaleS2(image):
    image = image.select(["B2", "B3", "B4", "B8"], ["B", "G", "R", "N"])
    rgb = image.select(["B", "G", "R"])
    nir = image.select(["N"])

    expression1 = "min(2540, rgb / 2) / 10"
    expression2 = "min(2540, nir / 2.2) / 10"

    rgb_rscl = rgb.expression(expression1, {"rgb": rgb}).toUint8()
    nir_rscl = (
        nir.expression(expression2, {"nir": nir}).toUint8().select(["constant"], ["N"])
    )

    result = rgb_rscl.addBands(nir_rscl)
    return result


def scaleS2v2(image):
    image = image.select(["B2", "B3", "B4", "B8"], ["B", "G", "R", "N"])
    band1 = image.select("B").subtract(100).multiply(0.7)
    band2 = image.select("G").multiply(0.6)
    band3 = image.select("R").multiply(0.8)
    band4 = image.select("N").add(600).multiply(0.85)

    image_adj = band1.addBands(band2).addBands(band3).addBands(band4)
    rgb = image_adj.select(["B", "G", "R"])
    nir = image_adj.select(["N"])

    expression1 = "min(2540, rgb) / 10"
    expression2 = "min(2540, nir / 3.937) / 10"

    rgb_rscl = rgb.expression(expression1, {"rgb": rgb}).toUint8()
    nir_rscl = (
        nir.expression(expression2, {"nir": nir}).toUint8().select(["constant"], ["N"])
    )

    result = rgb_rscl.addBands(nir_rscl)
    return result


def download_both_images(image1, image2, image_name, source1, source2, region):
    from geemap import download_ee_image
    import ee
    import os

    if source1 in ["Sentinel 2", "Landsat"]:
        rimage1 = scaleS2v2(image1)
    elif source1 == "Planet NICFI":
        rimage1 = scalePlanet(image1)

    if source2 in ["Sentinel 2", "Landsat"]:
        rimage2 = scaleS2v2(image2)
    elif source2 == "Planet NICFI":
        rimage2 = scalePlanet(image2)

    if os.path.exists(image_name):
        pass
    else:
        download_ee_image(
            rimage1.addBands(rimage2),
            image_name,
            scale=4.77,
            crs="EPSG:3857",
            region=region,
            overwrite=True,
        )

    return image_name


def raster_to_gdf(raster_path, output_epsg, threshold):
    import rasterio
    import geopandas as gpd
    from shapely.geometry import shape, box
    from rasterio.features import shapes
    from rasterio.warp import calculate_default_transform, reproject, Resampling
    from fiona.crs import from_epsg
    from shapely.geometry import Point
    import json
    from ipyleaflet import GeoData
    import numpy as np

    # Open the raster file
    with rasterio.open(raster_path) as src:
        # Read the CRS of the input raster
        input_crs = src.crs

        # Read the first band of the raster (assuming a single band with 0/1 values)
        band = src.read(1)
        band_reclassified = np.where(band > threshold, 1, 0).astype(np.uint8)

        # Extract shapes (polygons) from the raster
        mask = None  # No mask, we're interested in all 0/1 values
        shapes_gen = list(shapes(band_reclassified, mask=mask, transform=src.transform))
        
        # Filter out elements with value 0; only keep those with value 1
        filtered_shapes = [geom for geom, val in shapes_gen if val == 1]
        
        features = []
        if len(filtered_shapes) > 0:
            for geom, value in shapes_gen:
                if value == 1:  # We're interested in 1 values or positive mask areas
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": geom,
                            "properties": {"value": int(value)},
                        }
                    )
        else:
            # If no shapes, calculate the centroid of the raster extent
            raster_extent = box(*src.bounds)  # Get the extent as a bounding box
            centroid = raster_extent.centroid  # Get the centroid point
            centroid_geom = Point(centroid.x, centroid.y)

            # Create a single feature with the centroid
            features.append(
                {
                    "type": "Feature",
                    "geometry": centroid_geom.__geo_interface__,  # Use shapely's geo interface
                    "properties": {"value": "centroid"},
                }
            )

        # Create GeoJSON structure
        geojson = {"type": "FeatureCollection", "features": features}

        # Convert the GeoJSON geometry CRS to the user-defined output EPSG
        gdf = gpd.GeoDataFrame.from_features(geojson["features"])
        gdf.set_geometry("geometry")

        # Set the CRS from input raster
        gdf = gdf.set_crs(input_crs, allow_override=True, inplace=True).to_crs(
            epsg=output_epsg
        )  # Reproject to the output EPSG

        # geo_json_layer = GeoData(
        #     geo_dataframe=gdf,
        #     name='Deforestation'
        # )

    return gdf


#Funcion para verificar si el modelo de DL encontro poligonos de deforestacion
def verify_raster(raster_path, threshold):
    import rasterio
    import numpy as np
    from rasterio.features import shapes

    with rasterio.open(raster_path) as src:
        band = src.read(1)
        band_reclassified = np.where(band > threshold, 1, 0).astype(np.uint8)

        # Extract shapes (polygons) from the raster
        shapes_gen = list(shapes(band_reclassified, transform=src.transform))
        # Filter out elements with value 0; only keep those with value 1
        filtered_shapes = [geom for geom, val in shapes_gen if val == 1]

    if len(filtered_shapes) == 0 :
        return False
    else:
        return True


# Función que convierte el formato de los elementos de una lista de features
def convertir_formato(features):
    # Estructura de estilo que se añadirá a las nuevas features
    nuevo_estilo = {
        "style": {
            "stroke": True,
            "color": "#2196F3",
            "weight": 4,
            "opacity": 0.5,
            "fill": False,
            "fillColor": None,
            "fillOpacity": 0.1,
            "clickable": True,
        }
    }

    # Convertir cada feature al nuevo formato
    nuevas_features = []
    for feature in features:
        # Crear una nueva estructura sin el atributo 'value' en properties
        nueva_feature = {
            "type": "Feature",
            "properties": nuevo_estilo,
            "geometry": feature["geometry"],  # Mantener la geometría original
        }
        nuevas_features.append(nueva_feature)

    return nuevas_features


# Función que convierte el formato de los elementos de una lista de features
def convertir_formato2(features, color="#2196F3"):
    # Estructura de estilo que se añadirá a las nuevas features
    nuevo_estilo = {
        "style": {
            "color": color,
            "pane": "overlayPane",
            "attribution": None,
        }
    }

    # Convertir cada feature al nuevo formato
    nuevas_features = []
    for feature in features:
        # Crear una nueva estructura sin el atributo 'value' en properties
        nueva_feature = {
            "type": "Feature",
            "properties": nuevo_estilo,
            "geometry": feature["geometry"],  # Mantener la geometría original
        }
        nuevas_features.append(nueva_feature)

    return nuevas_features


def convertir_formato3(input_features, color="#2196F3"):
    """
    Convert a list of features from the input format to the output format,
    allowing the user to define the style color.

    Parameters:
        input_features (list): A list of feature dictionaries in the input format.
        color (str): Hex code for the stroke color (default: "#2196F3").

    Returns:
        list: A list of feature dictionaries in the target format.
    """
    style = {
        "pane": "overlayPane",
        "attribution": None,
        "bubblingMouseEvents": True,
        "fill": True,
        "smoothFactor": 1,
        "noClip": False,
        "stroke": True,
        "color": color,
        "weight": 4,
        "opacity": 0.5,
        "lineCap": "round",
        "lineJoin": "round",
        "dashArray": None,
        "dashOffset": None,
        "fillColor": None,
        "fillOpacity": 0.01,
        "fillRule": "evenodd",
        "interactive": True,
        "clickable": True,
    }

    def to_list(item):
        """
        Recursively convert tuples (or lists) to lists.
        """
        if isinstance(item, (tuple, list)):
            return [to_list(sub) for sub in item]
        return item

    converted_features = []
    for input_feature in input_features:
        geometry = input_feature.get("geometry", {})
        geom_type = geometry.get("type", "Polygon")
        coords = geometry.get("coordinates", [])
        coords_converted = to_list(coords)

        new_feature = {
            "type": "Feature",
            "properties": {"style": style},
            "geometry": {"type": geom_type, "coordinates": coords_converted},
        }
        converted_features.append(new_feature)

    return converted_features


def save_prediction(original_img, prediction, threshold, output_path):

    import rasterio
    import numpy as np

    """
    Save prediction as geotiff .

    Parameters:
    - original_img (list of str): Input raster file path.
    - prediction (object)
    - output_path (str): Output raster file path.
    """
    # Open the input rasters
    src1 = rasterio.open(original_img)

    # Read metadata of the first raster
    out_meta = src1.meta.copy()

    # Reclassify values: set all values greater than 0.2 to 1, and others to 0
    prediction_reclassified = np.where(prediction > threshold, 1, 0)

    # Update metadata to reflect the new number of bands

    out_meta.update(count=1)
    out_meta.update({"dtype": "float32"})

    # Write the stacked raster to the output file
    with rasterio.open(output_path, "w", **out_meta) as dest:
        dest.write(prediction_reclassified, indexes=1)

    return output_path


def save_prediction_prob(original_img, prediction, output_path):

    import rasterio
    import numpy as np

    """
    Save prediction as geotiff .

    Parameters:
    - original_img (list of str): Input raster file path.
    - prediction (object)
    - output_path (str): Output raster file path.
    """
    # Open the input rasters
    src1 = rasterio.open(original_img)

    # Read metadata of the first raster
    out_meta = src1.meta.copy()

    # Reclassify values: set all values greater than 0.2 to 1, and others to 0
    # prediction_reclassified = np.where(prediction > threshold, 1, 0)

    # Update metadata to reflect the new number of bands

    out_meta.update(count=1)
    out_meta.update({"dtype": "float32"})

    # Write the stacked raster to the output file
    with rasterio.open(output_path, "w", **out_meta) as dest:
        dest.write(prediction, indexes=1)

    return output_path


def apply_dl_model(image, model):
    import os
    import rasterio
    import numpy as np
    import tensorflow as tf
    from matplotlib import pyplot as plt

    from tensorflow.keras import layers, models
    from tensorflow.keras import backend as K
    from MightyMosaic import MightyMosaic

    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

    def read_image(file_path):
        file_path = file_path  # .decode('utf-8')
        with rasterio.open(file_path) as src:
            img_array = src.read()
        img_array2 = img_array / 255
        img_array3 = np.transpose(img_array2, (1, 2, 0))
        return img_array3.astype(np.float32)

    if not os.path.exists(image):
        raise Exception("First download selected images.")

    # Input large image
    input_image = image

    # size of patches
    patch_size = 256

    # Number of classes
    n_classes = 1

    img = read_image(input_image)

    @tf.keras.utils.register_keras_serializable()
    class RepeatElements(layers.Layer):
        def __init__(self, rep, axis=3, **kwargs):
            super(RepeatElements, self).__init__(**kwargs)
            self.rep = rep
            self.axis = axis

        def call(self, inputs):
            return K.repeat_elements(inputs, self.rep, self.axis)

        def compute_output_shape(self, input_shape):
            shape = list(input_shape)
            shape[self.axis] *= self.rep
            return tuple(shape)

        def get_config(self):
            config = super(RepeatElements, self).get_config()
            config.update({"rep": self.rep, "axis": self.axis})
            return config

    model1 = models.load_model(
        model, custom_objects={"RepeatElements": RepeatElements}, compile=False
    )
    mosaic = MightyMosaic.from_array(img, (256, 256), overlap_factor=2)
    prediction1 = mosaic.apply(
        lambda x: model1.predict(x, verbose=0), progress_bar=False, batch_size=2
    )
    final_prediction1 = prediction1.get_fusion()

    return final_prediction1


# Function to calculate total area
def calculate_total_area(gdf):
    # Reproject to a CRS that uses meters, change depending on your location
    gdf_proj = gdf.to_crs("EPSG:3857")

    # Calculate the area for each polygon and add it to a new column
    gdf_proj["area_m2"] = gdf_proj["geometry"].area

    # Sum the areas
    total_area = gdf_proj["area_m2"].sum()

    return total_area / 10000


def simplify_and_extract_features(
    gdf: gpd.GeoDataFrame, column: str, tolerance: float = 2.00
):
    """
    Simplifies the geometries in the GeoDataFrame and extracts the __geo_interface__['features'].

    Parameters:
    gdf (gpd.GeoDataFrame): Input GeoDataFrame.
    column (str): Column name of geometries to simplify.
    tolerance (float): Tolerance parameter for the simplify function. Default is 0.01.

    Returns:
    list: Simplified GeoJSON features.
    """
    import geopandas as gpd

    # Ensure the column exists
    if column not in gdf.columns:
        raise ValueError(f"Column '{column}' not found in the GeoDataFrame")

    gdf_repro = gdf.to_crs(epsg="3857")

    # Apply simplify to the geometry column and store the result in a new column
    gdf_repro["simplified_geometry"] = gdf_repro[column].simplify(
        tolerance, preserve_topology=True
    )

    # Reproyect and Extract the __geo_interface__ from the simplified geometries
    gdf2 = gdf_repro.set_geometry("simplified_geometry").to_crs(epsg="4326")

    features = gdf2.set_geometry("simplified_geometry").__geo_interface__["features"]

    return features


def add_files_to_zip(zip_filename, file1, file2):
    import zipfile

    # Open a zip file in write mode, create it if it doesn't exist
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        # Add the files to the zip
        zipf.write(file1)
        zipf.write(file2)


def is_after_march_2025(target_date_str):
    """
    Check if the given date string is after 2025-03-31.

    Parameters:
        target_date_str (str): The target date in the format 'YYYY-MM-DD'.

    Returns:
        bool: True if the target date is after 2025-03-31, False otherwise.
    """
    # Define the specific date to compare against
    specific_date = datetime.strptime("2025-03-31", "%Y-%m-%d").date()

    # Parse the target date string and convert it to a date object
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()

    # Compare the target date with the specific date
    return target_date > specific_date


def check_planet_collection_access():
    # Initialize the GEE account
    # Define the collection names and their corresponding regions
    collections = {
        "americas": "projects/planet-nicfi/assets/basemaps/americas",
        "africa": "projects/planet-nicfi/assets/basemaps/africa",
        "asia": "projects/planet-nicfi/assets/basemaps/asia",
    }

    # Initialize a dictionary to store the results
    access_status = {}

    # Iterate over each collection and check its access status
    for region, collection in collections.items():
        try:
            # Load the image collection
            img_collection = ee.ImageCollection(collection)

            # Get the size of the collection
            size = img_collection.size().getInfo()

            # Check if the size is greater than 0 and update the access status dictionary accordingly
            if size > 0:
                access_status[region] = "has_access"
            else:
                access_status[region] = "no_access"
        except ee.EEException as e:
            # Handle the case where the collection does not exist or user doesn't have access
            if "not found" in str(e):
                access_status[region] = "no_access"
            else:
                raise  # Re-raise other EEException errors

    # Return the dictionary containing the access status for each region
    return access_status


def check_access(dictionary):
    """
    Check if all values in the given dictionary are 'no_access'.

    Parameters:
        dictionary (dict): The dictionary to be checked.

    Returns:
        bool: True if all values are 'no_access', False otherwise.
    """
    # Iterate through each value in the dictionary
    for value in dictionary.values():
        # Check if any value is 'has_access'
        if value == "has_access":
            return False

    # If no 'has_access' found, return True
    return True


def getPlanetMonthly(geometry, date1, date2):

    # Define the no-access result dictionary
    no_images = [
        {
            "value": "Not available",
            "image_id": "Not available",
            "milis": "Not available",
            "source": "Planet NICFI",
            "cloud_cover": "Not available",
        }
    ]

    access_status = check_planet_collection_access()

    if check_access(access_status) is True:
        return no_images
    else:
        lista2 = [
            "American Samoa",
            "Arunachal Pradesh",
            "Ashmore and Cartier Islands",
            "Baker Island",
            "Bangladesh",
            "Bhutan",
            "British Indian Ocean Territory",
            "Brunei Darussalam",
            "Cambodia",
            "Christmas Island",
            "Cocos (Keeling) Islands",
            "Cook Islands",
            "Fiji",
            "French Polynesia",
            "Guam",
            "Howland Island",
            "India",
            "Indonesia",
            "Jarvis Island",
            "Johnston Atoll",
            "Kingman Reef",
            "Kiribati",
            "Lao People's Democratic Republic",
            "Malaysia",
            "Maldives",
            "Marshall Islands",
            "Micronesia (Federated States of)",
            "Myanmar",
            "Nauru",
            "Nepal",
            "Niue",
            "Northern Mariana Islands",
            "Palau",
            "Palmyra Atoll",
            "Papua New Guinea",
            "Paracel Islands",
            "Philippines",
            "Samoa",
            "Scarborough Reef",
            "Singapore",
            "Solomon Islands",
            "Spratly Islands",
            "Sri Lanka",
            "Thailand",
            "Timor-Leste",
            "Tokelau",
            "Tonga",
            "Tuvalu",
            "Vanuatu",
            "Viet Nam",
            "Wake Island",
            "Wallis and Futuna",
        ]

        # Define the Asia footprint based on countries in lista2
        footprint_asia = (
            ee.FeatureCollection("FAO/GAUL_SIMPLIFIED_500m/2015/level0")
            .filter(ee.Filter.inList("ADM0_NAME", lista2))
            .geometry()
        )

        collections = {
            "americas": "projects/planet-nicfi/assets/basemaps/americas",
            "africa": "projects/planet-nicfi/assets/basemaps/africa",
            "asia": "projects/planet-nicfi/assets/basemaps/asia",
        }
        # Initialize an empty image collection to store the merged results
        intersecting_images = ee.ImageCollection([])

        for region, status in access_status.items():
            if status == "has_access":
                try:
                    img_collection = ee.ImageCollection(collections[region])

                    # Use footprint_asia for Asia and the general geometry for others
                    if region == "asia":
                        # Check intersection with the specified geometry using ee.Algorithms.If
                        intersects_geometry = ee.Algorithms.If(
                            footprint_asia.intersects(geometry, 10),
                            img_collection,
                            ee.ImageCollection([]),
                        )
                    else:
                        # Check intersection with the specified geometry using ee.Algorithms.If
                        intersects_geometry = ee.Algorithms.If(
                            img_collection.first().geometry().intersects(geometry, 10),
                            img_collection,
                            ee.ImageCollection([]),
                        )

                    intersecting_images = intersecting_images.merge(intersects_geometry)
                except ee.EEException as e:
                    if "not found" in str(e):
                        print(
                            f"{region} collection not found or user does not have access."
                        )

        if intersecting_images.size().getInfo() == 0:
            return no_images

        else:
            selected_planet = ee.ImageCollection(intersecting_images)

            planet_filtered_collection = selected_planet.filterDate(
                date1, date2
            ).filterBounds(geometry)

            # Retrieve all image IDs with a single getInfo call
            image_ids = planet_filtered_collection.aggregate_array(
                "system:id"
            ).getInfo()
            elements = []

            if len(image_ids) > 0:
                # Process each image ID to format the name
                for image_id in image_ids:
                    # Split the image ID into parts
                    parts = image_id.split("/")
                    # Extract region and date parts
                    region_part = parts[-2].title()
                    date_part = parts[-1].split("_")[-2]
                    # Parse year and month
                    year = date_part[:4]
                    month = int(date_part[5:7])

                    date_str = date_part + "-01"
                    date_str2 = datetime.strptime(date_str, "%Y-%m-%d").strftime(
                        "%b %Y"
                    )

                    # Format the name
                    name = f"Planet Monthly {region_part} {date_str2}"
                    # Create image to display
                    planet_clip = ee.Image(image_id)  ##.clip(geometry)

                    # t1 = ee.Number(planet_clip.get('system:time_start')).getInfo()
                    t2 = ee.Number(planet_clip.get("system:time_end"))
                    t3 = ee.Date(t2).advance(-1, "days").millis().getInfo()

                    dictionary = {
                        "value": name,
                        "image_id": image_id,
                        "milis": t3,
                        "source": "Planet NICFI",
                        "cloud_cover": "Not available",
                    }
                    elements.append(dictionary)

            elif len(image_ids) == 0 and (
                is_future_date(date2) or is_after_march_2025(date2)
            ):

                last_two_imgs = selected_planet.sort("system:time_end", False).limit(2)
                image_ids_2 = last_two_imgs.aggregate_array("system:id").getInfo()
                # Process each image ID to format the name
                for image_id in image_ids_2:
                    # Split the image ID into parts
                    parts = image_id.split("/")
                    # Extract region and date parts
                    region_part = parts[-2].title()
                    date_part = parts[-1].split("_")[-2]
                    # Parse year and month
                    year = date_part[:4]
                    month = int(date_part[5:7])

                    date_str = date_part + "-01"
                    date_str2 = datetime.strptime(date_str, "%Y-%m-%d").strftime(
                        "%b %Y"
                    )

                    # Format the name
                    name = f"Planet Monthly {region_part} {date_str2}"
                    # Create image to display
                    planet_clip = ee.Image(image_id)  ##.clip(geometry)

                    # t1 = ee.Number(planet_clip.get('system:time_start')).getInfo()
                    t2 = ee.Number(planet_clip.get("system:time_end"))
                    t3 = ee.Date(t2).advance(-1, "days").millis().getInfo()

                    dictionary = {
                        "value": name,
                        "image_id": image_id,
                        "milis": t3,
                        "source": "Planet NICFI",
                        "cloud_cover": "Not available",
                    }
                    elements.append(dictionary)

    return elements


def getIndividualS2(geometry, date1, date2):
    s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
    s2_filtered = (
        s2.filterDate(date1, date2)
        .filterBounds(geometry)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90))
    )

    # Retrieve all unique Generation time values
    s2_dates_list = s2_filtered.aggregate_array("GENERATION_TIME").distinct().getInfo()
    elements = []

    if len(s2_dates_list) > 0:
        # Process each unique Generation time to get the mosaic

        for s2_date in s2_dates_list:
            # Create image to display
            s2_same_date = s2_filtered.filter(ee.Filter.eq("GENERATION_TIME", s2_date))
            # s2_same_date_clip = s2_same_date.mosaic().clip(geometry)

            # Calculate properties of images
            mean_cloud = (
                s2_same_date.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE")
                .format("%.2f")
                .getInfo()
            )
            date = datetime.fromtimestamp(s2_date / 1000).strftime("%Y-%m-%d")
            name = f"Sentinel 2 {date} "

            dictionary = {
                "value": name,
                "image_id": s2_date,
                "milis": s2_date,
                "source": "Sentinel 2",
                "cloud_cover": mean_cloud,
            }
            elements.append(dictionary)

    return elements


def getIndividualLandsat(geometry, date1, date2):
    l8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    l9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    landsat = l8.merge(l9)
    landsat_filtered = (
        landsat.filterDate(date1, date2)
        .filterBounds(geometry)
        .filter(ee.Filter.lt("CLOUD_COVER", 90))
    )

    # Retrieve all unique Generation time values
    landsat_dates_list = (
        landsat_filtered.aggregate_array("DATE_PRODUCT_GENERATED").distinct().getInfo()
    )
    elements = []

    if len(landsat_dates_list) > 0:
        # Process each unique Generation time to get the mosaic

        for landsat_date in landsat_dates_list:
            # Create image to display
            landsat_img = landsat_filtered.filter(
                ee.Filter.eq("DATE_PRODUCT_GENERATED", landsat_date)
            ).first()

            # Calculate properties of images
            cloud_cover = (
                ee.Number(landsat_img.get("CLOUD_COVER")).format("%.2f").getInfo()
            )
            scene_id = landsat_img.get("LANDSAT_SCENE_ID")
            date = datetime.fromtimestamp(landsat_date / 1000).strftime("%Y-%m-%d")
            name = f"Landsat {date} "

            dictionary = {
                "value": name,
                "image_id": scene_id,
                "milis": ee.Number(landsat_date).getInfo(),
                "source": "Landsat",
                "cloud_cover": cloud_cover,
            }
            elements.append(dictionary)

    elif len(landsat_dates_list) == 0:
        # Define the no-access result dictionary
        elements = [
            {
                "value": "Not available",
                "image_id": "Not available",
                "milis": "Not available",
                "source": "Planet NICFI",
                "cloud_cover": "Not available",
            }
        ]

    return elements


def geojson_to_geodataframe(geojson):
    from shapely.geometry import shape

    """
    Convert a GeoJSON-like dictionary to a GeoDataFrame.

    Parameters:
        geojson (dict): A dictionary representing a GeoJSON FeatureCollection.

    Returns:
        gpd.GeoDataFrame: The converted GeoDataFrame.

    Raises:
        ValueError: If the input has no features.
    """
    if not geojson.get("features"):
        raise ValueError("The input GeoJSON has no features.")

    # Extract features and convert to GeoDataFrame
    features = geojson["features"]
    geometries = [shape(feature["geometry"]) for feature in features]
    properties = [feature["properties"] for feature in features]

    # Create the GeoDataFrame
    gdf = gpd.GeoDataFrame(properties, geometry=geometries)

    return gdf


def multipolygon_to_geodataframe(geometry):
    """
    Convert a shapely.geometry.multipolygon.MultiPolygon or shapely.geometry.polygon.Polygon to a GeoDataFrame.

    Parameters:
        geometry (shapely.geometry.base.BaseGeometry): A MultiPolygon or Polygon object.

    Returns:
        gpd.GeoDataFrame: A GeoDataFrame with each Polygon as a separate row.

    Raises:
        ValueError: If the input is not a MultiPolygon or Polygon.
    """
    from shapely.geometry import shape, MultiPolygon, Polygon

    if isinstance(geometry, MultiPolygon):
        # Extract individual polygons from the MultiPolygon
        polygons = list(geometry.geoms)
    elif isinstance(geometry, Polygon):
        # Treat the single Polygon as a list with one element
        polygons = [geometry]
    else:
        raise ValueError("The input must be a MultiPolygon or Polygon.")

    # Create a GeoDataFrame
    gdf = gpd.GeoDataFrame(geometry=polygons)

    return gdf


def filter_features_by_color(features, color_to_remove):
    """
    Filters out GeoJSON features with a specific color.

    Args:
        features (list of dict): List of GeoJSON feature dictionaries.
        color_to_remove (str): The color to filter out (e.g., "red").

    Returns:
        list of dict: Filtered list of GeoJSON features.
    """
    if not isinstance(features, list):
        raise ValueError("The 'features' argument must be a list of dictionaries.")

    filtered_features = []

    for feature in features:
        # Check if the feature is a dictionary
        if not isinstance(feature, dict):
            continue

        # Get the properties of the feature
        properties = feature.get("properties", {})

        # Check if the 'color' key exists and if it matches the color_to_remove
        if properties.get("style").get("color") != color_to_remove:
            filtered_features.append(feature)

    return filtered_features


# Harmonize L8→S2, handling GEE scaling differences
def harmonizeL8ToS2_scaled(oliL2):
    # 1) Convert raw DN to float reflectance
    refl = (
        oliL2.select(
            ["SR_B1", "SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
            ["B1", "B2", "B3", "B4", "B8", "B6", "B7"],
        )
        .multiply(0.0000275)
        .add(-0.2)
    )

    # 2) Coeffs from HLS v2.0 Table 5 (Sentinel-2A → OLI), invert for OLI→S2
    slopes = ee.Image.constant([0.9959, 0.9778, 1.0053, 0.9765, 0.9983, 0.9987, 1.003])
    itcps = ee.Image.constant(
        [-0.0002, -0.0040, -0.0009, 0.0009, -0.0001, -0.0011, -0.0012]
    )

    # 3) Apply (ρ_OLI – b) / a to get S2 reflectance
    s2Refl = (
        refl.subtract(itcps)
        .divide(slopes)
        .set("system:time_start", oliL2.get("system:time_start"))
    )

    # 4) (Optional) back to 0–10000 DN to match S2_SR scale
    s2DN = s2Refl.multiply(10000).toShort()

    # Re‐attach metadata
    result = ee.Image(s2DN).copyProperties(oliL2)

    return ee.Image(result)


def SFIM_pan_sharpen(img, pan):
    imgScale = img.projection().nominalScale()
    panScale = pan.projection().nominalScale()

    kernelWidth = imgScale.divide(panScale)
    kernel = ee.Kernel.square(radius=kernelWidth.divide(2))

    panSmooth = pan.reduceNeighborhood(reducer=ee.Reducer.mean(), kernel=kernel)

    img = img.resample("bicubic")
    sharp = img.multiply(pan).divide(panSmooth).reproject(pan.projection())
    return sharp
