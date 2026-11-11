from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, box

from deforestation_alerts.engine import vector_dates, vector_input
from deforestation_alerts.engine.vector_input import VectorInputError
from deforestation_alerts.project.models import CustomSource, VectorSpec


def _source(path, *, driver, date_col="d", order="iso", side_m=30.0, lon=None, lat=None, layer=None):
    return CustomSource(name="v", origin="vector", ref=str(path), asset_type="FILE", format="date_only",
                        digit=1, vector=VectorSpec(driver=driver, layer=layer, lon=lon, lat=lat, date=date_col,
                                                   date_order=order, side_m=side_m))


def test_a_semicolon_csv_with_decimal_commas_and_a_bom(tmp_path):
    path = tmp_path / "alerts.csv"
    path.write_text("﻿ID;Lon;Lat;Fecha\n1;-55,5;-12,25;14/08/2026\n2;;-12;15/08/2026\n", encoding="utf-8")
    assert vector_input.driver_of(path) == "csv"
    assert vector_input.list_layers(path) == ()

    bare = vector_input.read_table(path)
    assert list(bare.columns[:4]) == ["ID", "Lon", "Lat", "Fecha"]
    assert bare.geometry.isna().all()
    assert vector_input.suggest_columns(bare.columns) == {"lon": "Lon", "lat": "Lat", "date": "Fecha"}

    gdf = vector_input.read_table(path, lon="Lon", lat="Lat")
    assert gdf.crs.to_epsg() == 4326
    assert gdf.geometry.iloc[0].equals(Point(-55.5, -12.25))
    assert gdf.geometry.iloc[1] is None
    s = vector_input.summarize(gdf, "Fecha")
    assert (s.points, s.polygons, s.skipped, s.reading.order) == (1, 0, 1, "dmy")
    assert s.date_min == s.date_max == date(2026, 8, 14)
    assert s.bounds == pytest.approx((-55.5, -12.25, -55.5, -12.25))


def test_a_windows_codepage_csv_reads(tmp_path):
    path = tmp_path / "excel.csv"
    path.write_bytes("Lon;Lat;Fecha;Lugar\n-55,5;-12,25;14/08/2026;São Félix\n".encode("cp1252"))
    bare = vector_input.read_table(path)
    assert list(bare.columns[:4]) == ["Lon", "Lat", "Fecha", "Lugar"]
    gdf = vector_input.read_table(path, lon="Lon", lat="Lat")
    assert gdf["Lugar"].iloc[0] == "São Félix"
    assert gdf.geometry.iloc[0].equals(Point(-55.5, -12.25))


