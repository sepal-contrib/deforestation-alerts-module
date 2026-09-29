"""Characterization tests for model export/import dictionaries (GEE-free models)."""

import pytest

from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.model.aux_model import AuxModel
from deforestation_alerts.model.alerts_model import SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel


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
    m.custom_alerts = [
        {"name": "MyAlerts", "asset": "projects/x/custom"},
        {"name": "Other", "asset": "projects/x/other"},
    ]
    m.attribute_raster = {
        "source": "projects/x/forest", "kind": "gee",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }
    exported = m.export_dictionary()
    assert set(exported) == {
        "ccdc_layer", "custom_alerts",
        "mask_layer", "aux_layer", "aux_layer_vis",
        "custom_report_template", "attribute_raster",
    }

    loaded = AuxModel()
    loaded.import_from_dictionary(exported)
    assert loaded.aux_layer == "projects/x/aux"
    assert loaded.aux_layer_vis == {"palette": ["#000000"]}
    assert loaded.custom_alerts == [
        {"name": "MyAlerts", "asset": "projects/x/custom"},
        {"name": "Other", "asset": "projects/x/other"},
    ]
    assert loaded.attribute_raster == {
        "source": "projects/x/forest", "kind": "gee",
        "bands": [{"band": "forest", "stat": "majority", "column": "forest"}],
    }


def test_aux_model_imports_legacy_single_custom_source():
    loaded = AuxModel()
    loaded.import_from_dictionary(
        {"custom_alert_layer": "projects/x/custom", "custom_alert_name": "MyAlerts"}
    )
    assert loaded.custom_alerts == [{"name": "MyAlerts", "asset": "projects/x/custom"}]


def test_aux_model_legacy_partial_or_reserved_is_ignored():
    loaded = AuxModel()
    loaded.import_from_dictionary({"custom_alert_layer": "projects/x/custom"})
    assert loaded.custom_alerts == []
    loaded.import_from_dictionary(
        {"custom_alert_layer": "projects/x/c", "custom_alert_name": "RADD"}
    )
    assert loaded.custom_alerts == []


def test_aux_model_import_without_custom_keys_keeps_current():
    m = AuxModel()
    m.custom_alerts = [{"name": "Keep", "asset": "a"}]
    m.import_from_dictionary({"mask_layer": "projects/x/mask"})
    assert m.custom_alerts == [{"name": "Keep", "asset": "a"}]


def test_aux_model_import_cleans_new_key():
    m = AuxModel()
    m.import_from_dictionary(
        {"custom_alerts": [{"name": "ccdc", "asset": "a"}, {"name": "Ok", "asset": "b"}]}
    )
    assert m.custom_alerts == [{"name": "Ok", "asset": "b"}]


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


def test_selected_alerts_model_import_roundtrips_numeric_codes():
    # import_from_dictionary (added for the Recipe load) is the inverse of
    # export: numeric codes go back into the *_n traits.
    src = SelectedAlertsModel()
    src.min_area = 2.5
    src.max_number_alerts = 10
    src.alert_selection_area_n = 2
    src.alert_sorting_method_n = 1
    exported = src.export_dictionary()

    dst = SelectedAlertsModel()
    dst.import_from_dictionary(exported)
    assert dst.min_area == 2.5
    assert dst.max_number_alerts == 10
    assert dst.alert_selection_area_n == 2
    assert dst.alert_sorting_method_n == 1


def test_selected_alerts_model_import_with_null_min_area_does_not_crash():
    # A saved recipe may carry min_area=null; importing it into a fresh model
    # must not crash (min_area's Float(None) default is unreadable under the
    # installed traitlets, so the importer guards on `key in data`/not-None).
    m = SelectedAlertsModel()
    m.import_from_dictionary(
        {
            "min_area": None,
            "alert_selection_area": 0,
            "alert_sorting_method": 0,
            "max_number_alerts": 0,
        }
    )
    assert m.alert_selection_area_n == 0
    assert m.max_number_alerts == 0


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


