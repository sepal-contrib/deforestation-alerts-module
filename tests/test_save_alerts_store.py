import os
import types
import geopandas as gpd
from shapely.geometry import Polygon
from gui.tile.analysis.export import _save_alerts_to_gdf
from deforestation_alerts.scripts.recipe_helper import load_gdf_from_parquet


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame({"status": ["Confirmed"]},
                           geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_save_writes_parquet(tmp_path):
    model = types.SimpleNamespace(recipe_folder_path=str(tmp_path))
    _save_alerts_to_gdf(_gdf(), model)
    parquet = tmp_path / "alert_db.parquet"
    assert parquet.exists()
    back = load_gdf_from_parquet(parquet)
    assert back["status"].iloc[0] == "Confirmed"


def test_save_skips_without_recipe_folder(tmp_path):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model)  # must not raise
    assert not list(tmp_path.iterdir())
