import os
import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from deforestation_alerts.scripts.alert_export import export_alerts_gdf, EXPORT_FORMATS


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([poly], crs="EPSG:4326"),
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_export_gpkg_drops_secondary_geometries(tmp_path):
    path = export_alerts_gdf(_gdf(), str(tmp_path), "gpkg",
                             ["GLAD-L", "RADD"], "2026-01-01", "2026-06-12")
    assert os.path.basename(path) == "alerts_glad-l-radd_2026-01-01_2026-06-12.gpkg"
    back = gpd.read_file(path)
    assert "point" not in back.columns and "bounding_box" not in back.columns
    assert back.geometry.iloc[0].geom_type == "Polygon"


def test_export_geojson(tmp_path):
    path = export_alerts_gdf(_gdf(), str(tmp_path), "geojson",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert path.endswith("alerts_glad-l_2026-01-01_2026-06-12.geojson")
    assert os.path.exists(path)


def test_export_csv_keeps_columns_as_wkt(tmp_path):
    path = export_alerts_gdf(_gdf(), str(tmp_path), "csv",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    text = open(path).read()
    assert path.endswith(".csv")
    assert "POINT" in text  # point column preserved as WKT


def test_export_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        export_alerts_gdf(_gdf(), str(tmp_path), "shp", [], "", "")


def test_export_formats_constant():
    assert set(EXPORT_FORMATS) == {"csv", "gpkg", "geojson"}
