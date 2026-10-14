import pandas as pd
import geopandas as gpd
import ee
import os
import pathlib
import zipfile
import ast
import numpy as np

from datetime import datetime, timedelta
from operator import itemgetter
from ipyleaflet import GeoData
from shapely.geometry import shape

# CNN code consolidated into cnn_worker (D3); re-export so `from
# mosaics_helper import *` keeps providing these names.
from deforestation_alerts.scripts.cnn_worker import (  # noqa: F401
    apply_dl_model,
    save_prediction_prob,
    RepeatElements,
)


def convert_julian_to_date(julian_date):
    # vectorize.py returns None for alert_date_min/max when a polygon's date
    # pixels are all nodata (stored in a gdf as NaN). Treat any missing/non-finite
    # value as "N/A" rather than crashing: a single NaN here used to blank the
    # whole Overview map ("not enough values to unpack") since the marker builder
    # converts every row's dates without per-row guarding.
    if julian_date is None or not np.isfinite(julian_date):
        return "N/A"

    # Split the input string into year and julian day
    julian_date_str = "%.3f" % julian_date
    year_str, julian_str = julian_date_str.split(".")
    year = int(year_str)

    # Calculate the date by adding the julian day to the beginning of the year
    date = datetime(year, 1, 1) + timedelta(days=int(julian_str) - 1)

    # Return the formatted date string
    return date.strftime("%Y-%m-%d")


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


def _scale_for_source(image, source):
    if source in ("Sentinel 2", "Landsat"):
        return scaleS2v2(image)
    if source == "Planet NICFI":
        return scalePlanet(image)
    raise ValueError(f"Unsupported imagery source: {source!r}")


def download_both_images(image1, image2, image_name, source1, source2, region):
    from deforestation_alerts.scripts.raster_export import download_ee_image

    rimage1 = _scale_for_source(image1, source1)
    rimage2 = _scale_for_source(image2, source2)

    download_ee_image(
        rimage1.addBands(rimage2),
        image_name,
        scale=4.77,
        crs="EPSG:3857",
        region=region,
        overwrite=True,
    )

    return image_name


def prediction_patches(raster_path, threshold, crs, min_size_ha=0.0):
    """The patches of a CNN prediction raster, in ``crs``, with their ``area_ha``.

    A pixel is a detection when its probability is above ``threshold``. Each
    polygon ``shapes`` makes of the detections is one patch, measured in
    ``crs`` (the project's grid CRS: equal-distance metres, unlike the
    composite's EPSG:3857, which overstates the area by ~20 % at 24 deg S).
    Patches under ``min_size_ha`` are dropped: the alert set's minimum size,
    the same one Integrate applies to its clusters. Nothing detected is an
    empty frame. ``crs=None`` (no grid yet) measures in the local UTM zone.
    """
    import rasterio
    from rasterio.features import shapes

    with rasterio.open(raster_path) as src:
        detected = (src.read(1) > threshold).astype(np.uint8)
        geoms = [shape(geom) for geom, _ in shapes(detected, mask=detected.astype(bool),
                                                     transform=src.transform)]
        src_crs = src.crs
    patches = gpd.GeoDataFrame(geometry=geoms, crs=src_crs)
    if crs is None and len(patches):
        crs = patches.estimate_utm_crs()  # no grid yet: the local UTM zone
    patches = patches.to_crs(crs) if crs is not None else patches
    patches["area_ha"] = patches.geometry.area / 10_000
    return patches[patches["area_ha"] >= float(min_size_ha)].reset_index(drop=True)


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


def calculate_total_area(gdf, crs=None):
    """The polygons' total area in hectares, measured in ``crs``.

    ``crs`` is the project's grid CRS (``project.grid.epsg``), the one
    Integrate measures every alert's ``area_ha`` in, so a reviewed polygon and
    the alert it replaces are on one scale. Without one (no grid yet), the
    polygons' own UTM zone. Never EPSG:3857: its areas are ~20 % too large at
    Paraguay's latitudes.
    """
    return float(gdf.to_crs(crs or gdf.estimate_utm_crs()).geometry.area.sum()) / 10_000


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

    # The tolerance is in metres: a projected frame (the project's grid CRS)
    # is simplified as it is.
    gdf_repro = gdf.copy() if gdf.crs is not None and gdf.crs.is_projected else gdf.to_crs(epsg="3857")

    # Apply simplify to the geometry column and store the result in a new column
    gdf_repro["simplified_geometry"] = gdf_repro[column].simplify(
        tolerance, preserve_topology=True
    )

    # Reproyect and Extract the __geo_interface__ from the simplified geometries
    gdf2 = gdf_repro.set_geometry("simplified_geometry").to_crs(epsg="4326")

    features = gdf2.set_geometry("simplified_geometry").__geo_interface__["features"]

    return features


def add_files_to_zip(zip_filename, *files):
    """Zip LOCAL files, each stored under its basename.

    ``arcname`` matters since the members live in a scratch folder or the set
    folder: without it zipfile stores the full path, so the user unpacking the
    archive would get a directory tree instead of the files they asked for.
    """
    import os
    import zipfile

    with zipfile.ZipFile(zip_filename, "w") as zipf:
        for path in files:
            zipf.write(path, arcname=os.path.basename(path))


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
