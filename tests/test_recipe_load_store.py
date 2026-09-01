"""Alert-store round trip through the SEPAL workspace adapter.

Same semantics as before the migration (parquet round-trips every geometry
column; a legacy CSV-only recipe still loads) -- only the storage boundary
changed: the store now lives in the user's SEPAL workspace, reached through
``Workspace``, not on the container's disk.
"""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import (
    ALERT_GEOM_COLS,
    load_alert_store,
    save_gdf_to_csv,
    save_gdf_to_parquet,
)


def _gdf():
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    g = gpd.GeoDataFrame({"status": ["Not reviewed"]},
                         geometry=gpd.GeoSeries([poly], crs="EPSG:4326"))
    g["bounding_box"] = g.geometry.envelope
    g["point"] = g.geometry.centroid
    return g


def test_load_store_roundtrip_via_parquet(ws):
    folder = ws.results_dir("recipe_test")
    save_gdf_to_parquet(_gdf(), ws, folder + "/alert_db.parquet")
    out = load_alert_store(ws, folder)
    assert out["point"].iloc[0].geom_type == "Point"


def test_load_store_legacy_csv_only(ws):
    """Old recipes carry only ``alert_db.csv``. ``save_gdf_to_csv`` is still a
    local serializer (its remote staging belongs with the export path, Task 12),
    so the fixture stages the legacy file exactly the way the export will."""
    folder = ws.results_dir("recipe_test")
    local = ws.scratch_file(".csv")
    save_gdf_to_csv(_gdf(), local, ALERT_GEOM_COLS)
    ws.upload_from_scratch(local, folder + "/alert_db.csv")

    out = load_alert_store(ws, folder)
    assert out.geometry.iloc[0].geom_type == "Polygon"
