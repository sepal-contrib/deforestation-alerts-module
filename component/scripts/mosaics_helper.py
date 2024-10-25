import pandas as pd
import geopandas as gpd
import ee
import geemap
import os
import pathlib
import zipfile
from datetime import datetime
import ast


from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta


def process_decimal_date(decimal_date):
    # Convert the decimal date to an integer representing the year
    year = int(decimal_date)

    # Calculate the fractional part representing the day of the year
    day_of_year = (decimal_date - year) * 365

    # Convert this to a date
    date = datetime(year, 1, 1) + timedelta(days=day_of_year)

    return date.strftime("%Y-%m-%d")


def get_five_dates(input_date):
    # Ensure input_date is a datetime object
    if isinstance(input_date, str):
        input_date = datetime.strptime(input_date, "%Y-%m-%d")

    # 1. First day of the current month
    first_day_current_month = input_date.replace(day=1)

    # 2. First day of the next month
    next_month = first_day_current_month + relativedelta(months=+1)

    # 3. First day of the previous month
    prev_month = first_day_current_month + relativedelta(months=-1)

    # 4.  First day of 2 month after
    next_month2 = first_day_current_month + relativedelta(months=+2)

    # 5. First day of 2 month before
    prev_month2 = first_day_current_month + relativedelta(months=-1)

    return [first_day_current_month, next_month, next_month2, prev_month, prev_month2]


def filter_older_dates(date_list, comparison_date):
    # Ensure comparison_date is a datetime object
    if isinstance(comparison_date, str):
        comparison_date = datetime.strptime(comparison_date, "%Y-%m-%d")

    # Convert the dates in the list to datetime objects if necessary
    date_list = [
        datetime.strptime(d, "%Y-%m-%d") if isinstance(d, str) else d for d in date_list
    ]

    # Filter out the dates that are older than the comparison_date
    older_dates = [d.strftime("%Y-%m-%d") for d in date_list if d < comparison_date]

    return older_dates


def reEscalePlanet(image):
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


def reEscaleS2(image):
    rgb = image.select(["B", "G", "R"])
    nir = image.select(["N"])

    expression1 = "min(2540, rgb / 2) / 10"
    expression2 = "min(2540, nir / 2.5) / 10"

    rgb_rscl = rgb.expression(expression1, {"rgb": rgb}).toUint8()
    nir_rscl = (
        nir.expression(expression2, {"nir": nir}).toUint8().select(["constant"], ["N"])
    )

    result = rgb_rscl.addBands(nir_rscl)
    return result


def download_both_images(image1, image2, image_name):
    from geemap import download_ee_image
    import ee

    download_ee_image(image1.addBands(image2), image_name, scale=4.77, crs="EPSG:3857")
    return image_name


# ToDO
# def planet_monthly_mosaics(aoi, dates, text):
#     if text == 'before':
#         pass
#     else if text == 'after':
#         pass
#     return rasters


def sentinel2_individual_images(aoi, dates, text):
    # Sentinel 2 collections
    s2 = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    csPlus = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
    s2List = ee.List([])

    def changeBandNameS2(image):
        return image.select(["B2", "B3", "B4", "B8"], ["blue", "green", "red", "nir"])

    # Cloud Masks parameters
    QA_BAND = "cs_cdf"
    CLEAR_THRESHOLD = 0.65

    def maskCLouds(img):
        mask = img.select(QA_BAND).gte(CLEAR_THRESHOLD)
        image = changeBandNameS2(img)
        result = image.updateMask(mask)
        return result

    filterCollection = (
        s2.filterBounds(aoi)
        .filterDate(startDate, endDate)
        .ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloudCover)
        .linkCollection(csPlus, [QA_BAND])
    )


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

        # Convert the shapes generator into a list of geo-features
        features = []
        if len(shapes_gen) > 0:
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
def convertir_formato2(features):
    # Estructura de estilo que se añadirá a las nuevas features
    nuevo_estilo = {"style": {"color": "#2196F3", "pane": "overlayPane"}}

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

    # from patchify import patchify, unpatchify
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
    print("inicio aplicar modelo")
    prediction1 = mosaic.apply(
        lambda x: model1.predict(x, verbose=0), progress_bar=False, batch_size=2
    )
    print("final aplicar modelo")
    final_prediction1 = prediction1.get_fusion()

    return final_prediction1


# Function to calculate total area
def calculate_total_area(gdf):
    # Reproject to a CRS that uses meters (assuming UTM zone 10N, change depending on your location)
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


def add_files_to_zip(zip_filename, file1, file2, file3):
    import zipfile

    # Open a zip file in write mode, create it if it doesn't exist
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        # Add the files to the zip
        zipf.write(file1)
        zipf.write(file2)
        zipf.write(file3)
    # print(f"Files {file1}, {file2}, {file3} have been added to {zip_filename}")
