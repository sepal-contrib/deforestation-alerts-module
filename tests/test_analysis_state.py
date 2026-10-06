import ast
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
from shapely.geometry import box

from deforestation_alerts.scripts import scene_index
from deforestation_alerts.scripts.scene_index import SOURCE_S2
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
    # The save stamp is one of them: it says a save landed on THIS alert, so
    # an untouched alert must not inherit the previous one's (spec §4.4).
    s.last_save_time.set(1791030720.0)
    s.reset_alert()
    assert s.defo_layer.value is None and s.before_info.value is None
    assert s.last_save_time.value == 0.0


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


def test_the_timeline_does_not_auto_pick_an_image_from_inside_the_alert_window():
    """What the retired selectors enforced by only offering out-of-window
    images, the timeline enforces in ``default_pick`` instead -- because a long
    GLAD window can leave a reviewer no other choice than an in-window image,
    so hiding them was never right. The rows are still offered: the same two
    scenes are picked the moment the window no longer covers them.
    """
    from deforestation_alerts.scripts import scene_query

    sel = _selection([(SOURCE_S2, "2026-06-10", 1.0), (SOURCE_S2, "2026-07-01", 1.0)])
    assert scene_query.default_pick(sel, date(2026, 5, 3), date(2026, 8, 23)) == (None, None)
    assert len(sel) == 2  # offered, just not chosen for the reviewer
    assert not sel["hidden"].any()  # and not faded either: only the pick refuses
    # the proof that the refusal is the window and nothing else about the rows
    narrow = scene_query.default_pick(sel, date(2026, 6, 20), date(2026, 6, 21))
    assert narrow == (scene_index.row_id(SOURCE_S2, date(2026, 6, 10)),
                      scene_index.row_id(SOURCE_S2, date(2026, 7, 1)))


def test_an_alert_change_forgets_the_previous_alerts_choices(caplog):
    """The chosen images, their info and the CNN predictions belong to one
    alert: ``view_actual_alert`` resets them before the panes choose again."""
    import logging

    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile

    app_state = AppState()
    alerts = _gdf()
    alerts["status"] = ["Not reviewed"]
    alerts["alert_sources"] = ["GLAD-L"]
    alerts["alert_date_min"] = [2026.010]
    alerts["alert_date_max"] = [2026.020]
    alerts["bounding_box"] = alerts.geometry
    app_state.alerts_gdf.set(alerts)
    app_state.actual_alert_id.set(0)
    s = AnalysisState()
    s.before_image.set(object())
    s.after_info.set(["Planet NICFI", 1, None, "v", "_010124"])
    s.prediction_files.set({1: "/m1.tif", 2: "/m2.tif"})
    widgets = SimpleNamespace()

    with caplog.at_level(logging.ERROR):
        analysis_tile.view_actual_alert(widgets, app_state, s, SimpleNamespace(), SimpleNamespace(v_model=None))

    assert s.before_image.value is None and s.after_info.value is None
    assert s.prediction_files.value == {}
    # The stand-in widgets carry no maps, so the map work after the reset fails
    # on them, and only there (not on the alert's data): one logged error.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Analysis: view_actual_alert map update failed"]
    assert errors[0].exc_info[0] is AttributeError and "draw_before" in str(errors[0].exc_info[1])


class _AlertLayer:
    def __init__(self, base=False, key=""):
        self.base, self.key = base, key


class _AlertMap:
    """A map with a basemap and one alert layer on it, for the clearing."""

    def __init__(self):
        self.layers = (_AlertLayer(base=True), _AlertLayer(key="Alert BB"))


class _Draw:
    def __init__(self):
        self.on_map = True

    def hide(self):
        self.on_map = False

    def clear(self):
        pass


def _view_widgets():
    import solara

    return SimpleNamespace(
        imagery_ctx=solara.reactive({"alert_id": 0, "token": 0, "bbox": None}),
        map_before=_AlertMap(), map_after=_AlertMap(),
        draw_before=_Draw(), draw_after=_Draw(),
        actual_bb=object(), actual_alert_grid=object(),
    )


