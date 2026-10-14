"""The Analysis review save writes ``alert_db.parquet`` into the set folder,
and skips when there is no set folder. (Integrate writes a new set's store
itself: ``tests/test_engine_integrate.py``.)
"""

import types

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
from gui.tile.analysis.review_save import write_store as _save_alerts_to_gdf


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame({"status": ["Confirmed"]},
                           geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_save_writes_parquet(data_root):
    folder = data_root / "recipe_test"
    model = types.SimpleNamespace(recipe_folder_path=str(folder))

    _save_alerts_to_gdf(_gdf(), model)

    parquet = folder / "alert_db.parquet"
    assert parquet.exists()
    assert not list(folder.glob("*.part"))
    back = load_gdf_from_parquet(parquet)
    assert back["status"].iloc[0] == "Confirmed"


def test_save_skips_without_recipe_folder(data_root, data_files):
    model = types.SimpleNamespace(recipe_folder_path="")
    _save_alerts_to_gdf(_gdf(), model)  # must not raise
    # nothing was written anywhere in the data directory
    assert data_files(data_root) == []
