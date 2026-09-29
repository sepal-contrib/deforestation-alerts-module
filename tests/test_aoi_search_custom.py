"""The AOI search registers custom sources from its click-time snapshot.

Importing gui.tile.aoi_tile runs aoi_helper's init_ee(); see the plan's Earth
Engine precondition. All EE work is monkeypatched out.
"""

from datetime import date

import pytest

from gui.tile import aoi_tile

SOURCES = (
    {"name": "S1", "asset": "users/x/s1"},
    {"name": "Planet", "asset": "users/x/planet"},
)


@pytest.fixture
def captured(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        aoi_tile, "check_aoi_inputs", lambda shim: ("fc", "2020-01-01", "2021-01-01")
    )
    monkeypatch.setattr(
        aoi_tile, "create_available_alert_dictionary", lambda d, *a: list(d)
    )

    def fake_rasters(available, aoi, d1, d2, ccdc, full_dictionary=None):
        seen["full"] = full_dictionary
        return {n: {"alert_raster": n} for n in available}

    monkeypatch.setattr(
        aoi_tile, "create_filtered_alert_raster_dictionary", fake_rasters
    )
    monkeypatch.setattr(
        aoi_tile,
        "create_basic_alerts_dictionary",
        lambda: {"RADD": {"collection": "r"}},
    )
    return seen


def _request(dictionary, custom=SOURCES):
    return aoi_tile.SearchRequest(
        feature_collection="fc",
        start_date="2020-01-01",
        end_date="2021-01-01",
        alerts_dictionary=dictionary,
        ccdc_layer=None,
        custom_alerts=custom,
    )


def test_init_shaped_dictionary_gets_the_snapshot(captured):
    # The init task writes built-ins only; the search must still add customs.
    init_dict = {"RADD": {"collection": "r"}}
    outcome = aoi_tile._run_search_sync(_request(init_dict))
    full = captured["full"]
    assert full["S1"]["slot"] == 0 and full["Planet"]["slot"] == 1
    assert full["S1"]["end_date"] == date.today().strftime("%Y-%m-%d")
    assert init_dict == {"RADD": {"collection": "r"}}  # input not mutated
    assert set(outcome.available_alerts_list) == {"RADD", "S1", "Planet"}
    assert outcome.dictionary_rebuilt is False


def test_rebuilt_dictionary_gets_the_snapshot(captured):
    outcome = aoi_tile._run_search_sync(_request(None))
    assert outcome.dictionary_rebuilt is True
    assert {"RADD", "S1", "Planet"} <= set(outcome.alerts_dictionary)


def test_stale_custom_entries_are_replaced(captured):
    stale = {
        "RADD": {"collection": "r"},
        "Old": {"collection": "users/x/old", "kind": "CUSTOM", "slot": 0},
    }
    aoi_tile._run_search_sync(_request(stale, custom=(SOURCES[1],)))
    full = captured["full"]
    assert "Old" not in full
    assert full["Planet"]["slot"] == 0


def test_request_defaults_to_no_custom_sources():
    req = aoi_tile.SearchRequest(
        feature_collection="fc",
        start_date="a",
        end_date="b",
        alerts_dictionary={},
        ccdc_layer=None,
    )
    assert req.custom_alerts == ()


def test_custom_observer_is_gone():
    assert not hasattr(aoi_tile, "_update_dictionary_custom")
