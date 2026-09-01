"""Characterization tests for deforestation_alerts/scripts/recipe_helper.py I/O.

The recipe JSON lives in the user's SEPAL workspace, so the two functions that
touch it are driven through ``Workspace``. ``load_gdf_from_csv`` stays a local
reader on purpose: its caller (``load_alert_store``) stages the remote CSV with
``download_to_scratch`` first, because pandas only opens local files.
"""

from shapely.geometry import Point

from deforestation_alerts.scripts.recipe_helper import (
    load_gdf_from_csv,
    update_saved_dictionary,
)


def test_load_gdf_from_csv_decodes_wkt_geometry(sample_wkt_csv):
    gdf = load_gdf_from_csv(str(sample_wkt_csv), ["point"])
    # WKT string decoded into a shapely Point
    first = gdf["point"].iloc[0]
    assert isinstance(first, Point)
    assert (first.x, first.y) == (1.0, 2.0)
    # empty geometry cell decodes to None (not an error)
    assert gdf["point"].iloc[1] is None
    # non-geometry column is left untouched
    assert list(gdf["id"]) == [0, 1]


def test_update_saved_dictionary_overwrites_and_adds_keys(ws):
    path = ws.results_dir("recipe_test", "recipe_parameters.json")
    ws.write_json(path, {"actual_alert_id": 0})

    update_saved_dictionary(ws, path, "actual_alert_id", 5)
    update_saved_dictionary(ws, path, "recipe_name", "recipe_test")

    data = ws.read_json(path)
    assert data["actual_alert_id"] == 5          # overwritten
    assert data["recipe_name"] == "recipe_test"  # added


def test_update_saved_dictionary_survives_a_missing_recipe(ws):
    """The old code swallowed FileNotFoundError so a per-alert id write could
    never break navigation; the adapter's equivalent is pysepal_api.NotFound."""
    update_saved_dictionary(
        ws, ws.results_dir("gone", "recipe_parameters.json"), "actual_alert_id", 1
    )  # must not raise


from deforestation_alerts.model.aux_model import AuxModel
from deforestation_alerts.model.alerts_model import SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.scripts.recipe_helper import save_model_parameters_to_json


class _AoiDateStub:
    """Stands in for AoiDateModel so the test never touches Earth Engine."""

    def export_dictionary(self):
        return {"aoi_method": "ADMIN0", "aoi_admin": ["CIV"]}


def test_save_model_parameters_merges_all_dicts(ws):
    app = AppTileModel()
    app.recipe_name = "recipe_test"
    out = ws.results_dir("recipe_test", "recipe_parameters.json")

    selected = SelectedAlertsModel()
    selected.min_area = 1.5  # Float(None) default raises on export under installed traitlets

    path = save_model_parameters_to_json(
        ws,
        out,
        AuxModel(),          # aux_model        -> contributes ccdc_layer, ...
        _AoiDateStub(),      # aoi_date_model   -> contributes aoi_method, aoi_admin
        None,                # alert_filter_model -> intentionally unused
        selected,            # selected_alerts_model
        AlertAnalysisModel(),   # analyzed_alerts_model
        app,                 # app_tile_model   -> contributes recipe_name, ...
    )

    assert path == out
    data = ws.read_json(path)
    # one representative key from each contributing model
    assert "ccdc_layer" in data                # AuxModel
    assert data["aoi_method"] == "ADMIN0"      # AoiDate stub
    assert "selected_alert_sources" in data    # SelectedAlertsModel
    assert data["actual_alert_id"] == 0        # AlertAnalysisModel (clamped)
    assert data["recipe_name"] == "recipe_test"  # AppTileModel


def test_save_model_parameters_is_valid_utf8_json_in_the_workspace(ws):
    """The recipe name is user-supplied free text; it must survive the round
    trip through the adapter (which encodes/decodes UTF-8 in both directions).

    Read back with ``read_json`` -- the call the production load path makes --
    so the assertion covers the decode that actually ships, not a friendlier
    neighbouring one."""
    app = AppTileModel()
    app.recipe_name = "Canindeyú_2026"
    selected = SelectedAlertsModel()
    selected.min_area = 1.5
    out = ws.results_dir("Canindeyú_2026", "recipe_parameters.json")

    save_model_parameters_to_json(
        ws, out, AuxModel(), _AoiDateStub(), None, selected, AlertAnalysisModel(), app
    )

    assert ws.read_json(out)["recipe_name"] == "Canindeyú_2026"