def _reviewable_alerts():
    alerts = _gdf()
    alerts["status"] = ["Not reviewed"]
    alerts["alert_sources"] = ["GLAD-L"]
    alerts["alert_date_min"] = [2026.010]
    alerts["alert_date_max"] = [2026.020]
    alerts["set_id"] = ["x"]
    alerts["bounding_box"] = alerts.geometry
    return alerts


def _with_work_in_flight(app_state):
    """An Analysis state as it is while a CNN run or an export is running on
    the alert on screen. Returns ``(state, its token)``."""
    s = AnalysisState()
    s.prediction_files.set({1: "/m1.tif"})
    s.verdict.set("Confirmed")
    s.defo_layer.set(_gdf())
    return s, s.alert_token.value


def test_losing_the_alert_selection_invalidates_the_analysis_state():
    """A project switch drops the alerts and selects nothing until the new
    ones arrive (``AppState._install``), and a context with no alerts never
    brings any.

    The guards of ``view_actual_alert`` returned before the reset, so the
    previous alert's token stayed CURRENT: a CNN run or an export still in
    flight passed ``is_current`` and applied its result onto a panel whose
    alert is gone, the imagery context still described that alert (so the
    timeline kept offering its lanes), and the ee bbox and covering grid it
    left on ``widgets`` were still what an export would be built from.
    """
    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile

    app_state = AppState()
    app_state.alerts_gdf.set(_reviewable_alerts())
    app_state.actual_alert_id.set(0)
    s, token = _with_work_in_flight(app_state)
    widgets = _view_widgets()

    # the project is switched: its alerts go, and nothing is selected
    app_state.alerts_gdf.set(None)
    app_state.actual_alert_id.set(-1)
    analysis_tile.view_actual_alert(
        widgets, app_state, s, SimpleNamespace(), SimpleNamespace(v_model=None)
    )

    assert not s.is_current(token), "work started on the gone alert is still current"
    assert s.prediction_files.value == {}
    assert s.verdict.value is None and s.defo_layer.value is None
    assert widgets.imagery_ctx.value is None, "the timeline still describes the gone alert"
    assert widgets.actual_bb is None and widgets.actual_alert_grid is None
    assert widgets.draw_before.on_map is False and widgets.draw_after.on_map is False
    for a_map in (widgets.map_before, widgets.map_after):
        assert [lyr.base for lyr in a_map.layers] == [True], "the alert's layers are still on"


def test_alerts_of_another_set_invalidate_the_analysis_state_too(monkeypatch):
    """Same hole on the other identity change: the active set is switched, and
    until that set's rows are published the alerts on screen are the previous
    set's. The panel keeping its numbers for those milliseconds is deliberate;
    keeping the token, the geometry and the export eligibility of an alert
    that belongs to another set is not.
    """
    from gui.store.state_manager import AppState
    from gui.tile.analysis import analysis_tile

    app_state = AppState()
    app_state.alerts_gdf.set(_reviewable_alerts())
    app_state.actual_alert_id.set(0)
    s, token = _with_work_in_flight(app_state)
    widgets = _view_widgets()

    monkeypatch.setattr(analysis_tile, "active_set", lambda p: SimpleNamespace(id="other"))
    monkeypatch.setattr(analysis_tile.set_store, "alerts_belong_to", lambda gdf, s_: False)

    analysis_tile.view_actual_alert(
        widgets, app_state, s, SimpleNamespace(), SimpleNamespace(v_model=None)
    )

    assert not s.is_current(token)
    assert s.prediction_files.value == {} and s.defo_layer.value is None
    assert widgets.imagery_ctx.value is None


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

    return SimpleNamespace(imagery_ctx=solara.reactive(None), map31=None, map32=None)


