"""Characterization tests for model export/import dictionaries (GEE-free models)."""

from component.model.app_model import AppTileModel
from component.model.aux_model import AuxModel
from component.model.alerts_model import SelectedAlertsModel
from component.model.analysis_model import AlertAnalysisModel


def test_app_tile_model_roundtrip():
    m = AppTileModel()
    m.recipe_name = "recipe_test"
    m.recipe_folder_path = "/tmp/recipe_test"
    m.temporary_recipe_name = "tmp"
    exported = m.export_dictionary()
    assert exported["recipe_name"] == "recipe_test"
    assert set(exported) == {
        "current_page_view", "temporary_recipe_name",
        "recipe_name", "recipe_folder_path",
    }

    loaded = AppTileModel()
    loaded.import_from_dictionary(exported)
    assert loaded.recipe_name == "recipe_test"
    assert loaded.recipe_folder_path == "/tmp/recipe_test"
    assert loaded.temporary_recipe_name == "tmp"


def test_aux_model_roundtrip():
    m = AuxModel()
    m.aux_layer = "projects/x/aux"
    m.aux_layer_vis = {"palette": ["#000000"]}
    exported = m.export_dictionary()
    assert set(exported) == {
        "ccdc_layer", "mask_layer", "aux_layer", "aux_layer_vis",
        "custom_report_template",
    }

    loaded = AuxModel()
    loaded.import_from_dictionary(exported)
    assert loaded.aux_layer == "projects/x/aux"
    assert loaded.aux_layer_vis == {"palette": ["#000000"]}


def test_selected_alerts_model_export_keys_and_numeric_codes():
    m = SelectedAlertsModel()
    # CHARACTERIZATION QUIRK: min_area is declared as Float(None) but the
    # installed traitlets version rejects None for a Float trait, so accessing
    # it at its default raises TraitError. export_dictionary() reads min_area,
    # so it only succeeds once min_area has been assigned a real float (the
    # normal app flow sets it via the UI). Pin that observed behavior here.
    m.min_area = 1.5
    m.alert_selection_area_n = 2
    m.alert_sorting_method_n = 1
    exported = m.export_dictionary()
    # the export deliberately stores the *numeric* (language-independent) codes
    assert exported["alert_selection_area"] == 2
    assert exported["alert_sorting_method"] == 1
    assert set(exported) == {
        "selected_alert_sources", "alert_selection_polygons", "min_area",
        "max_number_alerts", "alert_selection_area", "alert_sorting_method",
    }


def test_analysis_model_export_defaults_and_id_clamp():
    m = AlertAnalysisModel()
    # default actual_alert_id is -1; export clamps it to 0
    exported = m.export_dictionary()
    assert exported["actual_alert_id"] == 0
    assert exported["max_alert_id"] == 0
    assert exported["last_save_time"] == 0.0

    # import restores actual_alert_id and max_alert_id verbatim
    m.import_from_dictionary({"actual_alert_id": 7, "max_alert_id": 9,
                              "last_save_time": 123.0})
    assert m.actual_alert_id == 7
    assert m.max_alert_id == 9
    assert m.last_save_time == 123.0


def test_analysis_model_export_max_alert_id_mirrors_actual_alert_id():
    # CHARACTERIZATION QUIRK: export_dictionary derives "max_alert_id" from
    # actual_alert_id, NOT from max_alert_id (component/model/analysis_model.py).
    # We pin this so a later refactor must change it consciously.
    m = AlertAnalysisModel()
    m.actual_alert_id = 7
    m.max_alert_id = 9
    exported = m.export_dictionary()
    assert exported["actual_alert_id"] == 7
    assert exported["max_alert_id"] == 7  # mirrors actual_alert_id, NOT 9
