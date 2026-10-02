"""Tier 2: user-raster discovery against real Earth Engine. Skips without credentials."""

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine import user_raster as ur

# A public image that has an "elevation" band and no "Date" band.
SRTM = "USGS/SRTMGL1_003"


def test_native_discovery_on_a_collection_reads_the_first_image_not_the_mosaic(gee):
    import ee

    info = ee.data.computeValue(ur.discovery_request(catalogue.RADD_COLLECTION, "IMAGE_COLLECTION",
                                                     ["Alert", "Date"]))
    assert info["count"] > 600
    n = ur.native_from_discovery(info)
    assert n.crs == "EPSG:4326" and abs(n.scale - 10.0) < 0.01  # not the mosaic's 1 degree (111 km)
    assert ur.check_discovery(info, "radd", ["Alert", "Date"]) is None


def test_band_list_of_a_collection(gee):
    import ee

    names = ee.data.computeValue(ur.band_list_request(catalogue.RADD_COLLECTION, "IMAGE_COLLECTION"))
    assert {"Alert", "Date"} <= set(names)


def test_discovery_without_a_match_answers_a_message_key_not_an_earth_engine_error(gee):
    """``ee.Algorithms.If`` must not evaluate the branch that reads the missing
    bands: a collection with no image carrying the mapped bands, and an image
    without the mapped date band, both answer ``{"count": 0, ...}``."""
    import ee

    bands = ["Alert", "first_detection_date"]
    info = ee.data.computeValue(ur.discovery_request(catalogue.RADD_COLLECTION, "IMAGE_COLLECTION", bands))
    assert info == {"count": 0, "asset_type": "IMAGE_COLLECTION"}
    assert ur.check_discovery(info, "radd", bands) == "steps.alerts.err_no_images"

    bands = ["elevation", "Date"]
    info = ee.data.computeValue(ur.discovery_request(SRTM, "IMAGE", bands))
    assert info == {"count": 0, "asset_type": "IMAGE"}
    assert ur.check_discovery(info, "radd", bands) == "steps.alerts.err_missing_band"
