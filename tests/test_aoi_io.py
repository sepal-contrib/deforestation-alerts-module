import json
import re
from datetime import date
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import box

from gui.i18n import MESSAGES_DIR
from gui.scripts import aoi_io

_BOX = (-55.1, -10.1, -55.0, -10.0)


def _gdf():
    return gpd.GeoDataFrame(geometry=[box(-55.1, -10.1, -55.0, -10.0)], crs="EPSG:4326")


def test_digest_survives_a_geojson_round_trip(tmp_path):
    g = _gdf()
    aoi_io.write_sidecar(tmp_path, g)
    back = gpd.read_file(tmp_path / "aoi.geojson")
    assert aoi_io.geometry_digest(g) == aoi_io.geometry_digest(back)


def test_lazy_digest_for_admin_and_asset():
    assert aoi_io.lazy_digest(SimpleNamespace(method="ADMIN1", admin="2317", spec=None)) == "admin:2317"
    spec = SimpleNamespace(asset_id="users/me/aoi")
    assert aoi_io.lazy_digest(SimpleNamespace(method="ASSET", admin=None, spec=spec)) == "asset:users/me/aoi"
    assert aoi_io.lazy_digest(SimpleNamespace(method="DRAW", admin=None, spec=None)) is None
    # a column filter picks another geometry from the same asset (ruling #12);
    # "ALL" (a fresh unfiltered pick) and None (a restored one) are the same AOI
    filtered = SimpleNamespace(asset_id="users/me/aoi", column="NAME", value="Canindeyú")
    assert (aoi_io.lazy_digest(SimpleNamespace(method="ASSET", admin=None, spec=filtered))
            == "asset:users/me/aoi|NAME='Canindeyú'")
    unfiltered = SimpleNamespace(asset_id="users/me/aoi", column="ALL", value=None)
    assert aoi_io.lazy_digest(SimpleNamespace(method="ASSET", admin=None, spec=unfiltered)) == "asset:users/me/aoi"


def test_bounds_from_geojson():
    geom = {"type": "Polygon", "coordinates": [[[-55.1, -10.1], [-55.0, -10.1], [-55.0, -10.0], [-55.1, -10.0], [-55.1, -10.1]]]}
    assert aoi_io.bounds_from_geojson(geom) == (-55.1, -10.1, -55.0, -10.0)


def test_build_record_with_gdf():
    spec = SimpleNamespace(to_dict=lambda: {"method": "DRAW", "schema_version": 1})
    result = SimpleNamespace(name="My area", method="DRAW", admin=None, spec=spec)
    rec = aoi_io.build_record(result, _gdf(), (-55.1, -10.1, -55.0, -10.0))
    assert rec.name == "My area" and rec.geometry_file == "aoi.geojson"
    assert rec.spec == {"method": "DRAW", "schema_version": 1} and len(rec.digest) == 64


def test_build_record_without_gdf_is_identified_by_its_code():
    spec = SimpleNamespace(to_dict=lambda: {"method": "ADMIN1", "schema_version": 1, "admin_codes": ["194", "2317"]})
    result = SimpleNamespace(name="PRY_Canindeyu", method="ADMIN1", admin="2317", spec=spec)
    rec = aoi_io.build_record(result, None, _BOX)
    assert rec.digest == "admin:2317" and rec.geometry_file is None and rec.bounds == _BOX


def test_auto_project_name_is_valid_and_unique():
    today = date(2026, 9, 30)
    assert aoi_io.auto_project_name("PRY_Canindeyú", [], today) == "PRY_Canindey_20260930"
    assert aoi_io.auto_project_name("x/y", ["x_y_20260930"], today) == "x_y_20260930_2"


def test_ee_aoi_without_any_geometry_raises_the_translated_message(tmp_path):
    """Steps toast str(exc) as is (ruling #64): it must be the catalogue text, not a raw English literal."""
    text = json.loads((MESSAGES_DIR / "en" / "steps.json").read_text())["steps"]["aoi"]["no_geometry"]
    with pytest.raises(ValueError, match=re.escape(text)):
        aoi_io.ee_aoi(None, tmp_path)  # no selection and no sidecar
    with pytest.raises(ValueError, match=re.escape(text)):
        aoi_io.ee_aoi(SimpleNamespace(feature_collection=None), None)


def test_ee_aoi_is_always_a_feature_collection(tiny_aoi, tmp_path):
    """Downloads and previews clip to it (ruling #14): an IMAGE or IMAGE_COLLECTION
    asset AOI, or a bare geometry, is wrapped; the sidecar is the fallback."""
    import ee

    fc = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    assert aoi_io.ee_aoi(SimpleNamespace(feature_collection=fc), None) is fc

    image = ee.Image.constant(1).clip(tiny_aoi)
    picked = {"image": image, "collection": ee.ImageCollection([image]), "geometry": tiny_aoi}
    wrapped = {k: aoi_io.ee_aoi(SimpleNamespace(feature_collection=obj), None) for k, obj in picked.items()}
    aoi_io.write_sidecar(tmp_path, _gdf())
    wrapped["sidecar"] = aoi_io.ee_aoi(None, tmp_path)
    assert all(isinstance(w, ee.FeatureCollection) for w in wrapped.values())

    boxes = ee.Dictionary({k: w.geometry().bounds() for k, w in wrapped.items()}).getInfo()
    for k, geom in boxes.items():
        assert aoi_io.bounds_from_geojson(geom) == pytest.approx(_BOX, abs=1e-4), k


def test_sidecar_fallback_keeps_the_pickers_straight_edges(gee, tmp_path):
    """pysepal converts a picked gdf with planar edges. A default (geodesic) read
    of the sidecar would bow this AOI's southern edge ~17 km past the picked box."""
    wide = gpd.GeoDataFrame(geometry=[box(-60, -10, -40, -5)], crs="EPSG:4326")
    aoi_io.write_sidecar(tmp_path, wide)
    geom = aoi_io.ee_aoi(None, tmp_path).geometry().bounds().getInfo()
    assert aoi_io.bounds_from_geojson(geom) == pytest.approx((-60, -10, -40, -5), abs=1e-6)


def test_sidecar_gdf_from_an_evaluated_collection():
    from gui.scripts import aoi_io

    info = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {
            "type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}]}
    gdf = aoi_io.sidecar_gdf(info)
    assert gdf.crs.to_epsg() == 4326 and len(gdf) == 1 and gdf.total_bounds.tolist() == [0, 0, 1, 1]


def test_sidecar_request_keeps_the_geometry_and_drops_the_properties(tiny_aoi):
    import ee

    fc = ee.FeatureCollection([ee.Feature(tiny_aoi, {"name": "x", "code": 1})])
    gdf = aoi_io.sidecar_gdf(aoi_io.sidecar_request(fc).getInfo())
    assert list(gdf.columns) == ["geometry"] and len(gdf) == 1
    assert tuple(gdf.total_bounds) == pytest.approx(_BOX, abs=1e-3)  # 30 m simplify, about 3e-4 degrees


def test_sidecar_gdf_of_an_empty_collection_is_an_empty_frame():
    gdf = aoi_io.sidecar_gdf({"type": "FeatureCollection", "features": []})
    assert gdf.crs.to_epsg() == 4326 and len(gdf) == 0
