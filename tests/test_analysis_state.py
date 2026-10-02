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


def test_an_alert_change_makes_earlier_tokens_stale():
    s = AnalysisState()
    token = s.alert_token.value
    assert s.is_current(token) and s.is_current(None)
    s.reset_alert()
    assert not s.is_current(token)
    assert s.apply_if_current(token, lambda: s.before_info.set(["late"])) is False
    assert s.before_info.value is None
    assert s.apply_if_current(s.alert_token.value, lambda: s.before_info.set(["now"])) is True
    assert s.before_info.value == ["now"]


def _fake_imagery_widgets():
    import solara

    return SimpleNamespace(
        imagery_ctx=solara.reactive(None),
        imgSelection1=SimpleNamespace(disabled=False),
        imgSelection2=SimpleNamespace(disabled=False),
        map31=None, map32=None,
    )


def test_an_imagery_fetch_for_a_previous_alert_rebuilds_nothing(monkeypatch):
    """Final review B, I2: the S2/Landsat fetches run on threads. One that ends
    after the user moved to the next alert must not list its dates for that
    alert, nor pick (and store) its default before/after images there: the next
    alert's review Save would record them and its download build them."""
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    rebuilt = []
    monkeypatch.setattr(imagery, "_set_all_loading", lambda w: None)
    monkeypatch.setattr(imagery, "create_horizontal_slide_group_v3",
                        lambda data, *a, **k: rebuilt.append(data[0]["source"]))
    monkeypatch.setattr(imagery, "get_sentinel2_dates", lambda a, b: [a, b, a, b])

    def _fetch_while_the_user_moves_on(grid, start, end):
        s.reset_alert()  # view_actual_alert on the next alert
        return [{"value": "S2_of_A", "source": "Sentinel 2", "milis": 1, "cloud_cover": 0}]

    monkeypatch.setattr(imagery, "getIndividualS2", _fetch_while_the_user_moves_on)
    monkeypatch.setattr(imagery, "getIndividualLandsat", lambda *a: [])
    monkeypatch.setattr(imagery, "spawn_in_context", lambda fn, args=(): fn(*args))

    imagery.build_imagery(widgets, None, None, s)
    widgets.imagery_ctx.set({"token": s.alert_token.value, "grid": None, "fecha1": "a",
                             "fecha2": "b", "show": [False, True, False]})

    # Planet is built at once, for the alert still current; the S2 fetch (and
    # the Landsat one after it) ended after the alert change and rebuild nothing.
    assert rebuilt == ["Planet NICFI", "Planet NICFI"]


def test_an_image_listed_for_a_previous_alert_is_not_stored():
    """The date buttons keep the alert they were listed for: their default pick
    (or a click on one still showing) stores nothing once another alert is
    current."""
    from gui.tile.analysis import imagery

    s = AnalysisState()
    token = s.alert_token.value
    s.reset_alert()
    info = SimpleNamespace(loading=False, children=[])
    item = {"source": "Sentinel 2", "milis": 1704067200000, "cloud_cover": 12, "value": "S2_x"}
    imagery.image_slider_info_callback(item, info, 0, SimpleNamespace(), s, token=token)
    assert s.before_info.value is None and info.children == []

    widgets = _fake_imagery_widgets()
    widgets.actual_alert_grid = None
    drawn = []
    map_ = SimpleNamespace(addLayer=lambda *a: drawn.append(a))
    planet = {"source": "Planet NICFI", "image_id": "projects/x/nicfi", "milis": 1, "cloud_cover": 0}
    imagery.image_slider_map_callback(planet, map_, 0, widgets, s, None, token=token)
    assert s.before_image.value is None and drawn == []
    assert widgets.imgSelection1.disabled is False and widgets.imgSelection2.disabled is False
