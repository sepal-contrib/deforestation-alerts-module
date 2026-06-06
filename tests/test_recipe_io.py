"""Characterization tests for component/scripts/recipe_helper.py pure I/O."""

import json
from pathlib import Path

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


def test_update_saved_dictionary_overwrites_and_adds_keys(tmp_path):
    p = tmp_path / "recipe_parameters.json"
    p.write_text(json.dumps({"actual_alert_id": 0}))

    update_saved_dictionary(str(p), "actual_alert_id", 5)
    update_saved_dictionary(str(p), "recipe_name", "recipe_test")

    data = json.loads(p.read_text())
    assert data["actual_alert_id"] == 5          # overwritten
    assert data["recipe_name"] == "recipe_test"  # added


from deforestation_alerts.model.aux_model import AuxModel
from deforestation_alerts.model.alerts_model import SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.scripts.recipe_helper import save_model_parameters_to_json


class _AoiDateStub:
    """Stands in for AoiDateModel so the test never touches Earth Engine."""

    def export_dictionary(self):
        return {"aoi_method": "ADMIN0", "aoi_admin": ["CIV"]}


def test_save_model_parameters_merges_all_dicts(tmp_path):
    app = AppTileModel()
    app.recipe_name = "recipe_test"
    out = tmp_path / "recipe_parameters.json"

    selected = SelectedAlertsModel()
    selected.min_area = 1.5  # Float(None) default raises on export under installed traitlets

    path = save_model_parameters_to_json(
        str(out),
        AuxModel(),          # aux_model        -> contributes ccdc_layer, ...
        _AoiDateStub(),      # aoi_date_model   -> contributes aoi_method, aoi_admin
        None,                # alert_filter_model -> intentionally unused
        selected,            # selected_alerts_model
        AlertAnalysisModel(),   # analyzed_alerts_model
        app,                 # app_tile_model   -> contributes recipe_name, ...
    )

    data = json.loads(Path(path).read_text())
    # one representative key from each contributing model
    assert "ccdc_layer" in data                # AuxModel
    assert data["aoi_method"] == "ADMIN0"      # AoiDate stub
    assert "selected_alert_sources" in data    # SelectedAlertsModel
    assert data["actual_alert_id"] == 0        # AlertAnalysisModel (clamped)
    assert data["recipe_name"] == "recipe_test"  # AppTileModel
