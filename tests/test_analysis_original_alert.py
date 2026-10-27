"""The *Original alert* layer: the outlines of the alert on screen and of the
rest of its saved review group."""

from types import SimpleNamespace

import solara

from gui.tile.analysis import nearby, original_alert
from gui.tile.analysis.pane_layers import ALERT_TOP_Z
from tests._pane_helpers import make_panes
from tests.test_analysis_nearby import _alerts, _project


def _setup(monkeypatch, gdf, position, enabled=True):
    monkeypatch.setattr(nearby, "active_set", lambda p: SimpleNamespace(id="s1", split=None))
    app_state = SimpleNamespace(alerts_gdf=solara.reactive(gdf), actual_alert_id=solara.reactive(position),
                                project=solara.reactive(_project(enabled=enabled)))
    return SimpleNamespace(panes=make_panes()), app_state


def _outlined(widgets):
    spec = widgets.panes.spec(original_alert.ORIGINAL_ID)
    return None if spec is None else [f["properties"]["position"] for f in spec["data"]["features"]]


def test_a_lone_alert_is_outlined_alone(monkeypatch):
    widgets, app_state = _setup(monkeypatch, _alerts(4), 2)
    original_alert.draw_layer(widgets, app_state)
    spec = widgets.panes.spec(original_alert.ORIGINAL_ID)
    assert (spec["view"], spec["group"], spec["name"], spec["z"]) == (
        "all", "alert", "Original alert", ALERT_TOP_Z - 1)
    assert spec["meta"] == {"hideWhileEditing": True, "menuSection": "alerts"}
    assert spec["style"]["fill"] is False
    assert _outlined(widgets) == [2]


def test_a_grouped_alert_brings_its_whole_group(monkeypatch):
    widgets, app_state = _setup(monkeypatch, _alerts(4, groups=[101, 101, None, 101]), 3)
    original_alert.draw_layer(widgets, app_state)
    assert _outlined(widgets) == [0, 1, 3]


def test_grouping_off_outlines_the_alert_only(monkeypatch):
    widgets, app_state = _setup(monkeypatch, _alerts(4, groups=[101, 101, None, 101]), 3, enabled=False)
    original_alert.draw_layer(widgets, app_state)
    assert _outlined(widgets) == [3]


def test_no_alert_takes_the_layer_off(monkeypatch):
    widgets, app_state = _setup(monkeypatch, _alerts(4), 0)
    original_alert.draw_layer(widgets, app_state)
    app_state.actual_alert_id.set(-1)
    original_alert.draw_layer(widgets, app_state)
    assert _outlined(widgets) is None


def test_the_next_alert_clears_it_with_the_alert_layers(monkeypatch):
    widgets, app_state = _setup(monkeypatch, _alerts(4), 1)
    original_alert.draw_layer(widgets, app_state)
    widgets.panes.clear("alert")
    assert _outlined(widgets) is None


def test_opening_an_alert_outlines_it_under_its_box(monkeypatch):
    import geopandas as gpd

    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile
    from gui.tile.analysis.state import AnalysisState
    from tests.test_analysis_vis_layers import _alerts as _table
    from tests.test_analysis_vis_layers import _p

    monkeypatch.setattr(analysis_tile.logger, "exception", lambda *a, **k: None)  # no Earth Engine here
    widgets = SimpleNamespace(panes=make_panes(), imagery_ctx=solara.reactive(None))
    state = AppState()
    state.load_project_state(_p())
    table = _table(2)
    state.alerts_gdf.set(gpd.GeoDataFrame(table, geometry=list(table["bounding_box"]), crs="EPSG:4326"))
    for alert in (0, 1):
        state.actual_alert_id.set(alert)
        analysis_tile.view_actual_alert(widgets, state, AnalysisState(), SimpleNamespace(),
                                        SimpleNamespace(v_model=None))
    assert widgets.panes.ids() == [analysis_tile.ALERT_BB_ID, original_alert.ORIGINAL_ID]
    assert _outlined(widgets) == [1]
