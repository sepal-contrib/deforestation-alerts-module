from types import SimpleNamespace

import geopandas as gpd
import solara
from shapely.geometry import Point

from gui.tile.analysis import earlier
from gui.tile.analysis.earlier import EARLIER_ID, EARLIER_LAYER_NAME
from gui.tile.analysis.state import AnalysisState
from tests._pane_helpers import make_panes


def _fc(n):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"status": "Confirmed"},
         "geometry": {"type": "Point", "coordinates": [0, 0]}} for _ in range(n)]}


def _setup(monkeypatch, n=2):
    calls = []

    def near(p, set_id, lon, lat, radius_m, **kw):
        calls.append((set_id, lon, lat, radius_m))
        return _fc(n)

    monkeypatch.setattr(earlier.earlier_reviews, "near_reviews", near)
    monkeypatch.setattr(earlier, "active_set", lambda p: SimpleNamespace(id="s2"))
    gdf = gpd.GeoDataFrame({"point": [Point(1, 2), Point(3, 4)]}, geometry=[Point(1, 2), Point(3, 4)],
                           crs="EPSG:4326")
    app_state = SimpleNamespace(alerts_gdf=solara.reactive(gdf), actual_alert_id=solara.reactive(0),
                                project=solara.reactive(object()))
    widgets = SimpleNamespace(panes=make_panes())
    return widgets, AnalysisState(), app_state, calls


def _earlier(widgets):
    """The overlay's spec on the views, as a list (empty when it is off)."""
    spec = widgets.panes.spec(EARLIER_ID)
    return [] if spec is None else [spec]


