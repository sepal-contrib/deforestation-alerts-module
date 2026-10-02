import ast
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import box

from gui.tile.analysis.edit_tools import wire_defo_layer
from gui.tile.analysis.state import AnalysisState

ROOT = Path(__file__).resolve().parents[1]


class _Map:
    def __init__(self):
        self.added = []

    def add_layer(self, layer):
        self.added.append(layer)


def _gdf():
    return gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326")


def test_two_sessions_do_not_share_state():
    a, b = AnalysisState(), AnalysisState()
    a.prediction_files.set({1: "/x.tif"})
    assert b.prediction_files.value == {}


def test_reset_alert_clears_the_per_alert_values():
    s = AnalysisState()
    s.defo_layer.set(_gdf())
    s.before_info.set(["Planet", 1, 0, "v", "_010124"])
    s.reset_alert()
    assert s.defo_layer.value is None and s.before_info.value is None


def test_a_drawn_layer_lands_on_both_maps_once_per_value():
    s = AnalysisState()
    widgets = SimpleNamespace(map_before=_Map(), map_after=_Map())
    unsubscribe = wire_defo_layer(widgets, s)
    g = _gdf()
    s.defo_layer.set(g)
    s.defo_layer.set(g)  # the same object again: not a change
    s.defo_layer.set(None)  # cleared: nothing is drawn
    assert len(widgets.map_before.added) == 1 and len(widgets.map_after.added) == 1
    assert widgets.map_before.added[0].name == "Defo Layer"
    unsubscribe()
    s.defo_layer.set(_gdf())
    assert len(widgets.map_before.added) == 1


def test_nothing_under_analysis_imports_traitlets_or_pysepal_model():
    offenders = []
    for py in sorted((ROOT / "gui" / "tile" / "analysis").rglob("*.py")):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            if any(m == "traitlets" or m.startswith("traitlets.") or m in ("pysepal.model", "pysepal.model.Model")
                   for m in mods):
                offenders.append(py.name)
    assert offenders == []
    assert not (ROOT / "deforestation_alerts" / "model" / "analysis_model.py").exists()


def test_a_picked_image_and_its_info_land_in_the_state():
    from gui.tile.analysis import imagery

    s = AnalysisState()
    info = SimpleNamespace(loading=False, children=[])
    item = {"source": "Sentinel 2", "milis": 1704067200000, "cloud_cover": 12, "value": "S2_x"}
    imagery.image_slider_info_callback(item, info, 1, SimpleNamespace(), s)
    assert s.after_info.value == ["Sentinel 2", 1704067200000, 12, "S2_x", "_010124"]
    assert s.before_info.value is None


def test_an_alert_change_forgets_the_previous_alerts_choices():
    """The chosen images, their info and the CNN predictions belong to one
    alert: ``view_actual_alert`` resets them before the panes choose again."""
    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile

    app_state = AppState()
    alerts = _gdf()
    alerts["status"] = ["Not reviewed"]
    alerts["alert_sources"] = ["GLAD-L"]
    alerts["alert_date_min"] = [2026.010]
    alerts["alert_date_max"] = [2026.020]
    app_state.alerts_gdf.set(alerts)
    app_state.actual_alert_id.set(0)
    s = AnalysisState()
    s.before_image.set(object())
    s.after_info.set(["Planet NICFI", 1, None, "v", "_010124"])
    s.prediction_files.set({1: "/m1.tif", 2: "/m2.tif"})
    widgets = SimpleNamespace(selected_alert_info=SimpleNamespace(children=[]))

    analysis_tile.view_actual_alert(widgets, app_state, s, SimpleNamespace(), SimpleNamespace(v_model=None))

    assert s.before_image.value is None and s.after_info.value is None
    assert s.prediction_files.value == {}
