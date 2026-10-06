"""Loss drivers are stored as codes and shown in the current language (final
review B, M2): a project reviewed in Spanish and then in French kept labels of
both languages in its store, its exports and its report."""

from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from gui.scripts import loss_drivers
from gui.tile.analysis.review_panel import build_review_panel
from gui.tile.analysis.state import AnalysisState


@pytest.fixture
def locale():
    set_locale("en")
    yield set_locale
    set_locale("en")


@pytest.mark.parametrize("code", ["es-ES", "fr"])
def test_a_review_made_in_another_language_stores_codes(locale, code):
    locale(code)
    widgets = SimpleNamespace()
    analysis = AnalysisState()
    build_review_panel(widgets, None, analysis, None)
    combobox = widgets.driver_combobox
    assert combobox.return_object  # Vuetify 2 draws a chip from the value itself
    assert combobox.v_model == []  # spec U13: no default driver
    # the chips and the items read in this language, the values are codes
    analysis.answers.set({"loss_driver": ["other"]})
    assert combobox.v_model == [{"text": loss_drivers.label("other"), "value": "other"}]
    assert loss_drivers.label("other") != "Other"  # really translated
    assert loss_drivers.encode(combobox.v_model) == "other"
    values = [item["value"] for item in combobox.items]
    assert values == [c for c, _ in loss_drivers.DRIVERS]
    assert [item["text"] for item in combobox.items] == [loss_drivers.label(c) for c, _ in loss_drivers.DRIVERS]
    assert loss_drivers.encode(["logging", "mining", "other"]) == "logging, mining, other"


@pytest.mark.parametrize("stored, codes", [
    ("Logging, Mining and Other", ["logging", "mining", "other"]),  # English, the old join
    ("Tala and Minería", ["logging", "mining"]),  # Spanish labels, the old join
    ("Bûcherie", ["logging"]),  # French, a label since corrected
    ("Exploitation forestière and Minage", ["logging", "mining"]),  # current and retired French
    ("expansión agricola, Feux de forêt and Other", ["agriculture_expansion", "forest_fires", "other"]),
    ("logging, mining", ["logging", "mining"]),  # already codes
    ("Roads and dams", ["Roads and dams"]),  # free text keeps its "and"
    ("", []),
    (None, []),
    (float("nan"), []),
])
def test_stored_descriptions_read_back_to_codes(stored, codes):
    assert loss_drivers.codes(stored) == codes


def test_free_text_is_stored_as_typed():
    assert loss_drivers.encode(["Roads", {"text": "Mining", "value": "mining"}, "Mining"]) == "Roads, mining"
    assert loss_drivers.encode("Tala") == "logging"
    assert loss_drivers.encode(None) == "" and loss_drivers.encode([]) == ""


def test_an_export_shows_the_drivers_in_its_language(locale):
    gdf = gpd.GeoDataFrame({"description": ["logging, mining", "Logging and Other", None, "Roads"]},
                           geometry=[box(0, 0, 1, 1)] * 4, crs="EPSG:4326")
    locale("es-ES")
    out = loss_drivers.with_labels(gdf)
    got = out["description"].tolist()
    assert got[:2] + got[3:] == ["Tala, Minería", "Tala, Otra", "Roads"] and pd.isna(got[2])
    assert gdf["description"].tolist()[0] == "logging, mining"  # the store's frame is untouched
    locale("fr")
    assert loss_drivers.label("logging, other") == "Exploitation forestière, Autre"


@pytest.mark.parametrize("code, chips", [
    ("en", ["Logging", "Roads"]),
    ("es-ES", ["Tala", "Roads"]),
    ("fr", ["Exploitation forestière", "Roads"]),
])
def test_a_stored_review_loads_back_as_chips_in_this_language(tmp_path, monkeypatch, locale, code, chips):
    """The chips show labels, never codes (final re-review, Important 1): a
    reviewed alert's drivers come back as items, free text as typed, and a
    re-save stores the same codes."""
    from tests.test_analysis_alert_info import _alerts, _state, _wire

    locale(code)
    s = _state(tmp_path, monkeypatch)
    analysis = AnalysisState()
    widgets = _wire(s, analysis)
    build_review_panel(widgets, None, analysis, None)
    gdf = _alerts(2)
    gdf["status"] = ["Confirmed", "Not reviewed"]  # only a reviewed alert opens on its answers
    gdf["description"] = ["logging, Roads", None]
    s.alerts_gdf.set(gdf)  # alert 0 is selected and drawn

    selected = widgets.driver_combobox.v_model
    assert [c["text"] if isinstance(c, dict) else c for c in selected] == chips
    assert loss_drivers.encode(selected) == "logging, Roads"  # a re-save keeps the codes
    typed = selected + ["Dams"]  # the user types free text next to the chips
    assert loss_drivers.encode(typed) == "logging, Roads, Dams"


def test_selection_is_items_and_free_text(locale):
    locale("fr")
    assert loss_drivers.selection("Logging, Roads") == [{"text": "Exploitation forestière", "value": "logging"}, "Roads"]
    assert loss_drivers.selection(None) == []


def test_an_unreviewed_alert_shows_no_driver_not_the_last_ones(tmp_path, monkeypatch, locale):
    """Moving from a reviewed alert to an unreviewed one must not carry the
    first alert's drivers over: a Save there would store them as its own.
    Nor does it start on a default driver (spec U13: an unreviewed alert opens
    on no answers, so "required" means something)."""
    from tests.test_analysis_alert_info import _alerts, _state, _wire

    s = _state(tmp_path, monkeypatch)
    analysis = AnalysisState()
    widgets = _wire(s, analysis)
    build_review_panel(widgets, None, analysis, None)
    gdf = _alerts(2)
    gdf["status"] = ["Confirmed", "Not reviewed"]
    gdf["description"] = ["logging, Roads", None]
    s.alerts_gdf.set(gdf)  # alert 0, reviewed
    assert loss_drivers.encode(widgets.driver_combobox.v_model) == "logging, Roads"
    s.actual_alert_id.value = 1
    assert widgets.driver_combobox.v_model == []  # U13
    assert analysis.answers.value == {}
