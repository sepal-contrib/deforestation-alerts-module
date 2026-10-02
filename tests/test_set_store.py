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


def test_active_store_and_load(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    assert set_store.active_store_path(p) == store
    assert len(set_store.load_active_alerts(p)) == 2


def test_no_active_set_or_store_loads_nothing(tmp_path, monkeypatch):
    assert set_store.active_store_path(None) is None
    assert set_store.load_active_alerts(None) is None
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    store.unlink()  # the set's files were deleted or moved (review focus 4)
    assert set_store.load_active_alerts(p) is None
    p.active_set = None
    assert set_store.active_store_path(p) is None
    assert set_store.load_active_alerts(p) is None


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


def _fake_tippecanoe(monkeypatch, built, during=None):
    """``pmt.alerts_to_pmtiles`` as a recorder of the alerts it was handed;
    ``during`` runs in its place (what happens while tippecanoe runs)."""
    def build(alerts, pm, *, guard=None):
        built.append(alerts)
        if during is not None:
            during()
        if guard is not None:
            guard()
        pm.write_bytes(b"PMTiles")
        return pm

    monkeypatch.setattr(pmt, "tippecanoe_available", lambda: True)
    monkeypatch.setattr(pmt, "alerts_to_pmtiles", build)


def test_ensure_pmtiles_builds_from_the_store_on_disk(tmp_path, monkeypatch):
    """During a re-run of the same set the alerts a session holds are the
    previous ones (the set id is the same, so they pass as the set's): tiles
    built from them would be newer than the new store, and kept. So the store
    on disk is what the tiles are built from, whatever is on hand."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    _alerts().iloc[0:1].to_parquet(store)  # the re-run's store: one alert left
    built = []
    _fake_tippecanoe(monkeypatch, built)

    pm = set_store.ensure_pmtiles(p, s)

    assert pm == store.parent / "alerts.pmtiles" and pm.exists()
    [alerts] = built
    assert alerts["alert_id"].tolist() == [0]


def test_ensure_pmtiles_waits_while_the_project_is_being_written(tmp_path, monkeypatch):
    """An Integrate re-running the set holds the project's writer mark while
    it swaps the store in and builds the tiles itself; a review Save holds it
    while it rewrites the store. Stale tiles are not rebuilt meanwhile (the
    caller retries when the mark is released), and current ones are served."""
    from gui.store.project_writers import writing

    p, s, store = _project_with_set(tmp_path, monkeypatch)
    built = []
    _fake_tippecanoe(monkeypatch, built)
    pm = store.parent / "alerts.pmtiles"

    with writing("d"):
        with pytest.raises(set_store.StoreBusy):
            set_store.ensure_pmtiles(p, s)
        assert built == [] and not pm.exists()
        with writing("other"):  # another project's writer is not this one's
            pass
    assert set_store.ensure_pmtiles(p, s) == pm and len(built) == 1  # the writer is done

    with writing("d"):
        assert set_store.ensure_pmtiles(p, s) == pm and len(built) == 1  # current: served, not rebuilt


def test_ensure_pmtiles_marks_the_project_as_being_written(tmp_path, monkeypatch):
    """Spec §12: the tile build writes into the set's folder, so the Project
    dialog's delete guard sees the project for its duration."""
    from gui.store.project_writers import is_writing

    p, s, store = _project_with_set(tmp_path, monkeypatch)
    busy = []
    _fake_tippecanoe(monkeypatch, [], during=lambda: busy.append(is_writing("d")))
    assert not is_writing("d")
    set_store.ensure_pmtiles(p, s)
    assert busy == [True] and not is_writing("d")


def test_a_store_rewritten_during_the_build_discards_the_tiles(tmp_path, monkeypatch):
    """Another session's Integrate (no shared writer mark) swaps the store in
    while tippecanoe runs on the store read before: the tiles would be newer
    than the new store yet show the old alerts, so they are dropped."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    built = []

    def rerun_swaps_the_store():  # once, during the first build
        if len(built) == 1:
            _alerts().iloc[0:1].to_parquet(store)

    _fake_tippecanoe(monkeypatch, built, during=rerun_swaps_the_store)

    with pytest.raises(set_store.StoreBusy):
        set_store.ensure_pmtiles(p, s)
    assert len(built) == 1 and not (store.parent / "alerts.pmtiles").exists()
    assert set_store.pmtiles_stale(p, s)  # so the next ask rebuilds, from the new store
    assert set_store.ensure_pmtiles(p, s).exists() and built[-1]["alert_id"].tolist() == [0]


def test_on_wait_runs_before_the_writer_mark_is_released(tmp_path, monkeypatch):
    """Overview retries a deferred build when the project's writers are done,
    which it learns from the release of the writer mark. A ``StoreBusy``
    raised by the guard inside the build's own mark reaches it after that
    release, so the retry must be noted while the mark is still held."""
    from gui.store.project_writers import is_writing, writing

    p, s, store = _project_with_set(tmp_path, monkeypatch)
    built = []

    def rerun_swaps_the_store():
        if len(built) == 1:
            _alerts().iloc[0:1].to_parquet(store)

    _fake_tippecanoe(monkeypatch, built, during=rerun_swaps_the_store)
    seen = []

    with pytest.raises(set_store.StoreBusy):
        set_store.ensure_pmtiles(p, s, on_wait=lambda: seen.append(is_writing("d")))
    assert seen == [True] and not is_writing("d")

    with writing("d"):  # the other refusal, before any mark of the build's own
        with pytest.raises(set_store.StoreBusy):
            set_store.ensure_pmtiles(p, s, on_wait=lambda: seen.append(is_writing("d")))
    assert seen == [True, True]
    assert set_store.ensure_pmtiles(p, s, on_wait=lambda: seen.append("no")).exists()  # a build: not called
    assert seen == [True, True]


def test_ensure_pmtiles_without_tippecanoe_is_none(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    monkeypatch.setattr(pmt, "tippecanoe_available", lambda: False)
    assert set_store.ensure_pmtiles(p, s) is None
    assert not (store.parent / "alerts.pmtiles").exists()


@pytest.mark.skipif(not pmt.tippecanoe_available(), reason="tippecanoe not installed")
def test_ensure_pmtiles_builds_only_when_stale(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    _alerts().to_parquet(store)  # a store with the status and area columns the tiles carry
    pm = set_store.ensure_pmtiles(p, s)
    assert pm == store.parent / "alerts.pmtiles"
    assert pm.read_bytes()[:7] == b"PMTiles"
    built = pm.stat().st_mtime_ns

    assert set_store.ensure_pmtiles(p, s) == pm
    assert pm.stat().st_mtime_ns == built  # current: not rebuilt

    earlier = store.stat().st_mtime - 5
    os.utime(pm, (earlier, earlier))  # the store was rewritten after the tiles were built
    assert set_store.pmtiles_stale(p, s)
    assert set_store.ensure_pmtiles(p, s) == pm
    assert pm.stat().st_mtime > earlier and not set_store.pmtiles_stale(p, s)  # rebuilt
    assert not list(store.parent.glob("*.part"))


def test_loaded_alerts_carry_their_stores_generation(tmp_path, monkeypatch):
    """The loader stamps what it reads; a rewrite of the store since (a
    re-run's swap) reads as changed, so a Save of those alerts is refused."""
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    gdf = set_store.load_active_alerts(p)
    assert gdf.attrs[set_store.STORE_GENERATION] == set_store.store_generation(store)
    assert not set_store.generation_changed(gdf, store)
    assert not set_store.generation_changed(gdf.copy().iloc[0:1], store)  # the stamp follows pandas copies
    staged = store.with_name("alert_db.staged.parquet")
    _alerts().to_parquet(staged)
    os.replace(staged, store)  # Integrate's swap
    assert set_store.generation_changed(gdf, store)
    assert not set_store.generation_changed(_gdf(), store)  # unstamped alerts are not checked
    assert not set_store.generation_changed(None, store)


def test_store_generation_follows_every_rewrite(tmp_path, monkeypatch):
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet

    p, s, store = _project_with_set(tmp_path, monkeypatch)
    first = set_store.store_generation(store)
    assert first is not None and set_store.store_generation(store) == first
    save_gdf_to_parquet(_alerts(), str(store))  # a review Save's write (.part + rename)
    second = set_store.store_generation(store)
    assert second != first
    staged = store.with_name("alert_db.staged.parquet")
    _alerts().to_parquet(staged)
    os.replace(staged, store)  # Integrate's swap
    assert set_store.store_generation(store) not in (first, second)
    store.unlink()
    assert set_store.store_generation(store) is None


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


def test_a_go_wish_selects_its_alert_when_its_set_is_published(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 0
    state = _session(p)
    alerts = _alerts().assign(alert_id=[5, 7])
    state.goto_alert.set(("s", 7))
    assert set_store.publish_set_alerts(state, "d", s, alerts)
    assert state.actual_alert_id.value == 1 and state.goto_alert.value is None


def test_a_wish_for_another_set_is_dropped_and_the_saved_position_used(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 1
    state = _session(p)
    state.goto_alert.set(("other", 0))
    assert set_store.publish_set_alerts(state, "d", s, _alerts())
    assert state.actual_alert_id.value == 1 and state.goto_alert.value is None


def test_position_of_and_load_set_alerts(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    gdf = set_store.load_set_alerts(p, s)
    assert len(gdf) == 2 and gdf.attrs[set_store.STORE_GENERATION] == set_store.store_generation(store)
    assert set_store.position_of(gdf.assign(alert_id=[4, 9]), 9) == 1
    assert set_store.position_of(gdf.assign(alert_id=[4, 9]), 3) is None
    assert set_store.position_of(None, 0) is None
    store.unlink()
    assert set_store.load_set_alerts(p, s) is None


def test_every_install_drops_a_pending_go(tmp_path, monkeypatch):
    p, s, store = _project_with_set(tmp_path, monkeypatch)
    state = _session(p)
    state.goto_alert.set(("s", 1))
    state.load_project_state(p)
    assert state.goto_alert.value is None
