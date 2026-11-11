"""Opening a version 1 recipe converts it once, under the manifest lock (v1 migration, task 5)."""

import json
import threading
import time

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box, shape

from deforestation_alerts.project import io
from gui.scripts.v1_open import ensure_converted, resolve_v1_aoi
from tests._v1_helpers import write_recipe

AOI_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
}]}
NAME = "recipe_2026-09-29-223039"


def _counting(delay=0.0):
    calls = []

    def resolve(params):
        calls.append(params["aoi_method"])
        time.sleep(delay)
        return AOI_FC

    return resolve, calls


def test_ensure_converted_converts_once(tmp_path):
    write_recipe(tmp_path / NAME)
    resolve, calls = _counting()
    assert ensure_converted(NAME, tmp_path, resolve=resolve) is True
    assert ensure_converted(NAME, tmp_path, resolve=resolve) is False
    assert calls == ["ADMIN1"]
    assert io.load_project(NAME, tmp_path).migrated_from.version == 1


def test_stale_marker_beside_a_manifest_is_removed(tmp_path):
    write_recipe(tmp_path / NAME)
    resolve, _ = _counting()
    ensure_converted(NAME, tmp_path, resolve=resolve)
    marker = tmp_path / NAME / ".v1_converting"
    marker.touch()  # a death after the manifest, before the marker was removed
    assert ensure_converted(NAME, tmp_path, resolve=resolve) is False
    assert not marker.exists()


def test_two_opens_convert_once(tmp_path):
    write_recipe(tmp_path / NAME)
    resolve, calls = _counting(delay=0.2)
    results, errors = [], []

    def open_it():
        try:
            results.append(ensure_converted(NAME, tmp_path, resolve=resolve))
            io.load_project(NAME, tmp_path)
        except Exception as exc:  # surfaced by the assert below
            errors.append(exc)

    threads = [threading.Thread(target=open_it) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert sorted(results) == [False, True] and len(calls) == 1


def test_v2_project_is_left_alone(tmp_path):
    (tmp_path / "new").mkdir()
    resolve, calls = _counting()
    assert ensure_converted("new", tmp_path, resolve=resolve) is False
    assert calls == [] and list((tmp_path / "new").iterdir()) == []


def test_stored_geojson_needs_no_earth_engine():
    # the conftest guard fails the test on any Earth Engine call
    assert resolve_v1_aoi({"aoi_method": "DRAW", "aoi_geo_json": AOI_FC}) == AOI_FC


def test_empty_drawing_raises():
    with pytest.raises(ValueError):
        resolve_v1_aoi({"aoi_method": "DRAW", "aoi_geo_json": {"type": "FeatureCollection", "features": []}})


def test_vector_file_filtered_by_column(tmp_path):
    path = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame({"name": ["a", "b"], "when": pd.to_datetime(["2020-01-01", "2021-01-01"])},
                     geometry=[box(0, 0, 1, 1), box(5, 5, 6, 6)], crs="EPSG:4326").to_file(path)
    fc = resolve_v1_aoi({"aoi_method": "SHAPE", "aoi_vector_json": {"pathname": str(path), "column": "name", "value": "b"}})
    assert len(fc["features"]) == 1 and fc["features"][0]["properties"] == {}
    assert shape(fc["features"][0]["geometry"]).bounds == (5.0, 5.0, 6.0, 6.0)
    json.dumps(fc)  # a date attribute would not serialise: only the geometry is kept
    all_fc = resolve_v1_aoi({"aoi_method": "SHAPE", "aoi_vector_json": {"pathname": str(path), "column": "ALL", "value": None}})
    assert len(all_fc["features"]) == 2


def test_missing_vector_file_names_the_path(tmp_path):
    gone = tmp_path / "gone.shp"
    with pytest.raises(ValueError, match="gone.shp"):
        resolve_v1_aoi({"aoi_method": "SHAPE", "aoi_vector_json": {"pathname": str(gone), "column": "ALL", "value": None}})
