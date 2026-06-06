"""Characterization tests for component/scripts/recipe_helper.py pure I/O."""

import json
from pathlib import Path

from shapely.geometry import Point

from component.scripts.recipe_helper import (
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