def test_a_csv_with_out_of_range_coordinates_and_missing_columns(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("x,y,date\n200,1,2026-01-01\n10,-95,2026-01-02\nabc,1,2026-01-03\n5,6,2026-01-04\n")
    gdf = vector_input.read_table(path, lon="x", lat="y")
    assert [g is None for g in gdf.geometry] == [True, True, True, False]
    with pytest.raises(VectorInputError) as exc:
        vector_input.read_table(path, lon="lon", lat="y")
    assert exc.value.key == "steps.alerts.err_vector_columns"
    with pytest.raises(VectorInputError) as exc:
        vector_input.summarize(gdf, "fecha")
    assert exc.value.key == "steps.alerts.err_vector_columns"


def test_summarize_without_an_order(tmp_path):
    gdf = gpd.GeoDataFrame({"d": ["03/04/2026", "05/06/2026"]}, geometry=[Point(0, 0), None], crs=4326)
    s = vector_input.summarize(gdf, "d")
    assert s.reading.ambiguous and s.reading.order is None
    assert (s.points, s.skipped, s.date_min, s.date_max) == (1, 1, None, None)
    s = vector_input.summarize(gdf, "d", "dmy")
    assert s.date_min == s.date_max == date(2026, 4, 3)


def test_driver_of_refuses_other_extensions(tmp_path):
    assert vector_input.driver_of(tmp_path / "A.SHP") == "shp"
    assert vector_input.driver_of(tmp_path / "a.gpkg") == "gpkg"
    assert vector_input.driver_of(tmp_path / "A.GPKG") == "gpkg" and vector_input.driver_of("a.CSV") == "csv"
    with pytest.raises(VectorInputError) as exc:
        vector_input.driver_of(tmp_path / "a.kml")
    assert exc.value.key == "steps.alerts.err_vector_unreadable"


def test_a_gpkg_in_a_projected_crs_comes_back_in_wgs84(tmp_path):
    path = tmp_path / "alerts.gpkg"
    src = gpd.GeoDataFrame({"d": pd.to_datetime(["2026-08-14"])}, geometry=[Point(500000, 8600000)],
                           crs="EPSG:32721")
    src.to_file(path, layer="pts")
    src.iloc[:0].to_file(path, layer="other")
    assert vector_input.driver_of(path) == "gpkg"
    assert set(vector_input.list_layers(path)) == {"pts", "other"}

    gdf = vector_input.read_table(path, layer="pts")
    want = src.to_crs(4326).geometry.iloc[0]
    got = gdf.geometry.iloc[0]
    assert gdf.crs.to_epsg() == 4326
    assert abs(got.x - want.x) < 1e-9 and abs(got.y - want.y) < 1e-9
    s = vector_input.summarize(gdf, "d")
    assert s.reading.order == "native"
    feats, _ = vector_input.usable_features(gdf, "d", "native")
    assert int(feats["date"].iloc[0]) == int(vector_dates.parse(pd.Series(["2026-08-14"]), "iso")[0])

    with pytest.raises(VectorInputError) as exc:
        vector_input.read_table(path, layer="nope")
    assert exc.value.key == "steps.alerts.err_vector_unreadable"


def _shapefile(tmp_path, name="pts"):
    path = tmp_path / f"{name}.shp"
    gpd.GeoDataFrame({"d": ["2026-01-05"]}, geometry=[Point(1, 2)], crs=4326).to_file(path)
    return path


def test_a_shapefile_reads_and_its_sidecars_are_found_in_any_case(tmp_path):
    path = _shapefile(tmp_path)
    for p in tmp_path.iterdir():
        p.rename(p.with_name(p.stem.upper() + p.suffix.upper()))
    gdf = vector_input.read_table(tmp_path / "PTS.SHP")
    assert gdf.crs.to_epsg() == 4326 and gdf.geometry.iloc[0].equals(Point(1, 2))
    assert not path.exists()


def test_a_shapefile_without_prj_is_refused(tmp_path):
    path = _shapefile(tmp_path)
    path.with_suffix(".prj").unlink()
    with pytest.raises(VectorInputError) as exc:
        vector_input.read_table(path)
    assert exc.value.key == "steps.alerts.err_vector_no_crs"


def test_a_shapefile_without_dbf_is_refused(tmp_path):
    path = _shapefile(tmp_path)
    path.with_suffix(".dbf").unlink()
    with pytest.raises(VectorInputError) as exc:
        vector_input.read_table(path)
    assert exc.value.key == "steps.alerts.err_vector_sidecars"


def test_geometry_kinds(tmp_path):
    geoms = [Point(0, 0), MultiPoint([(1, 1), (2, 2)]), box(0, 0, 1, 1),
             MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]), LineString([(0, 0), (1, 1)]), None]
    path = tmp_path / "mixed.gpkg"
    gpd.GeoDataFrame({"d": ["2026-01-05"] * 6}, geometry=geoms, crs=4326).to_file(path)
    gdf = vector_input.read_table(path)
    feats, stats = vector_input.usable_features(gdf, "d", "iso")
    assert stats == {"features_read": 6, "features_skipped": 2, "points": 3, "polygons": 2}
    assert list(feats.columns) == ["date", "geometry"]
    assert sorted(feats.geom_type) == ["MultiPolygon", "Point", "Point", "Point", "Polygon"]
    assert (feats["date"] == 2026005).all()
    s = vector_input.summarize(gdf, "d")
    assert (s.points, s.polygons, s.skipped) == (3, 2, 2)


