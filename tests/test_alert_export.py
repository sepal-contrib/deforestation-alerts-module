"""The on-demand exports land in the USER's SEPAL workspace, not the container.

Every assertion reads the produced bytes back out of the fake client's
in-memory workspace (``ws.client.files``), so a regression that wrote to the
container's disk would fail here even though the file "exists" locally.
"""

import pandas as pd
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


def _real_shape_gdf():
    """Multi-row gdf with the unset ``alert_polygon`` shape that
    convert_to_geopandas actually emits (empty object Series -> NaN floats)."""
    polys = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
    ]
    gdf = gpd.GeoDataFrame(
        {"area_ha": [1.0, 2.0]}, geometry=gpd.GeoSeries(polys, crs="EPSG:4326")
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    gdf["point"] = gdf.geometry.centroid
    gdf["alert_polygon"] = pd.Series([], dtype="object")
    return gdf


def _read_back(ws, remote_path, tmp_path, suffix):
    """Stage a remote export back down so GeoPandas can open it."""
    local = tmp_path / f"readback{suffix}"
    local.write_bytes(ws.read_bytes(remote_path))
    return local


@pytest.mark.parametrize("fmt", ["csv", "gpkg", "geojson"])
def test_export_handles_unset_alert_polygon_multirow(ws, fmt):
    # Regression: NaN alert_polygon must not crash any export format.
    folder = ws.results_dir("recipe_a")
    path = export_alerts_gdf(_real_shape_gdf(), ws, folder, fmt,
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert ws.exists(path)


def test_export_gpkg_drops_secondary_geometries(ws, tmp_path):
    folder = ws.results_dir("recipe_a")
    path = export_alerts_gdf(_gdf(), ws, folder, "gpkg",
                             ["GLAD-L", "RADD"], "2026-01-01", "2026-06-12")
    assert path == f"{folder}/alerts_glad-l-radd_2026-01-01_2026-06-12.gpkg"
    back = gpd.read_file(_read_back(ws, path, tmp_path, ".gpkg"))
    assert "point" not in back.columns and "bounding_box" not in back.columns
    assert back.geometry.iloc[0].geom_type == "Polygon"


def test_export_geojson(ws):
    folder = ws.results_dir("recipe_a")
    path = export_alerts_gdf(_gdf(), ws, folder, "geojson",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert path == f"{folder}/alerts_glad-l_2026-01-01_2026-06-12.geojson"
    assert ws.exists(path)


def test_export_csv_keeps_columns_as_wkt(ws):
    folder = ws.results_dir("recipe_a")
    path = export_alerts_gdf(_gdf(), ws, folder, "csv",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert path.endswith(".csv")
    assert "POINT" in ws.read_text(path)  # point column preserved as WKT


def test_export_rejects_unknown_format(ws):
    with pytest.raises(ValueError):
        export_alerts_gdf(_gdf(), ws, ws.results_dir("recipe_a"), "shp", [], "", "")
    assert ws.client.files.files == {}


def test_export_leaves_no_scratch_residue(ws, tmp_path):
    """The staged local copy is released once it has been uploaded."""
    export_alerts_gdf(_gdf(), ws, ws.results_dir("recipe_a"), "gpkg",
                      ["GLAD-L"], "2026-01-01", "2026-06-12")
    scratch = tmp_path / "scratch"
    assert [p for p in scratch.rglob("*") if p.is_file()] == []


def test_export_formats_constant():
    assert set(EXPORT_FORMATS) == {"csv", "gpkg", "geojson"}
