"""GeoParquet alert store, written to and read from the SEPAL workspace.

pyarrow/GeoPandas only speak local paths, so the store is staged in
``Workspace.scratch_*`` and shipped through the adapter; these tests assert the
same round-trip guarantees as before the migration, now against the remote
store rather than a tmp_path.
"""

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


def test_parquet_handles_empty_alert_polygon_multirow(ws):
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
    path = ws.results_dir("recipe_test", "alert_db.parquet")
    save_gdf_to_parquet(gdf, ws, path)
    back = load_gdf_from_parquet(ws, path)
    assert len(back) == 2
    assert back["alert_polygon"].iloc[0] is None
    assert back["alert_polygon"].iloc[1] is None
    assert back["point"].iloc[1].geom_type == "Point"


def test_parquet_roundtrip_preserves_all_geometry_columns(ws):
    path = ws.results_dir("recipe_test", "alert_db.parquet")
    save_gdf_to_parquet(_sample_gdf(), ws, path)
    back = load_gdf_from_parquet(ws, path)
    assert back.geometry.name == "geometry"
    assert back.geometry.iloc[0].geom_type == "Polygon"
    assert back["point"].iloc[0].geom_type == "Point"
    assert back["bounding_box"].iloc[0].geom_type == "Polygon"
    assert back["area_ha"].iloc[0] == 1.0
    assert back["alert_polygon"].iloc[0] is None


def test_save_returns_the_remote_path_and_the_store_lands_in_the_workspace(ws):
    """The write goes to the user's SEPAL workspace, not the container disk:
    the staged local copy is scratch, and only the returned remote path is the
    store."""
    path = ws.results_dir("recipe_test", "alert_db.parquet")
    assert save_gdf_to_parquet(_sample_gdf(), ws, path) == path
    assert ws.exists(path)


def test_save_load_roundtrip_leaves_no_scratch_residue(ws, tmp_path):
    """The store is staged locally, but this runs once per alert save: a leftover
    copy of ``alert_db.parquet`` per save would fill the container's tmp for the
    life of the session."""
    path = ws.results_dir("recipe_test", "alert_db.parquet")
    save_gdf_to_parquet(_sample_gdf(), ws, path)
    load_gdf_from_parquet(ws, path)

    scratch = tmp_path / "scratch"
    residue = [p for p in scratch.rglob("*") if p.is_file()]
    assert residue == []