def test_rows_without_a_readable_date_are_dropped():
    gdf = gpd.GeoDataFrame({"d": ["2026-01-05", "garbage", None]},
                           geometry=[Point(0, 0), Point(1, 1), Polygon([(0, 0), (1, 0), (1, 1)])], crs=4326)
    feats, stats = vector_input.usable_features(gdf, "d", "iso")
    assert len(feats) == 1 and stats["features_skipped"] == 2 and stats["polygons"] == 0


def test_read_features_refuses_a_changed_file(tmp_path):
    csv = tmp_path / "a.csv"
    csv.write_text("lon,lat,d\n1,2,25/03/2026\n")
    with pytest.raises(VectorInputError) as exc:
        vector_input.read_features(_source(csv, driver="csv", lon="lon", lat="lat", order="mdy"))
    assert exc.value.key == "steps.prepare.vector_file_changed"

    feats, stats = vector_input.read_features(_source(csv, driver="csv", lon="lon", lat="lat", order="dmy"))
    assert stats["points"] == 1 and int(feats["date"].iloc[0]) == 2026084

    with pytest.raises(VectorInputError) as exc:
        vector_input.read_features(_source(csv, driver="csv", lon="lon", lat="lat", date_col="fecha",
                                           order="dmy"))
    assert exc.value.key == "steps.prepare.vector_file_changed"

    with pytest.raises(VectorInputError) as exc:
        vector_input.read_features(_source(csv, driver="csv", lon="lng", lat="lat", order="dmy"))
    assert exc.value.key == "steps.prepare.vector_file_changed"

    with pytest.raises(VectorInputError) as exc:
        vector_input.read_features(_source(csv, driver="csv", lon="lon", lat="lat", order="dmy", side_m=None))
    assert exc.value.key == "steps.prepare.vector_file_changed"

    # polygons need no side
    gpkg = tmp_path / "p.gpkg"
    gpd.GeoDataFrame({"d": ["2026-01-05"]}, geometry=[box(0, 0, 1, 1)], crs=4326).to_file(gpkg)
    _, stats = vector_input.read_features(_source(gpkg, driver="gpkg", side_m=None))
    assert stats["polygons"] == 1


def test_normalize_vector_keeps_the_window_only_and_an_empty_window_is_valid(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("lon,lat,d\n1,1,2026-01-05\n2,2,2026-02-05\n3,3,2026-03-05\n")
    src = _source(path, driver="csv", lon="lon", lat="lat")
    out = tmp_path / "raw" / "p1.parquet"
    out.parent.mkdir()

    stats = vector_input.normalize_vector(src, date(2026, 1, 1), date(2026, 1, 31), out)
    assert stats["features_in_window"] == 1 and stats["features_read"] == 3
    back = gpd.read_parquet(out)
    assert len(back) == 1 and int(back["date"].iloc[0]) == 2026005 and back.crs.to_epsg() == 4326

    empty = tmp_path / "raw" / "p2.parquet"
    stats = vector_input.normalize_vector(src, date(2025, 1, 1), date(2025, 12, 31), empty)
    assert stats["features_in_window"] == 0
    back = gpd.read_parquet(empty)
    assert len(back) == 0 and back.crs.to_epsg() == 4326 and set(back.columns) == {"date", "geometry"}
    assert not list(Path(out.parent).glob("*.part"))


def test_a_failed_write_leaves_no_part_and_no_output(tmp_path, monkeypatch):
    path = tmp_path / "a.csv"
    path.write_text("lon,lat,d\n1,1,2026-01-05\n")
    src = _source(path, driver="csv", lon="lon", lat="lat")
    out = tmp_path / "p1.parquet"

    def boom(self, target, *args, **kwargs):
        Path(target).write_bytes(b"half a file")
        raise OSError("disk full")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", boom)
    with pytest.raises(OSError, match="disk full"):
        vector_input.normalize_vector(src, date(2026, 1, 1), date(2026, 1, 31), out)
    assert not out.with_name(out.name + ".part").exists()
    assert not out.exists()


def test_usable_features_keeps_the_frames_crs():
    gdf = gpd.GeoDataFrame({"d": ["2026-01-05"]}, geometry=[Point(500000, 8600000)], crs="EPSG:32721")
    feats, _ = vector_input.usable_features(gdf, "d", "iso")
    assert feats.crs.to_epsg() == 32721
