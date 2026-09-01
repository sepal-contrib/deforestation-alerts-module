"""The two call sites that persist ``alert_db.parquet`` write it to the user's
SEPAL workspace through ``Workspace``, and still skip when there is no recipe.

``recipe_folder_path`` now holds a REMOTE path (``Workspace.results_dir(...)``),
so both writers are handed the workspace alongside it.
"""

import types

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import load_gdf_from_parquet
from gui.tile.analysis.export import _save_alerts_to_gdf


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame({"status": ["Confirmed"]},
                           geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_save_writes_parquet(ws):
    folder = ws.results_dir("recipe_test")
    model = types.SimpleNamespace(recipe_folder_path=folder)

    _save_alerts_to_gdf(_gdf(), model, ws)

    parquet = folder + "/alert_db.parquet"
    assert ws.exists(parquet)
    back = load_gdf_from_parquet(ws, parquet)
    assert back["status"].iloc[0] == "Confirmed"


def test_save_skips_without_recipe_folder(ws):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model, ws)  # must not raise
    # nothing was uploaded anywhere in the user's workspace
    assert ws.client.files.files == {}


def test_filter_persist_writes_parquet(ws):
    """The analyze store write moved out of the task and into the completion
    effect (a cancelled run must leave no loadable alert_db.parquet); it must
    still write the same store when a recipe folder exists."""
    from gui.tile.alerts_filter_tile import _persist_alerts_gdf

    folder = ws.results_dir("recipe_test")
    _persist_alerts_gdf(_gdf(), folder, ws)

    back = load_gdf_from_parquet(ws, folder + "/alert_db.parquet")
    assert back["status"].iloc[0] == "Confirmed"


def test_filter_persist_skips_without_recipe_folder(ws):
    from gui.tile.alerts_filter_tile import _persist_alerts_gdf

    _persist_alerts_gdf(_gdf(), "", ws)  # must not raise
    assert ws.client.files.files == {}