def test_app_tile_model_import_except_clause_has_json_imported():
    """Regression: import_from_dictionary's except clause references
    json.JSONDecodeError, but the module had no `import json`. When ANY exception
    fires in the try, Python evaluates the except tuple — without `import json`
    that raises NameError, masking the original error. Passing None makes
    `.get()` raise AttributeError; we assert that exact exception surfaces
    (proving the except tuple evaluated cleanly, i.e. json is importable)."""
    m = AppTileModel()
    with pytest.raises(AttributeError):
        m.import_from_dictionary(None)
    # A real dict still round-trips (sanity).
    m.import_from_dictionary({"recipe_name": "r"})
    assert m.recipe_name == "r"


# ---------------------------------------------------------------------------
# AoiDateModel: pre-existing model bugs fixed alongside the Recipe port.
# AoiDateModel() construction hits GEE (admin-code/folder lookup), so these are
# gated on the `gee` fixture (skipped offline) like the rest of the GEE tests.
# ---------------------------------------------------------------------------
def test_aoi_date_model_reset_clears_aoi_fields(gee):
    from deforestation_alerts.model.aoi_date_model import AoiDateModel

    m = AoiDateModel()
    m.admin = "CIV"
    m.name = "my aoi"
    m.method = "SHAPE"
    m.vector_json = {"pathname": "/x.shp", "column": "ALL", "value": None}
    m.geo_json = {"type": "FeatureCollection", "features": [{"a": 1}]}
    m.start_date = "2020-01-01"
    m.end_date = "2020-02-01"

    m.reset_model()

    # Before the fix, reset_model assigned to non-existent attributes
    # (self.aoi_admin, ...) so the real traits were never cleared.
    assert m.admin is None
    assert m.name is None
    assert m.start_date == ""
    assert m.end_date == ""
    # And it must clear the GEOMETRY traits too — otherwise creating a new
    # recipe after a SHAPE/DRAW session leaks stale geometry into the next save.
    assert not m.vector_json
    assert not m.geo_json


def test_aoi_date_model_import_roundtrips_aoi_vector_json(gee):
    from deforestation_alerts.model.aoi_date_model import AoiDateModel

    src = AoiDateModel()
    src.method = "SHAPE"
    src.vector_json = {"pathname": "/x.shp", "column": "ALL", "value": None}
    src.start_date = "2021-01-01"
    exported = src.export_dictionary()
    assert "aoi_vector_json" in exported

    # Import without re-setting method => no _from_* re-dispatch (no extra EE
    # call). Before the fix import read vector_json from the wrong "geo_json"
    # key, so aoi_vector_json never round-tripped.
    dst = AoiDateModel()
    dst.import_from_dictionary(
        {
            "aoi_vector_json": exported["aoi_vector_json"],
            "start_date": exported["start_date"],
        }
    )
    assert dst.vector_json == {"pathname": "/x.shp", "column": "ALL", "value": None}
    assert dst.start_date == "2021-01-01"


def test_drawn_aoi_stays_in_memory_and_never_exports_an_asset(gee):
    """A drawn AOI is used as an in-memory ee.FeatureCollection, like
    spatial_risk's Solara AoiView. pysepal's AoiModel would also export it as a
    GEE asset, checking the task list first -- a side effect the app never
    reads, and the call that broke drawing on ee-client 3.2.1."""
    from unittest.mock import MagicMock

    import ee

    from deforestation_alerts.model.aoi_date_model import AoiDateModel

    gee_interface = MagicMock()
    m = AoiDateModel(gee_interface=gee_interface)
    m.name = "drawn"
    m._from_geo_json(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"style": {}},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [0, 1], [1, 1], [1, 0], [0, 0]]],
                    },
                }
            ],
        }
    )

    assert isinstance(m.feature_collection, ee.FeatureCollection)
    gee_interface.is_running.assert_not_called()
    gee_interface.export_table_to_asset.assert_not_called()
    gee_interface.get_asset.assert_not_called()
