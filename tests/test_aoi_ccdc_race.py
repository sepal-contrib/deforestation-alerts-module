"""Task 8 review fix (Important 1 / ruling R20).

The ccdc traitlets observer defers its GEE round-trip
(``get_alert_dates_ccdc``, two blocking ``.getInfo()`` calls) to a background
thread via ``spawn_in_context``, so two quick asset changes race two
independent threads. Without a token guard, a slower EARLIER round-trip can
complete after a faster LATER one and silently overwrite the later (correct)
dictionary entry with a stale one.

This drives ``_update_dictionary_ccdc`` directly — a plain module function, no
Solara render involved, same style as ``test_recipe_tile.py`` /
``test_overview_render.py`` — to prove the generation-token guard (mirroring
``AoiLayerRequest``/``_apply_aoi_layer`` in the same file) actually discards
the superseded write rather than merely being present.

No GEE, no browser.
"""

from types import SimpleNamespace

from gui.tile import aoi_tile


def test_ccdc_stale_write_is_discarded_by_token_guard(monkeypatch):
    dates_by_layer = {
        "layer-a": ("2020-01-01", "2020-12-31"),
        "layer-b": ("2021-01-01", "2021-12-31"),
    }
    monkeypatch.setattr(
        aoi_tile, "get_alert_dates_ccdc", lambda layer: dates_by_layer[layer]
    )

    alert_filter_model = SimpleNamespace(alerts_dictionary={})
    generation = {"value": 0}

    # Trigger 1: user picks layer-a. _register_ccdc_observer's handler bumps
    # the token and captures the request before any network work starts.
    generation["value"] += 1
    request_a = aoi_tile.CcdcUpdateRequest(
        ccdc_layer="layer-a", token=generation["value"]
    )

    # Trigger 2 supersedes it: the user immediately corrects to layer-b, before
    # request_a's round-trip has returned.
    generation["value"] += 1
    request_b = aoi_tile.CcdcUpdateRequest(
        ccdc_layer="layer-b", token=generation["value"]
    )

    # The LATER, faster round-trip (layer-b) completes first and writes its
    # correct entry.
    aoi_tile._update_dictionary_ccdc(alert_filter_model, request_b, generation)
    assert alert_filter_model.alerts_dictionary["CCDC"]["collection"] == "layer-b"

    # The EARLIER, slower round-trip (layer-a) completes last. Its token (1)
    # no longer matches generation["value"] (2), so the guard must discard
    # this write instead of stomping layer-b's correct entry with stale
    # layer-a dates.
    aoi_tile._update_dictionary_ccdc(alert_filter_model, request_a, generation)
    assert alert_filter_model.alerts_dictionary["CCDC"] == {
        "collection": "layer-b",
        "start_date": "2021-01-01",
        "end_date": "2021-12-31",
    }


def test_ccdc_write_is_applied_when_not_superseded(monkeypatch):
    """Sanity counterpart: an in-order, non-superseded call still writes."""
    monkeypatch.setattr(
        aoi_tile, "get_alert_dates_ccdc", lambda layer: ("2022-01-01", "2022-06-01")
    )
    alert_filter_model = SimpleNamespace(alerts_dictionary={})
    generation = {"value": 0}
    generation["value"] += 1
    request = aoi_tile.CcdcUpdateRequest(
        ccdc_layer="layer-c", token=generation["value"]
    )

    aoi_tile._update_dictionary_ccdc(alert_filter_model, request, generation)

    assert alert_filter_model.alerts_dictionary["CCDC"] == {
        "collection": "layer-c",
        "start_date": "2022-01-01",
        "end_date": "2022-06-01",
    }


def test_ccdc_stale_remove_is_also_discarded_by_token_guard(monkeypatch):
    """The remove branch is a write too (mutates the shared dict) and gets the
    same guard: a stale "clear" arriving after a newer "re-add" must not drop
    the newer entry.
    """
    monkeypatch.setattr(
        aoi_tile, "get_alert_dates_ccdc", lambda layer: ("2023-01-01", "2023-06-01")
    )
    alert_filter_model = SimpleNamespace(alerts_dictionary={})
    generation = {"value": 0}

    # Trigger 1: clear CCDC.
    generation["value"] += 1
    clear_request = aoi_tile.CcdcUpdateRequest(ccdc_layer=None, token=generation["value"])

    # Trigger 2 supersedes it: the user immediately re-picks a layer.
    generation["value"] += 1
    add_request = aoi_tile.CcdcUpdateRequest(
        ccdc_layer="layer-d", token=generation["value"]
    )

    # The later add completes first (writes the entry the "CCDC" in dictionary
    # check in the stale clear branch will see).
    aoi_tile._update_dictionary_ccdc(alert_filter_model, add_request, generation)
    assert "CCDC" in alert_filter_model.alerts_dictionary

    # The stale clear arrives last; its token no longer matches, so it must be
    # a no-op rather than removing the freshly re-added entry.
    aoi_tile._update_dictionary_ccdc(alert_filter_model, clear_request, generation)
    assert alert_filter_model.alerts_dictionary["CCDC"]["collection"] == "layer-d"
