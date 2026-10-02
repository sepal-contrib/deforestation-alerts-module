from datetime import date

import numpy as np
import pytest

from deforestation_alerts.engine import catalogue, presets
from deforestation_alerts.project.models import BandMap, CustomSource, NativeGrid

WIN = (date(2024, 1, 1), date(2025, 6, 30))


def _src(ref, asset_type, fmt="radd", encoding="yyddd", alert="Alert", dt="Date"):
    return CustomSource(name="c", origin="gee", ref=ref, asset_type=asset_type, format=fmt, date_encoding=encoding,
                        band_map=BandMap(alert=alert, date=dt), native=NativeGrid(crs="EPSG:4326", scale=10.0),
                        digit=3)


def _latest_sa(ee):
    col = (ee.ImageCollection(catalogue.RADD_COLLECTION).filter(ee.Filter.eq("geography", "sa"))
           .filterMetadata("layer", "contains", "alert").sort("system:time_end", False))
    return ee.Image(col.first()).get("system:id").getInfo()


def test_a_custom_radd_image_equals_predefined_radd(gee, tiny_aoi):
    import ee

    aoi = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    custom = catalogue.build_custom_image(_src(_latest_sa(ee), "IMAGE"), *WIN, aoi)
    predefined = catalogue.build_image("RADD", *WIN, aoi, {})
    diff = custom.unmask(0).subtract(predefined.unmask(0)).abs().reduceRegion(
        ee.Reducer.sum(), tiny_aoi, 10, maxPixels=1e9).getInfo()
    assert diff == {"alert": 0, "date": 0}


def test_a_mixed_band_collection_is_filtered_without_failing(gee, tiny_aoi):
    import ee

    aoi = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    img = catalogue.build_custom_image(_src(catalogue.RADD_COLLECTION, "IMAGE_COLLECTION"), *WIN, aoi)
    assert img.bandNames().getInfo() == ["alert", "date"]
    img.reduceRegion(ee.Reducer.count(), tiny_aoi, 100).getInfo()  # evaluates: no "no bands" error


def test_ccdc_on_a_synthetic_image(gee, tiny_aoi):
    import ee

    aoi = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    src = _src("unused", "IMAGE", fmt="ccdc", encoding="decimal_year",
               alert="detection_count", dt="first_detection_date")
    start_of_feb_14 = 2024 + 44 / 366
    for count, code in ((3, 1), (1, 2)):
        img = ee.Image.constant([count, start_of_feb_14]).rename(["detection_count", "first_detection_date"])
        out = catalogue.preset_image(img, src, *WIN, aoi).reduceRegion(
            ee.Reducer.max(), tiny_aoi, 1000).getInfo()
        assert out == {"alert": code, "date": 2024045}


def _date_only(encoding):
    return _src("unused", "IMAGE", fmt="date_only", encoding=encoding, alert=None, dt="d")


def _evaluate(ee, tiny_aoi, src, value):
    aoi = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    img = ee.Image.constant(value).rename("d")
    return catalogue.preset_image(img, src, *WIN, aoi).reduceRegion(ee.Reducer.max(), tiny_aoi, 1000).getInfo()


# Every date-only encoding on Earth Engine, against the numpy twin (spec §13
# "the three presets, in Earth Engine expressions and in numpy"): None = no alert.
@pytest.mark.parametrize("encoding, value, expected", [
    ("yymm", 2401, 2024001),  # the month's first day
    ("yymm", 2403, 2024061),  # after 29 February 2024
    ("yymm", 2413, None),  # no month 13
    ("yyddd", 24366, 2024366),
    ("yyddd", 25366, None),  # 2025 has 365 days
    ("yyyyddd", 2025045, 2025045),
    ("decimal_year", 2024 + 44 / 366, 2024045),
])
def test_date_only_encodings_on_earth_engine_match_numpy(gee, tiny_aoi, encoding, value, expected):
    import ee

    assert int(presets.to_yyyyddd(np.array([value]), encoding)[0]) == (expected or 0)  # the numpy twin agrees
    out = _evaluate(ee, tiny_aoi, _date_only(encoding), value)
    # every dated pixel is a confirmed alert; an invalid date is masked: no alert
    assert out == ({"alert": None, "date": None} if expected is None else {"alert": 1, "date": expected})


@pytest.mark.parametrize("value, kept", [(2024001, True), (2025181, True), (2023365, False), (2025182, False)])
def test_the_window_keeps_both_endpoints_on_earth_engine(gee, tiny_aoi, value, kept):
    import ee

    out = _evaluate(ee, tiny_aoi, _date_only("yyyyddd"), value)  # WIN: 2024-01-01 .. 2025-06-30 (day 181)
    assert out == ({"alert": 1, "date": value} if kept else {"alert": None, "date": None})
