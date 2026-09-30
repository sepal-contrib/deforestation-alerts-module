"""Tier 2: the builders against real Earth Engine. Skips without credentials."""

from datetime import date, timedelta

import pytest

from deforestation_alerts.engine import catalogue as cat

# The first quarter of 2024 (GLAD-L's 2024 lives in 2023final) and a window
# that crosses New Year into the years GLAD-L's UpdResult holds.
WINDOWS = [(date(2024, 1, 1), date(2024, 3, 31)), (date(2025, 11, 3), date(2026, 2, 28))]


@pytest.mark.parametrize("start,end", WINDOWS, ids=["2024Q1", "across-new-year"])
@pytest.mark.parametrize("key", cat.KEYS)
def test_builder_contract(gee, tiny_aoi, key, start, end):
    meta = cat.meta_request(key).getInfo()
    assert cat.covers(key, meta, start, end)
    img = cat.build_image(key, start, end, tiny_aoi, meta)
    info = img.getInfo()
    assert [b["id"] for b in info["bands"]] == ["alert", "date"]
    assert all(b["data_type"]["precision"] == "int" for b in info["bands"])
    stats = img.reduceRegion("minMax", tiny_aoi, scale=30, maxPixels=1e9).getInfo()
    if stats.get("alert_min") is not None:
        assert stats["alert_min"] >= 1 and stats["alert_max"] <= 2
        assert stats["date_min"] >= cat.yyyyddd(start) and stats["date_max"] <= cat.yyyyddd(end)


@pytest.mark.parametrize("key", cat.KEYS)
def test_window_keeps_both_end_days(gee, tiny_aoi, key):
    import ee

    meta = cat.meta_request(key).getInfo()

    def date_stats(start, end):
        img = cat.build_image(key, start, end, tiny_aoi, meta).select("date")
        reducer = ee.Reducer.minMax().combine(ee.Reducer.count(), sharedInputs=True)
        return img.reduceRegion(reducer, tiny_aoi, scale=30, maxPixels=1e9).getInfo()

    wide = date_stats(*WINDOWS[0])
    assert wide["date_count"], f"no {key} alert in tiny_aoi"
    first, last = (date(v // 1000, 1, 1) + timedelta(days=v % 1000 - 1) for v in (wide["date_min"], wide["date_max"]))
    # a window that starts on the first alert's day and ends on the last one's keeps both
    assert date_stats(first, last) == wide


def test_glad_l_window_across_new_year_reads_both_years(gee, tiny_aoi):
    start, end = WINDOWS[1]
    meta = cat.meta_request("GLAD-L").getInfo()
    img = cat.build_image("GLAD-L", start, end, tiny_aoi, meta)
    stats = img.reduceRegion("minMax", tiny_aoi, scale=30, maxPixels=1e9).getInfo()
    # tiny_aoi holds GLAD-L alerts on both sides of 2026-01-01 (50 px when checked, 2026-09-30)
    assert cat.yyyyddd(start) <= stats["date_min"] < cat.yyyyddd(date(2026, 1, 1)) <= stats["date_max"]


def test_glad_l_window_no_collection_holds_is_empty(gee, tiny_aoi):
    start, end = date(2024, 8, 1), date(2024, 8, 31)
    meta = cat.meta_request("GLAD-L").getInfo()
    assert not cat.covers("GLAD-L", meta, start, end)
    img = cat.build_image("GLAD-L", start, end, tiny_aoi, meta)
    info = img.getInfo()
    assert [b["id"] for b in info["bands"]] == ["alert", "date"]
    assert all(b["data_type"]["precision"] == "int" for b in info["bands"])
    assert img.reduceRegion("count", tiny_aoi, scale=30).getInfo() == {"alert": 0, "date": 0}


def test_glad_s2_dates_are_the_calendar_days_of_its_day_counts(gee):
    import ee

    start, end = WINDOWS[1]
    # near Porto Velho: GLAD-S2 alerts on both sides of 2026-01-01 (271 px when checked, 2026-09-30)
    region = ee.Geometry.Rectangle([-63.60, -9.10, -63.50, -9.00])
    meta = cat.meta_request("GLAD-S2").getInfo()
    img = cat.build_image("GLAD-S2", start, end, region, meta)
    days = ee.Image(cat.GLAD_S2 + "/alertDate").rename("days")
    pixels = (
        img.select("date").addBands(days).updateMask(img.select("date").mask())
        .reduceRegion(ee.Reducer.toList(), region, scale=10, maxPixels=1e9).getInfo()
    )
    assert pixels["date"], "no GLAD-S2 alert in the region"
    assert len(pixels["date"]) == len(pixels["days"])  # one shared mask: the lists align
    for got, count in zip(pixels["date"], pixels["days"]):
        assert got == cat.yyyyddd(cat.GLAD_S2_ORIGIN + timedelta(days=count)), count
    assert {d // 1000 for d in pixels["date"]} == {2025, 2026}