def _selection(rows):
    """A timeline selection frame for ``rows``, through the real query."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import box as _box

    from deforestation_alerts.scripts import scene_query

    tiles = gpd.GeoDataFrame({"tile": ["W"]},
                             geometry=gpd.GeoSeries([_box(0, 0, 2, 2)], crs=scene_index.WGS84),
                             crs=scene_index.WGS84)
    scenes = pd.DataFrame(
        [(src, pd.Timestamp(d), cloud, "W", f"k{i}") for i, (src, d, cloud) in enumerate(rows)],
        columns=list(scene_index.SCENE_COLUMNS),
    ).astype({"date": "datetime64[ns]"})
    return scene_query.scenes_for(scene_index.SceneTables(scenes=scenes, tiles=tiles),
                                  _box(0.5, 0.5, 1.5, 1.5), date(2026, 1, 1), date(2026, 12, 31),
                                  max_cloud=40.0)


def test_an_image_chosen_for_a_previous_alert_is_not_drawn():
    """The token gate the old fetch threads needed still holds, one layer
    further in: the timeline publishes into ``before_info`` and the pane
    rebuilds off that publish, so a rebuild that lands after the next alert
    change must store nothing and draw nothing.
    """
    from gui.tile.analysis import imagery

    s = AnalysisState()
    token = s.alert_token.value
    s.reset_alert()
    assert not s.is_current(token)
    assert s.apply_if_current(token, lambda: s.before_image.set("X")) is False
    assert s.before_image.value is None

    widgets = _fake_imagery_widgets()
    widgets.actual_alert_grid = None
    drawn = []
    map_ = SimpleNamespace(addLayer=lambda *a: drawn.append(a))
    item = {"source": "Planet NICFI", "keys": ["projects/x/nicfi"]}
    imagery.image_slider_map_callback(item, map_, 0, widgets, s, None, token=token)
    assert s.before_image.value is None and drawn == []


def test_a_replaced_pick_in_the_same_lane_draws_nothing(monkeypatch):
    """Two picks in the same lane, for the SAME alert, share the alert token,
    so the token cannot tell them apart: the reviewer picks A, then B before
    A's rebuild finishes, and A -- landing last -- stored its image and
    redrew the pane, leaving the map showing A under a readout that says B.
    Each lane therefore carries its own request generation.
    """
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    widgets.map_before = SimpleNamespace(side="before")
    widgets.map_after = SimpleNamespace(side="after")
    held, drawn = [], []
    monkeypatch.setattr(imagery, "spawn_in_context", lambda fn, args=(): held.append((fn, args)))

    def _rebuild(item, map_, pane, widgets_, analysis_, ctx_, token=None, fresh=None):
        """``image_slider_map_callback``'s signature, with the lane's own
        freshness gate: ``spawn_in_context`` forwards positional args only."""
        if analysis_.is_current(token) and (fresh is None or fresh()):
            drawn.append(item["keys"])

    monkeypatch.setattr(imagery, "image_slider_map_callback", _rebuild)
    imagery.wire_panes(widgets, s, None)

    s.before_keys.set(["A"])
    s.before_info.set(["Sentinel 2", 1, "1.00", "A ", "_010126"])
    s.before_keys.set(["B"])
    s.before_info.set(["Sentinel 2", 2, "1.00", "B ", "_020126"])
    assert len(held) == 2

    held[1][0](*held[1][1])  # B lands first...
    held[0][0](*held[0][1])  # ...and A's slow rebuild only now

    assert drawn == [["B"]], "the replaced pick redrew the pane over the current one"


def test_a_pick_marks_its_lane_pending_so_nothing_captures_a_mixture(monkeypatch):
    """A pick publishes its metadata at the click and its ee.Image only when
    the rebuild lands. In between the lane holds the new pick's dates beside
    the previous pick's image, and a Run or an export captures that mixture --
    a composite named after one pick, built from the other's pixels. The lane
    is marked pending instead, which both of those refuse.
    """
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    widgets.map_before = SimpleNamespace(side="before")
    widgets.map_after = SimpleNamespace(side="after")
    monkeypatch.setattr(imagery, "spawn_in_context", lambda fn, args=(): None)
    imagery.wire_panes(widgets, s, None)
    s.before_image.set(object())  # what the previous pick drew

    s.before_keys.set(["B"])
    s.before_info.set(["Sentinel 2", 2, "1.00", "B ", "_020126"])

    assert s.before_image.value is None, "the new pick's dates beside the old image"


def test_an_image_for_a_replaced_pick_is_not_stored_or_drawn():
    """And the gate holds inside the rebuild itself, which is where the image
    is published and the layers are added."""
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    widgets.actual_alert_grid = None
    drawn = []
    map_ = SimpleNamespace(addLayer=lambda *a: drawn.append(a))
    item = {"source": "Planet NICFI", "keys": ["projects/x/nicfi"]}

    imagery.image_slider_map_callback(item, map_, 0, widgets, s, None,
                                      token=s.alert_token.value, fresh=lambda: False)

    assert s.before_image.value is None and drawn == []


def test_a_pick_published_for_a_previous_alert_rebuilds_nothing(monkeypatch):
    """``wire_panes`` reads the token on the publishing thread, at the publish,
    not inside the worker: a pick made for one alert cannot be rebuilt against
    the alert that is current by the time the worker runs.
    """
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    # distinguishable, because two empty SimpleNamespaces compare EQUAL and a
    # swapped pair would then slip past the assertion at the end
    widgets.map_before = SimpleNamespace(side="before")
    widgets.map_after = SimpleNamespace(side="after")
    held, rebuilt = [], []
    monkeypatch.setattr(imagery, "spawn_in_context", lambda fn, args=(): held.append((fn, args)))

    def _rebuild(item, map_, pane, widgets_, analysis_, ctx_, token=None, fresh=None):
        """``image_slider_map_callback``'s own signature, so the token is read
        from the position ``wire_panes`` actually passes it in --
        ``spawn_in_context`` forwards positional args only."""
        if analysis_.is_current(token):
            rebuilt.append((item, token, map_))

    monkeypatch.setattr(imagery, "image_slider_map_callback", _rebuild)
    imagery.wire_panes(widgets, s, None)

    s.before_keys.set(["k0"])
    s.before_info.set(["Sentinel 2", 1, "1.00", "x ", "_010126"])
    assert len(held) == 1
    s.reset_alert()  # the user moves on before the worker is let go
    fn, args = held[0]
    fn(*args)
    assert rebuilt == []

    # and the same publish for the alert that *is* current rebuilds
    s.before_keys.set(["k1"])
    s.before_info.set(["Sentinel 2", 2, "1.00", "y ", "_020126"])
    fn, args = held[-1]
    fn(*args)
    assert [item["keys"] for item, _, _ in rebuilt] == [["k1"]]

    # ...and each lane reaches the pane it belongs to. The before lane must
    # draw on the LEFT map and the after lane on the RIGHT one: a swapped pair
    # puts every pick on the wrong side of the workspace, which is plainly
    # wrong on screen and which nothing else in the suite notices.
    s.after_keys.set(["k2"])
    s.after_info.set(["Landsat", 3, "1.00", "z ", "_030126"])
    fn, args = held[-1]
    fn(*args)
    assert [m for _, _, m in rebuilt] == [widgets.map_before, widgets.map_after]


def test_reset_alert_clears_the_selection_and_its_granule_keys():
    analysis = AnalysisState()
    analysis.before_info.set(["Sentinel 2", 1, "1.00", "x ", "_010126"])
    analysis.before_keys.set(["k"])
    analysis.reset_alert()
    assert analysis.before_info.value is None and analysis.before_keys.value == []


def test_nothing_under_analysis_holds_a_retired_planet_reactive():
    analysis = AnalysisState()
    assert not hasattr(analysis, "before_planet") and not hasattr(analysis, "after_planet")


def test_an_image_layer_drawn_after_an_alert_change_is_not_added(monkeypatch):
    """Final re-review, Minor 3: ``addLayer`` blocks on getMapId after the
    image is stored. An alert change meanwhile clears the maps for the next
    alert; the late layer must not land on them (``LockedMap.gated``)."""
    from ipyleaflet import TileLayer

    from gui.tile.analysis import imagery
    from gui.tile.analysis.widgets import LockedMap

    s = AnalysisState()
    token = s.alert_token.value
    map_ = LockedMap(gee=False)
    base = len(map_.layers)
    monkeypatch.setattr(imagery, "ee", SimpleNamespace(Image=lambda _id: SimpleNamespace(clip=lambda g: "img")))

    def _add_ee_layer(img, vis, name, shown):  # getMapId blocks, the user moves on
        if name == "False Color":
            s.reset_alert()
        map_.add_layer(TileLayer(url="https://example.invalid/{z}/{x}/{y}.png", name=name))

    map_.addLayer = _add_ee_layer
    widgets = _fake_imagery_widgets()
    widgets.actual_alert_grid = None
    planet = {"source": "Planet NICFI", "keys": ["projects/x/nicfi"]}
    imagery.image_slider_map_callback(planet, map_, 0, widgets, s, None, token=token)

    assert [lyr.name for lyr in map_.layers[base:]] == ["True Color"]
    map_.add_layer(TileLayer(url="https://example.invalid/{z}/{x}/{y}.png", name="later"))
    assert map_.layers[-1].name == "later"  # outside the callback, adds are not gated


def test_a_rebuild_that_raises_reaches_the_log_and_the_reviewer(monkeypatch, caplog):
    """The hole Task 12 exists to close, pinned behaviourally.

    The pane rebuild runs on a bare daemon thread (``spawn_in_context``). An
    unhandled exception there reaches ``threading.excepthook`` ->
    ``sys.stderr``, which under Voila nobody ever sees, so the reviewer is left
    with a pane that simply did not change. Removing the worker's
    ``try/except`` + ``logger.exception`` + ``current_notifier().error(...)``
    passed the whole suite, because nothing asserted either destination:
    ``imagery.py`` is not in ``test_feedback_notifications.RAW_LAYOUTS``
    either (it owns no ``tracked_job`` -- a pane redraw is not a tracked job).
    """
    import logging

    from gui.i18n import t
    from gui.tile.analysis import imagery

    s = AnalysisState()
    widgets = _fake_imagery_widgets()
    widgets.map_before = SimpleNamespace(side="before")
    widgets.map_after = SimpleNamespace(side="after")
    held, toasted = [], []
    monkeypatch.setattr(imagery, "spawn_in_context", lambda fn, args=(): held.append((fn, args)))
    monkeypatch.setattr(imagery, "current_notifier",
                        lambda: SimpleNamespace(error=lambda message: toasted.append(message)))

    def _boom(*_a, **_k):
        raise RuntimeError("getMapId refused")

    monkeypatch.setattr(imagery, "image_slider_map_callback", _boom)
    imagery.wire_panes(widgets, s, None)

    s.before_keys.set(["k0"])
    s.before_info.set([SOURCE_S2, 1, "1.00", "x ", "_010126"])
    assert len(held) == 1
    fn, args = held[0]
    with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts.analysis"):
        fn(*args)  # must not raise out of the worker body

    # the server log: one ERROR record carrying the traceback
    records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(records) == 1 and records[0].exc_info is not None
    assert "before" in records[0].getMessage()

    # and the reviewer: the rendered message, not the bare key
    assert len(toasted) == 1
    assert toasted[0] == t("analysis.timeline.image_failed_before", error="getMapId refused")
    assert "analysis.timeline.image_failed_before" not in toasted[0]
    assert "getMapId refused" in toasted[0]

    # the after lane says *after*, so a reviewer knows which pane went dead
    s.after_keys.set(["k1"])
    s.after_info.set([SOURCE_S2, 2, "1.00", "y ", "_020126"])
    fn, args = held[-1]
    fn(*args)
    assert len(toasted) == 2
    assert toasted[1] == t("analysis.timeline.image_failed_after", error="getMapId refused")
    assert toasted[1] != toasted[0]


def test_the_verdict_and_the_dirty_flag_are_per_alert_state():
    from gui.tile.analysis.state import AnalysisState

    s = AnalysisState()
    assert s.verdict.value is None and s.dirty.value is False
    s.verdict.set("Confirmed")
    s.dirty.set(True)
    s.prediction_shapes.set({1: (2, 3.9)})
    s.reset_alert()
    assert s.verdict.value is None
    assert s.dirty.value is False
    assert s.prediction_shapes.value == {}


def test_capture_alert_takes_the_verdict_from_the_state():
    from types import SimpleNamespace

    from gui.store.state_manager import AppState
    from gui.tile.analysis.state import AnalysisState, capture_alert

    app_state = AppState()
    analysis = AnalysisState()
    analysis.verdict.set("False Positive")
    widgets = SimpleNamespace(driver_combobox=SimpleNamespace(v_model=["mining"]))
    alert = capture_alert(analysis, widgets, app_state)
    assert alert.status == "False Positive"
    assert alert.drivers == ["mining"]


def test_touch_marks_the_alert_dirty():
    from gui.tile.analysis.state import AnalysisState, touch

    s = AnalysisState()
    touch(s)
    assert s.dirty.value is True
