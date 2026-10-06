"""Loss drivers are stored as codes and shown in the current language (final
review B, M2): a project reviewed in Spanish and then in French kept labels of
both languages in its store, its exports and its report.

The pure functions stay here until Task 10 retires ``loss_drivers``; the
combobox tests moved to tests/test_analysis_questions_panel.py with the
control (Task 6)."""

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from gui.scripts import loss_drivers


@pytest.fixture
def locale():
    set_locale("en")
    yield set_locale
    set_locale("en")


@pytest.mark.parametrize("code", ["es-ES", "fr"])
def test_a_review_made_in_another_language_stores_codes(locale, code):
    """The panel half of this test (the combobox) moved to
    tests/test_analysis_questions_panel.py with the control itself (Task 6)."""
    locale(code)
    assert loss_drivers.label("other") != "Other"  # really translated
    assert loss_drivers.selection("other") == [{"text": loss_drivers.label("other"), "value": "other"}]
    assert loss_drivers.encode(loss_drivers.selection("other")) == "other"
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


def test_selection_is_items_and_free_text(locale):
    locale("fr")
    assert loss_drivers.selection("Logging, Roads") == [{"text": "Exploitation forestière", "value": "logging"}, "Roads"]
    assert loss_drivers.selection(None) == []
