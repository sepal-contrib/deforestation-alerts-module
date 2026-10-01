"""Tier-1: the local-vectorize gdf -> alerts_gdf assembler (no GEE, no UI)."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_table import convert_to_geopandas


def _vectorized_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {
            "alert_type_unique": [[1], [110]],   # GLAD-L ; RADD+GLAD-S2
            "count": [4, 9],
            "alert_date_min": [2022.1, 2023.2],
            "alert_date_max": [2022.3, 2023.4],
            "area_ha": [0.5, 1.2],
            "geometry": [poly, poly],
        },
        geometry="geometry", crs="EPSG:4326",
    )


def test_convert_sets_polygon_geometry_and_backcompat_columns():
    gdf = convert_to_geopandas(_vectorized_gdf())
    assert gdf.geometry.iloc[0].geom_type == "Polygon"          # D2 active geom
    assert gdf["bounding_box"].iloc[0].geom_type in ("Polygon", "LineString")
    assert gdf["point"].iloc[0].geom_type == "Point"
    assert gdf["area_ha"].iloc[0] == 0.5


def test_convert_decodes_alert_sources():
    gdf = convert_to_geopandas(_vectorized_gdf())
    assert gdf["alert_sources"].iloc[0] == "GLAD-L"
    # 110 -> RADD (tens) + GLAD-S2 (hundreds); membership check (order-independent)
    parts = gdf["alert_sources"].iloc[1].replace(" and ", ", ").split(", ")
    assert set(parts) == {"RADD", "GLAD-S2"}


def _custom_coded_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {
            "alert_type_unique": [[10000], [10001]],  # custom only ; custom + GLAD-L
            "count": [4, 4],
            "alert_date_min": [2022.1, 2022.1],
            "alert_date_max": [2022.3, 2022.3],
            "area_ha": [0.5, 0.5],
            "geometry": [poly, poly],
        },
        geometry="geometry", crs="EPSG:4326",
    )


def test_convert_threads_custom_names_into_alert_sources():
    out = convert_to_geopandas(_custom_coded_gdf(), custom_names={0: "MyAlerts"})
    assert out["alert_sources"].iloc[0] == "MyAlerts"
    parts = out["alert_sources"].iloc[1].replace(" and ", ", ").split(", ")
    assert set(parts) == {"GLAD-L", "MyAlerts"}


def test_convert_adds_empty_review_columns():
    gdf = convert_to_geopandas(_vectorized_gdf())
    for col in ("status", "before_img", "after_img", "alert_polygon",
                "description", "admin1", "admin2", "admin3"):
        assert col in gdf.columns
    assert (gdf["status"] == "Not reviewed").all()
