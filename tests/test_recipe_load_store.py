import geopandas as gpd
from shapely.geometry import Polygon
from deforestation_alerts.scripts.recipe_helper import (
    save_gdf_to_parquet, save_gdf_to_csv, load_alert_store, ALERT_GEOM_COLS,
)


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    g = gpd.GeoDataFrame({"status": ["Not reviewed"]},
                         geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    g["bounding_box"] = g.geometry.envelope
    g["point"] = g.geometry.centroid
    return g


def test_load_store_roundtrip_via_parquet(tmp_path):
    save_gdf_to_parquet(_gdf(), tmp_path / "alert_db.parquet")
    out = load_alert_store(str(tmp_path))
    assert out["point"].iloc[0].geom_type == "Point"


def test_load_store_legacy_csv_only(tmp_path):
    save_gdf_to_csv(_gdf(), tmp_path / "alert_db.csv", ALERT_GEOM_COLS)
    out = load_alert_store(str(tmp_path))
    assert out.geometry.iloc[0].geom_type == "Polygon"
