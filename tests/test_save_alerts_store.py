"""The Analysis review save persists ``alert_db.parquet`` to the user's SEPAL
workspace through ``Workspace``, and skips when there is no set folder.

``recipe_folder_path`` holds a path built with ``Workspace.results_dir(...)``,
so the writer is handed the workspace alongside it. (Integrate writes a new
set's store itself: ``tests/test_engine_integrate.py``.)
"""

import types

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
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


def test_save_skips_without_recipe_folder(ws, data_files):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model, ws)  # must not raise
    # nothing was written anywhere in the user's workspace
    assert data_files(ws) == []