def test_switch_on_adds_one_layer_to_every_view(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    (spec,) = _earlier(widgets)
    assert (spec["view"], spec["group"], spec["name"]) == ("all", "earlier", EARLIER_LAYER_NAME)
    assert spec["style"] == {"weight": 2, "fill": False}
    assert len(spec["data"]["features"]) == 2
    first = spec["data"]["features"][0]["properties"]
    assert first["status"] == "Confirmed" and first["style"]["color"] == "#d32f2f"
    assert calls == [("s2", 1.0, 2.0, 1000.0)]


def test_switch_off_removes_it(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    analysis.show_earlier_reviews.set(False)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    assert not _earlier(widgets)


def test_a_new_distance_replaces_the_layer(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    analysis.earlier_radius_km.set(5)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    assert calls[-1][3] == 5000
    assert widgets.panes.ids() == [EARLIER_ID]


def test_the_next_alert_redraws_for_its_point(monkeypatch):
    widgets, analysis, app_state, calls = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    # what an alert change does: clear the alert layers and the overlay,
    # reset, then view
    app_state.actual_alert_id.set(1)
    widgets.panes.clear("alert")
    earlier.remove_earlier_layer(widgets)
    assert not _earlier(widgets)
    analysis.reset_alert()
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    assert (calls[-1][1], calls[-1][2]) == (3.0, 4.0)
    assert len(_earlier(widgets)) == 1
    assert analysis.show_earlier_reviews.value is True  # the switch survives the alert


def test_a_stale_result_is_dropped(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    analysis.show_earlier_reviews.set(True)
    real = earlier.earlier_reviews.near_reviews

    def slow(*a, **k):
        analysis.reset_alert()  # the alert changes while the read blocks
        return real(*a, **k)

    monkeypatch.setattr(earlier.earlier_reviews, "near_reviews", slow)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    assert not _earlier(widgets)


def test_none_found_reports_zero(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch, n=0)
    analysis.show_earlier_reviews.set(True)
    got = []
    earlier.refresh_earlier_reviews(widgets, analysis, app_state, on_result=got.append)
    assert got == [None, 0]


def test_bad_distance_shows_the_error_and_keeps_the_last_value(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    monkeypatch.setattr(earlier, "spawn_in_context", lambda target, args=(): target(*args))
    controls = earlier.build_earlier_controls(widgets, app_state, analysis)
    field = controls.children[1].children[0]
    for bad in ("0", "abc", "51"):
        field.v_model = bad
        assert field.error_messages and analysis.earlier_radius_km.value == 1.0
    field.v_model = "2,5"
    assert not field.error_messages and analysis.earlier_radius_km.value == 2.5


def test_the_style_colours_by_status():
    assert earlier._style({"properties": {"status": "Confirmed"}})["color"] == "#d32f2f"
    assert earlier._style({"properties": {"status": "Need revision"}})["color"] == "#f9a825"
    assert earlier._style({"properties": {"status": "Confirmed"}})["fill"] is False


def _blocking(monkeypatch, widgets, analysis, app_state):
    """A near_reviews whose calls wait for their own Event, in call order."""
    import threading

    gates, started = [], []

    def near(p, set_id, lon, lat, radius_m, **kw):
        gate = threading.Event()
        gates.append(gate)
        started.append(radius_m)
        gate.wait(5)
        return _fc(int(radius_m // 1000))

    monkeypatch.setattr(earlier.earlier_reviews, "near_reviews", near)
    return gates, started


def _run(widgets, analysis, app_state):
    import threading

    th = threading.Thread(target=earlier.refresh_earlier_reviews, args=(widgets, analysis, app_state))
    th.start()
    return th


def _wait_for(cond):
    import time

    for _ in range(500):
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def test_a_load_in_flight_does_not_re_add_the_layer_after_the_switch_goes_off(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    gates, started = _blocking(monkeypatch, widgets, analysis, app_state)
    analysis.show_earlier_reviews.set(True)
    th = _run(widgets, analysis, app_state)
    _wait_for(lambda: gates)
    analysis.show_earlier_reviews.set(False)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    gates[0].set()
    th.join(5)
    assert not _earlier(widgets)


def test_an_older_load_does_not_overwrite_a_newer_one(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    gates, started = _blocking(monkeypatch, widgets, analysis, app_state)
    analysis.show_earlier_reviews.set(True)
    old = _run(widgets, analysis, app_state)          # 1 km
    _wait_for(lambda: len(gates) == 1)
    analysis.earlier_radius_km.set(5)
    new = _run(widgets, analysis, app_state)          # 5 km
    _wait_for(lambda: len(gates) == 2)
    gates[1].set()
    new.join(5)
    gates[0].set()                                    # the 1 km load ends last
    old.join(5)
    (spec,) = _earlier(widgets)
    assert len(spec["data"]["features"]) == 5


def test_the_caption_is_cleared_when_a_refresh_starts(monkeypatch):
    widgets, analysis, app_state, _ = _setup(monkeypatch, n=0)
    got = []
    analysis.show_earlier_reviews.set(True)
    earlier.refresh_earlier_reviews(widgets, analysis, app_state, on_result=got.append)
    assert got == [None, 0]


def test_view_actual_alert_loads_off_the_calling_thread(monkeypatch):
    from unittest.mock import MagicMock

    from gui.tile.analysis import analysis_tile
    from tests.test_analysis_state import _reviewable_alerts

    widgets, analysis, app_state, calls = _setup(monkeypatch)
    alerts = _reviewable_alerts()
    alerts["point"] = alerts.geometry.centroid
    alerts["alert_polygon"] = None
    app_state.alerts_gdf.set(alerts)
    app_state.project = solara.reactive(None)
    monkeypatch.setattr(analysis_tile, "active_set", lambda p: None)
    monkeypatch.setattr(analysis_tile.set_store, "alerts_belong_to", lambda g, s: True)
    monkeypatch.setattr(analysis_tile, "ee", MagicMock())
    spawned = []
    monkeypatch.setattr(earlier, "spawn_in_context", lambda fn, args=(): spawned.append((fn, args)))
    widgets = MagicMock()
    analysis.show_earlier_reviews.set(True)
    app_state.project = SimpleNamespace(value=SimpleNamespace(questions=[]))
    analysis_tile.view_actual_alert(widgets, app_state, analysis, SimpleNamespace(),
                                    SimpleNamespace(v_model=None))
    assert calls == [] and len(spawned) == 1


def test_a_raising_read_on_the_worker_is_logged_and_leaves_the_panel_usable(monkeypatch, caplog):
    widgets, analysis, app_state, _ = _setup(monkeypatch)
    monkeypatch.setattr(earlier, "spawn_in_context", lambda target, args=(): target(*args))

    def boom(*a, **k):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(earlier.earlier_reviews, "near_reviews", boom)
    controls = earlier.build_earlier_controls(widgets, app_state, analysis)
    switch = controls.children[0]
    with caplog.at_level("ERROR", logger="SEPALUI.deforestation_alerts"):
        switch.v_model = True  # runs the spawned refresh, which raises
    assert any("earlier-reviews overlay failed" in r.message and r.exc_info for r in caplog.records)
    assert _earlier(widgets) == []
    # the next refresh still works once the read recovers
    monkeypatch.setattr(earlier.earlier_reviews, "near_reviews", lambda *a, **k: _fc(1))
    earlier.refresh_earlier_reviews(widgets, analysis, app_state)
    assert len(_earlier(widgets)) == 1
