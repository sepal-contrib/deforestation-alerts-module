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


def test_alerts_belong_to_their_own_set_only(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.alerts_belong_to(_alerts(), s)  # every set_id is "s"
    assert set_store.alerts_belong_to(_gdf(), s)  # no set_id column (a store written before it existed)
    assert set_store.alerts_belong_to(None, s)
    assert set_store.alerts_belong_to(_alerts().iloc[0:0], s)
    assert not set_store.alerts_belong_to(_alerts().assign(set_id="other"), s)
    mixed = _alerts()
    mixed.loc[1, "set_id"] = "other"
    assert not set_store.alerts_belong_to(mixed, s)


def test_a_set_is_held_only_by_its_own_alerts(tmp_path, monkeypatch):
    """The shell's set loader skips a set the alerts on hand already are
    (Integrate publishes them itself); unlike ``alerts_belong_to``, nothing,
    no rows or rows without a ``set_id`` hold no set."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.holds_set(_alerts(), s)
    assert not set_store.holds_set(None, s)
    assert not set_store.holds_set(_alerts().iloc[0:0], s)
    assert not set_store.holds_set(_gdf(), s)
    assert not set_store.holds_set(_alerts().assign(set_id="other"), s)


def _session(p):
    from gui.store.state_manager import AppState

    state = AppState()
    state.load_project_state(p)
    return state


def test_publishing_a_set_resets_the_selection_first_then_restores_its_position(tmp_path, monkeypatch):
    """Analysis's auto-select only runs from -1 and ``view_actual_alert`` only
    follows ``actual_alert_id``: the reset to -1 before the alerts land makes a
    set switch at the same index still show the new set's alert."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 1
    state = _session(p)
    state.alerts_gdf.set(_gdf())
    state.actual_alert_id.set(1)
    seen = []
    state.actual_alert_id.subscribe(lambda i: seen.append(("id", i)))
    state.alerts_gdf.subscribe(lambda g: seen.append(("alerts", len(g))))

    assert set_store.publish_set_alerts(state, "d", s, _alerts()) is True

    assert seen == [("id", -1), ("alerts", 2), ("id", 1)]


def test_publishing_clamps_the_saved_position_and_an_empty_set_selects_nothing(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 9
    state = _session(p)
    assert set_store.publish_set_alerts(state, "d", s, _alerts())
    assert state.actual_alert_id.value == 1
    assert set_store.publish_set_alerts(state, "d", s, _alerts().iloc[0:0])
    assert state.actual_alert_id.value == -1 and len(state.alerts_gdf.value) == 0
    assert set_store.publish_set_alerts(state, "d", s, None)  # the store was deleted or moved
    assert state.alerts_gdf.value is None and state.actual_alert_id.value == -1


def test_a_set_that_is_no_longer_active_is_not_published(tmp_path, monkeypatch):
    """A load that finishes after its project was switched or closed, or after
    another set became active, touches nothing."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    state = _session(p)
    assert set_store.publish_set_alerts(state, "e", s, _alerts()) is False
    other = s.model_copy(update={"id": "t"})
    assert set_store.publish_set_alerts(state, "d", other, _alerts().assign(set_id="t")) is False
    state.close_project_state()
    assert set_store.publish_set_alerts(state, "d", s, _alerts()) is False
    assert state.alerts_gdf.value is None and state.actual_alert_id.value == -1


@pytest.mark.parametrize("tippecanoe", [True, False])
def test_ensure_pmtiles_refuses_another_sets_alerts(tmp_path, monkeypatch, tippecanoe):
    """Tiles written from another set's alerts would be newer than this set's
    store, so the mtime rule would keep them as current."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    built = []
    monkeypatch.setattr(pmt, "tippecanoe_available", lambda: tippecanoe)
    monkeypatch.setattr(pmt, "alerts_to_pmtiles", lambda alerts, pm: built.append(alerts))
    with pytest.raises(ValueError, match="'s'"):
        set_store.ensure_pmtiles(p, s, _alerts().assign(set_id="other"))
    assert built == [] and not (store.parent / "alerts.pmtiles").exists()
    own = _alerts()
    set_store.ensure_pmtiles(p, s, own)
    assert [alerts is own for alerts in built] == ([True] if tippecanoe else [])


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
