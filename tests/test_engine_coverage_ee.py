from datetime import date

import pytest

from deforestation_alerts.engine import coverage as cv


def _fc(ee, rect):
    return ee.FeatureCollection([ee.Feature(ee.Geometry.Rectangle(rect))])


def test_coverage_shares_and_first_days(gee):
    import ee

    canindeyu = _fc(ee, [-55.5, -24.5, -55.4, -24.4])
    amazon = _fc(ee, [-55.10, -10.10, -55.00, -10.00])
    spain = ee.FeatureCollection("FAO/GAUL/2015/level0").filter(ee.Filter.eq("ADM0_NAME", "Spain"))
    raw_c = ee.data.computeValue(cv.coverage_request(canindeyu, ("GLAD-L", "RADD", "GLAD-S2")))
    raw_a = ee.data.computeValue(cv.coverage_request(amazon, ("GLAD-L", "RADD")))
    raw_s = ee.data.computeValue(cv.coverage_request(spain, ("GLAD-L",)))
    c = cv.parse_coverage(raw_c, aoi_digest="c", today=date.today())
    a = cv.parse_coverage(raw_a, aoi_digest="a", today=date.today())
    s = cv.parse_coverage(raw_s, aoi_digest="s", today=date.today())
    assert c["GLAD-S2"].fraction == 0.0
    assert a["GLAD-L"].fraction == pytest.approx(1.0, abs=0.01)
    assert a["RADD"].regions[0].first == date(2020, 1, 1)
    assert 0.005 < s["GLAD-L"].fraction < 0.05  # the Canary Islands only (about 1.5%)
    assert a["GLAD-L"].gaps and a["GLAD-L"].gaps[0][0] == date(2024, 7, 17)
