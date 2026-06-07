"""Tier-1: alerts_gdf CSV round-trip with polygon geometry + old-recipe backfill."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.recipe_helper import (
    save_gdf_to_csv,
    load_gdf_from_csv,
    backfill_geometry,
)

GEOM_COLS = ["geometry", "bounding_box", "point"]


def _alerts_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    gdf = gpd.GeoDataFrame(
        {"id": [0], "status": ["Not reviewed"], "geometry": [poly]},
        geometry="geometry", crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    return gdf


def test_polygon_roundtrips_through_csv(tmp_path):
    gdf = _alerts_gdf()
    csv = tmp_path / "alert_db.csv"
    save_gdf_to_csv(gdf, str(csv), GEOM_COLS)

    loaded = load_gdf_from_csv(str(csv), GEOM_COLS)
    assert loaded["geometry"].iloc[0].geom_type == "Polygon"
    assert loaded["geometry"].iloc[0].equals(gdf["geometry"].iloc[0])
    assert loaded["point"].iloc[0].geom_type == "Point"


def test_old_recipe_without_polygon_backfills_from_bbox(tmp_path):
    # Simulate a pre-Phase-3 recipe: only point + bounding_box stored.
    csv = tmp_path / "old_alert_db.csv"
    csv.write_text(
        "id,point,bounding_box\n"
        "0,POINT (0.5 0.5),\"POLYGON ((0 0, 0 1, 1 1, 1 0, 0 0))\"\n"
    )
    loaded = load_gdf_from_csv(str(csv), ["point", "bounding_box"])
    loaded = backfill_geometry(loaded)   # fills geometry from bounding_box
    assert loaded["geometry"].iloc[0].geom_type == "Polygon"
