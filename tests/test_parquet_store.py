"""GeoParquet alert store, written to and read from the SEPAL workspace.

pyarrow/GeoPandas only speak local paths, so the store is staged in
``Workspace.scratch_*`` and shipped through the adapter; these tests assert the
same round-trip guarantees as before the migration, now against the remote
store rather than a tmp_path.
"""

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import (
    load_alert_store,
    load_gdf_from_parquet,
    save_gdf_to_csv,
    save_gdf_to_parquet,
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


def _stage_legacy_csv(ws, remote_path, gdf):
    """Put a legacy ``alert_db.csv`` in the workspace.

    ``save_gdf_to_csv`` stays a local serializer (its remote staging belongs to
    the export path, Task 12), so the upload is spelled out here.
    """
    local = ws.scratch_file(".csv")
    save_gdf_to_csv(gdf, local, _GEOM_COLS)
    return ws.upload_from_scratch(local, remote_path)


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


def test_load_alert_store_prefers_parquet(ws):
    folder = ws.results_dir("recipe_test")
    save_gdf_to_parquet(_sample_gdf(), ws, folder + "/alert_db.parquet")
    _stage_legacy_csv(ws, folder + "/alert_db.csv", _sample_gdf())
    gdf = load_alert_store(ws, folder)
    assert gdf["point"].iloc[0].geom_type == "Point"


def test_load_alert_store_falls_back_to_csv(ws):
    folder = ws.results_dir("recipe_test")
    _stage_legacy_csv(ws, folder + "/alert_db.csv", _sample_gdf())
    gdf = load_alert_store(ws, folder)
    assert gdf is not None
    assert gdf.geometry.iloc[0].geom_type == "Polygon"


def test_load_alert_store_returns_none_when_empty(ws):
    """A recipe folder with no store yet -- and, since ``ensure_dir`` was never
    called, a folder that does not exist remotely either. Both must answer None,
    not raise."""
    assert load_alert_store(ws, ws.results_dir("recipe_test")) is None
