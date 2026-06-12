import geopandas as gpd
from shapely.geometry import Polygon
from deforestation_alerts.scripts.recipe_helper import (
    save_gdf_to_parquet,
    load_gdf_from_parquet,
    load_alert_store,
    save_gdf_to_csv,
)

_GEOM_COLS = ["geometry", "bounding_box", "point", "alert_polygon"]


def _sample_gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0], "status": ["Not reviewed"], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([poly], crs="EPSG:4326"),
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_parquet_roundtrip_preserves_all_geometry_columns(tmp_path):
    path = tmp_path / "alert_db.parquet"
    save_gdf_to_parquet(_sample_gdf(), path)
    back = load_gdf_from_parquet(path)
    assert back.geometry.name == "geometry"
    assert back.geometry.iloc[0].geom_type == "Polygon"
    assert back["point"].iloc[0].geom_type == "Point"
    assert back["bounding_box"].iloc[0].geom_type == "Polygon"
    assert back["area_ha"].iloc[0] == 1.0
    assert back["alert_polygon"].iloc[0] is None


def test_load_alert_store_prefers_parquet(tmp_path):
    save_gdf_to_parquet(_sample_gdf(), tmp_path / "alert_db.parquet")
    save_gdf_to_csv(_sample_gdf(), tmp_path / "alert_db.csv", _GEOM_COLS)
    gdf = load_alert_store(str(tmp_path))
    assert gdf["point"].iloc[0].geom_type == "Point"


def test_load_alert_store_falls_back_to_csv(tmp_path):
    save_gdf_to_csv(_sample_gdf(), tmp_path / "alert_db.csv", _GEOM_COLS)
    gdf = load_alert_store(str(tmp_path))
    assert gdf is not None
    assert gdf.geometry.iloc[0].geom_type == "Polygon"


def test_load_alert_store_returns_none_when_empty(tmp_path):
    assert load_alert_store(str(tmp_path)) is None
