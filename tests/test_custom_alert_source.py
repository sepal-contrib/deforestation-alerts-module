"""Custom alert sources in aoi_helper — slot-aware raster build.

aoi_helper runs init_ee() at import, so everything is imported inside the test
and depends on the ``gee`` fixture (skips without Earth Engine).
"""


def test_filtered_raster_dictionary_carries_custom_slot(gee, monkeypatch):
    from deforestation_alerts.scripts import aoi_helper

    calls = []

    def fake_get_alerts(collection, start, end, aoi, asset, slot=0):
        calls.append((collection, asset, slot))
        return f"img:{collection}:{asset}:{slot}"

    monkeypatch.setattr(aoi_helper, "get_alerts", fake_get_alerts)
    full = {"RADD": {"collection": "x"}}
    aoi_helper.set_custom_alerts_dictionary(
        full,
        [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}],
        "2015-01-01",
        "2026-09-30",
    )
    out = aoi_helper.create_filtered_alert_raster_dictionary(
        ["RADD", "B"], "aoi", "2020-01-01", "2021-01-01", None, full_dictionary=full
    )
    assert out["RADD"] == {"alert_raster": "img:RADD:None:0"}
    assert out["B"] == {"alert_raster": "img:CUSTOM:users/x/b:1", "custom_slot": 1}
    assert ("CUSTOM", "users/x/b", 1) in calls


def test_from_custom_writes_slot_codes_as_int32(gee, tiny_aoi, monkeypatch):
    import ee
    from deforestation_alerts.scripts import aoi_helper

    # A synthetic RADD-shaped image stands in for the asset: ee.Image(img)
    # returns img unchanged, and getAsset is stubbed to report an IMAGE.
    monkeypatch.setattr(aoi_helper.ee.data, "getAsset", lambda _id: {"type": "IMAGE"})
    confirmed = ee.Image.constant([3, 23001]).rename(["Alert", "Date"])
    img = aoi_helper._from_custom("2022-01-01", "2024-01-01", tiny_aoi, confirmed, slot=2)

    assert img.bandTypes().getInfo()["alert"]["precision"] == "int"
    stats = img.select("alert").reduceRegion(
        ee.Reducer.max(), tiny_aoi, 30
    ).getInfo()
    assert stats["alert"] == 10**6
