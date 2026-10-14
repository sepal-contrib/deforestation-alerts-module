"""GeoParquet alert store, written into the data directory through a unique
``.part`` renamed into place, and read back where it is.
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet


def _sample_gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0], "status": ["Not reviewed"], "alert_polygon": [None]},
        geometry=gpd.GeoSeries([poly], crs="EPSG:4326"),
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_parquet_handles_empty_alert_polygon_multirow(data_root):
    """Regression: convert_to_geopandas sets ``alert_polygon = pd.Series([], object)``,
    which reindexes to NaN floats on a multi-row frame. The writer must coerce those
    to missing geometry instead of crashing on the float dtype."""
    polys = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
    ]
    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0, 2.0]}, geometry=gpd.GeoSeries(polys, crs="EPSG:4326")
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    gdf["alert_polygon"] = pd.Series([], dtype="object")  # all-NaN on 2 rows
    path = str(data_root / "recipe_test" / "alert_db.parquet")
    save_gdf_to_parquet(gdf, path)
    back = load_gdf_from_parquet(path)
    assert len(back) == 2
    assert back["alert_polygon"].iloc[0] is None
    assert back["alert_polygon"].iloc[1] is None
    assert back["point"].iloc[1].geom_type == "Point"


def test_parquet_roundtrip_preserves_all_geometry_columns(data_root):
    path = str(data_root / "recipe_test" / "alert_db.parquet")
    save_gdf_to_parquet(_sample_gdf(), path)
    back = load_gdf_from_parquet(path)
    assert back.geometry.name == "geometry"
    assert back.geometry.iloc[0].geom_type == "Polygon"
    assert back["point"].iloc[0].geom_type == "Point"
    assert back["bounding_box"].iloc[0].geom_type == "Polygon"
    assert back["area_ha"].iloc[0] == 1.0
    assert back["alert_polygon"].iloc[0] is None


def test_save_returns_the_path_and_the_store_lands_there(data_root):
    path = str(data_root / "recipe_test" / "alert_db.parquet")
    assert save_gdf_to_parquet(_sample_gdf(), path) == path
    assert Path(path).exists()


def test_save_load_roundtrip_leaves_no_part_file(data_root, data_files):
    """The store is written through a ``.part`` beside it: nothing but the
    store is left in its folder."""
    path = str(data_root / "recipe_test" / "alert_db.parquet")
    save_gdf_to_parquet(_sample_gdf(), path)
    load_gdf_from_parquet(path)

    assert not list((data_root / "recipe_test").glob("*.part"))
    assert data_files(data_root) == [Path(path)]
