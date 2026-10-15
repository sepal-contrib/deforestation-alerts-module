"""The on-demand exports land in the folder they are given (the set folder),
built in a private scratch folder and copied into place.
"""

from pathlib import Path

import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from deforestation_alerts import gdal_env
from deforestation_alerts.scripts.alert_export import (
    EXPORT_FORMATS, SCOPES, export_alerts_gdf, export_name, scope_counts, scope_rows)


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


@pytest.mark.parametrize("fmt", ["csv", "gpkg"])
def test_export_handles_unset_alert_polygon_multirow(data_root, fmt):
    # Regression: NaN alert_polygon must not crash any export format.
    folder = data_root / "recipe_a"
    path = export_alerts_gdf(_real_shape_gdf(), folder, fmt,
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert Path(path).exists()


def test_export_gpkg_drops_secondary_geometries(data_root):
    folder = data_root / "recipe_a"
    path = export_alerts_gdf(_gdf(), folder, "gpkg",
                             ["GLAD-L", "RADD"], "2026-01-01", "2026-06-12")
    assert path == f"{folder}/alerts_glad-l-radd_2026-01-01_2026-06-12.gpkg"
    back = gpd.read_file(path)
    assert "point" not in back.columns and "bounding_box" not in back.columns
    assert back.geometry.iloc[0].geom_type == "Polygon"


def test_export_csv_keeps_columns_as_wkt(data_root):
    folder = data_root / "recipe_a"
    path = export_alerts_gdf(_gdf(), folder, "csv",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert path.endswith(".csv")
    assert "POINT" in Path(path).read_text()  # point column preserved as WKT


def test_export_rejects_unknown_format(data_root, data_files):
    with pytest.raises(ValueError):
        export_alerts_gdf(_gdf(), data_root / "recipe_a", "shp", [], "", "")
    assert data_files(data_root) == []


def test_export_leaves_only_the_export(data_root, data_files, tmp_path, monkeypatch):
    """Only the export lands in the folder (no ``.part``, no GDAL sidecar),
    and its scratch folder is deleted."""
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(gdal_env, "scratch_dir", lambda: scratch)
    folder = data_root / "recipe_a"
    path = export_alerts_gdf(_gdf(), folder, "gpkg",
                             ["GLAD-L"], "2026-01-01", "2026-06-12")
    assert data_files(data_root) == [Path(path)]
    assert [p for p in scratch.rglob("*")] == []


def _statuses(*statuses):
    polys = [Polygon([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]) for i in range(len(statuses))]
    return gpd.GeoDataFrame({"area_ha": [1.0] * len(statuses), "status": list(statuses)},
                            geometry=gpd.GeoSeries(polys, crs="EPSG:4326"))


def test_export_formats_constant():
    assert list(EXPORT_FORMATS) == ["gpkg", "csv"]
    assert SCOPES == ("confirmed", "reviewed", "unreviewed", "all")


def test_scope_rows_split_by_status():
    gdf = _statuses("Confirmed", "False Positive", "Need revision", "Not reviewed", None, "")
    assert list(scope_rows(gdf, "confirmed").index) == [0]
    assert list(scope_rows(gdf, "reviewed").index) == [0, 1, 2]
    assert list(scope_rows(gdf, "unreviewed").index) == [3, 4, 5]
    assert len(scope_rows(gdf, "all")) == 6
    assert scope_counts(gdf) == {"confirmed": 1, "reviewed": 3, "unreviewed": 3, "all": 6}


def test_scope_rows_without_status_column():
    gdf = _statuses("x").drop(columns=["status"])
    assert scope_counts(gdf) == {"confirmed": 0, "reviewed": 0, "unreviewed": 1, "all": 1}


def test_scope_rows_rejects_an_unknown_scope():
    with pytest.raises(ValueError):
        scope_rows(_statuses("Confirmed"), "mine")


def test_export_name_suffixes_every_scope_but_all():
    assert export_name(["GLAD-L"], "2026-01-01", "2026-06-12", "gpkg") == \
        "alerts_glad-l_2026-01-01_2026-06-12.gpkg"
    assert export_name(["GLAD-L"], "2026-01-01", "2026-06-12", "csv", "confirmed") == \
        "alerts_glad-l_2026-01-01_2026-06-12_confirmed.csv"


def test_export_writes_only_the_scoped_rows(data_root):
    folder = data_root / "recipe_a"
    path = export_alerts_gdf(_statuses("Confirmed", "Not reviewed"), folder, "gpkg",
                             ["GLAD-L"], "2026-01-01", "2026-06-12", scope="confirmed")
    assert path == f"{folder}/alerts_glad-l_2026-01-01_2026-06-12_confirmed.gpkg"
    assert list(gpd.read_file(path)["status"]) == ["Confirmed"]


def test_export_rejects_geojson(data_root):
    with pytest.raises(ValueError):
        export_alerts_gdf(_gdf(), data_root / "recipe_a", "geojson", [], "", "")
