"""The active alert set's files for Overview (spec §9.2, §9.3): its store, its
PMTiles (rebuilt when older than the store, spec §5.3), the GeoJSON centroid
fallback (spec §9.2, §12) and the map-click hit test.
"""

import os
import time
from datetime import datetime

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.engine import pmtiles as pmt
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project
from gui.scripts import set_store


def _gdf():
    return gpd.GeoDataFrame({"alert_id": [0, 1]},
                            geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01)], crs="EPSG:4326")


def test_hit_alert_inside_and_near():
    g = _gdf()
    assert set_store.hit_alert(g, 0.005, 0.005) == 0
    assert set_store.hit_alert(g, 1.013, 1.005) == 1   # 0.003° outside, within tolerance
    assert set_store.hit_alert(g, 5, 5) is None


def test_hit_alert_without_alerts():
    assert set_store.hit_alert(None, 0.005, 0.005) is None
    assert set_store.hit_alert(_gdf().iloc[0:0], 0.005, 0.005) is None


def _project_with_set(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    s = AlertSet(id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
                 store_rel_path="sets/s/alert_db.parquet", combined_rel_path="c", footprint_rel_path="f",
                 fingerprint="f", created_at=datetime.now())
    p.alert_sets["s"] = s
    p.active_set = "s"
    store = tmp_path / "d" / s.store_rel_path
    store.parent.mkdir(parents=True)
    _gdf().to_parquet(store)
    return p, s, store


def test_active_store_and_load(tmp_path, monkeypatch, ws):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.active_store_path(p) == store
    assert len(set_store.load_active_alerts(p, ws)) == 2


def test_no_active_set_or_store_loads_nothing(tmp_path, monkeypatch, ws):
    assert set_store.active_store_path(None) is None
    assert set_store.load_active_alerts(None, ws) is None
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    store.unlink()  # the set's files were deleted or moved (review focus 4)
    assert set_store.load_active_alerts(p, ws) is None
    p.active_set = None
    assert set_store.active_store_path(p) is None
    assert set_store.load_active_alerts(p, ws) is None


def test_set_folder_is_the_folder_of_the_store(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.set_folder(p, s) == store.parent


def test_pmtiles_stale_follows_the_store(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.pmtiles_stale(p, s)  # missing
    pm = store.parent / "alerts.pmtiles"
    pm.write_bytes(b"PMTiles")
    assert not set_store.pmtiles_stale(p, s)
    later = time.time() + 5
    os.utime(store, (later, later))  # a review save rewrote the store
    assert set_store.pmtiles_stale(p, s)


def _alerts():
    return gpd.GeoDataFrame(
        {"alert_id": [0, 1], "status": ["Not reviewed", "Confirmed"], "area_ha": [1.5, 0.5],
         "alert_date_min": [2026.01, 2026.02], "set_id": ["s", "s"]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01)], crs="EPSG:4326",
    )


def test_ensure_pmtiles_without_tippecanoe_is_none(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    monkeypatch.setattr(pmt, "tippecanoe_available", lambda: False)
    assert set_store.ensure_pmtiles(p, s, _alerts()) is None
    assert not (store.parent / "alerts.pmtiles").exists()


@pytest.mark.skipif(not pmt.tippecanoe_available(), reason="tippecanoe not installed")
def test_ensure_pmtiles_builds_only_when_stale(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    pm = set_store.ensure_pmtiles(p, s, _alerts())
    assert pm == store.parent / "alerts.pmtiles"
    assert pm.read_bytes()[:7] == b"PMTiles"
    built = pm.stat().st_mtime_ns

    assert set_store.ensure_pmtiles(p, s, _alerts()) == pm
    assert pm.stat().st_mtime_ns == built  # current: not rebuilt

    earlier = store.stat().st_mtime - 5
    os.utime(pm, (earlier, earlier))  # the store was rewritten after the tiles were built
    assert set_store.pmtiles_stale(p, s)
    assert set_store.ensure_pmtiles(p, s, _alerts()) == pm
    assert pm.stat().st_mtime > earlier and not set_store.pmtiles_stale(p, s)  # rebuilt


def test_centroid_geojson_has_one_point_per_alert():
    fc = set_store.centroid_geojson(_alerts())
    assert fc["type"] == "FeatureCollection"
    points = [f["geometry"] for f in fc["features"]]
    assert [pt["type"] for pt in points] == ["Point", "Point"]
    assert points[0]["coordinates"] == pytest.approx((0.005, 0.005))
    assert points[1]["coordinates"] == pytest.approx((1.005, 1.005))
    assert [f["properties"] for f in fc["features"]] == [
        {"alert_id": 0, "status_code": 0},
        {"alert_id": 1, "status_code": 1},
    ]


def test_centroid_geojson_is_in_wgs84_and_skips_empty_geometries():
    alerts = _alerts().to_crs(epsg=3857)
    alerts.loc[1, "geometry"] = None
    fc = set_store.centroid_geojson(alerts)
    assert len(fc["features"]) == 1
    assert fc["features"][0]["geometry"]["coordinates"] == pytest.approx((0.005, 0.005), abs=1e-6)
    assert set_store.centroid_geojson(_alerts().iloc[0:0]) == {"type": "FeatureCollection", "features": []}
