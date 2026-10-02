from datetime import date, datetime
from types import SimpleNamespace

from deforestation_alerts.project.models import AoiRecord, FileVisLayer, GeeVisLayer, NicfiVisLayer, Period, Project
from gui.store.state_manager import AppState
from gui.tile.analysis import vis_sync


def _p(*layers):
    p = Project(name="a", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    p.vis_layers = {l.name: l for l in layers}
    return p


def test_wanted_and_plan():
    shown = GeeVisLayer(name="s2", asset="x", bands=["B4", "B3", "B2"], show_in_analysis=True)
    hidden = FileVisLayer(name="slope", path="/x.tif", show_in_analysis=False)
    wanted = vis_sync.wanted(_p(shown, hidden))
    assert set(wanted) == {"vis:s2"}
    build, remove = vis_sync.plan({"vis:old": "sig"}, wanted)
    assert build == ["vis:s2"] and remove == ["vis:old"]
    assert vis_sync.plan(wanted, wanted) == ([], [])


def test_a_period_month_nicfi_layer_is_rebuilt_when_the_active_period_changes():
    p = _p(NicfiVisLayer(name="planet", show_in_analysis=True),
           NicfiVisLayer(name="fixed", month="2024-01", show_in_analysis=True))
    p.periods = {"p1": Period(id="p1", start=date(2024, 1, 1), end=date(2024, 3, 31)),
                 "p2": Period(id="p2", start=date(2024, 4, 1), end=date(2024, 6, 30))}
    p.active_period = "p1"
    before = vis_sync.wanted(p)
    p.active_period = "p2"
    after = vis_sync.wanted(p)
    assert vis_sync.plan(before, after) == (["vis:planet"], [])  # the fixed-month layer stays


def _maps(removed):
    return SimpleNamespace(
        map_before=SimpleNamespace(name="before", remove_layer=lambda k, none_ok=False: removed.append(("before", k))),
        map_after=SimpleNamespace(name="after", remove_layer=lambda k, none_ok=False: removed.append(("after", k))))


def test_the_sync_adds_switched_off_layers_to_both_maps_and_removes_them(monkeypatch):
    built, removed = [], []
    monkeypatch.setattr(vis_sync, "builder", lambda map_, layer, key, **kw:
                        (lambda: built.append((map_.name, key, kw["shown"]))))
    widgets = _maps(removed)
    state = AppState()
    layer = GeeVisLayer(name="s2", asset="x", bands=["B4"], show_in_analysis=True)
    state.load_project_state(_p(layer))
    unsubscribe = vis_sync.wire(widgets, state, spawn=lambda target, args=(): target(*args))
    assert sorted(built) == [("after", "vis:s2", False), ("before", "vis:s2", False)]
    removed.clear()  # a build first clears its own key from each map
    state.update_project("a", lambda p: setattr(p, "vis_layers", {}))
    assert sorted(removed) == [("after", "vis:s2"), ("before", "vis:s2")]
    unsubscribe()
    built.clear()
    state.update_project("a", lambda p: setattr(p, "vis_layers", {"s2": layer}))
    assert built == []  # unsubscribed


def _toasts(monkeypatch):
    """A real bus for the run thread, and a spy on its error toasts (the bus
    keeps only the latest error toast)."""
    from pysepal.solara.notifications.bus import NotificationBus
    from pysepal.solara.notifications.notifier import Notifier

    bus, errors = NotificationBus(), []
    real_error = Notifier.error

    def spy(self, message, **kw):
        errors.append(message)
        real_error(self, message, **kw)

    monkeypatch.setattr(Notifier, "error", spy)
    monkeypatch.setattr(vis_sync, "current_notifier", lambda: Notifier(bus))
    return bus, errors


def test_a_failing_build_does_not_stop_the_other_layers_and_is_toasted_once(monkeypatch, caplog):
    built, removed, tried = [], [], []
    bus, errors = _toasts(monkeypatch)

    def builder(map_, layer, key, **kw):
        def build():
            tried.append((map_.name, key))
            if layer.name == "bad":
                raise FileNotFoundError("The file /nope.tif no longer exists.")
            built.append((map_.name, key))
        return build

    monkeypatch.setattr(vis_sync, "builder", builder)
    state = AppState()
    bad = FileVisLayer(name="bad", path="/nope.tif", show_in_analysis=True)
    state.load_project_state(_p(bad, GeeVisLayer(name="s2", asset="x", bands=["B4"], show_in_analysis=True)))
    unsubscribe = vis_sync.wire(_maps(removed), state, spawn=lambda target, args=(): target(*args))
    assert sorted(built) == [("after", "vis:s2"), ("before", "vis:s2")]
    assert "no longer exists" in caplog.text
    assert tried.count(("before", "vis:bad")) + tried.count(("after", "vis:bad")) == 1  # one map tried
    assert {("before", "vis:bad"), ("after", "vis:bad")} <= set(removed)  # nothing half-built stays
    assert errors == ["The layer bad could not be added to the Analysis maps: The file /nope.tif no longer exists."]
    assert sum(t.count for t in bus.toasts.value if t.message == errors[0]) == 1

    for step in range(3):  # every alert step saves the project: no retry, no second toast
        state.update_project("a", lambda p, step=step: setattr(p, "updated_at", datetime.now()))
    assert len(errors) == 1 and len(tried) == 3

    state.load_project_state(state.project.value.model_copy())  # opened again: one more try
    assert len(errors) == 2 and len(tried) == 4
    unsubscribe()


def test_the_visualization_layers_survive_an_alert_change(monkeypatch):
    """The per-alert view clears the maps on every alert change; the vis
    layers must stay on both (real SepalMaps: ``find_layer`` by key)."""
    import pandas as pd
    from ipyleaflet import TileLayer
    from pysepal.mapping import SepalMap
    from shapely.geometry import Point, box

    from gui.tile.analysis import analysis_tile
    from gui.tile.analysis.state import AnalysisState

    def builder(map_, layer, key, **kw):
        return lambda: map_.add_layer(TileLayer(url="https://example.invalid/{z}/{x}/{y}.png", name=layer.name,
                                                visible=kw["shown"]), key=key)

    monkeypatch.setattr(vis_sync, "builder", builder)
    monkeypatch.setattr(analysis_tile.logger, "exception", lambda *a, **k: None)  # no Earth Engine here
    stub = SimpleNamespace(hide=lambda: None)
    widgets = SimpleNamespace(map_before=SepalMap(gee=False), map_after=SepalMap(gee=False),
                              selected_alert_info=SimpleNamespace(children=[]), draw_before=stub, draw_after=stub,
                              set_synced_view=lambda *a: None)
    state = AppState()
    state.load_project_state(_p(GeeVisLayer(name="s2", asset="x", bands=["B4"], show_in_analysis=True)))
    unsubscribe = vis_sync.wire(widgets, state, spawn=lambda target, args=(): target(*args))
    state.alerts_gdf.set(pd.DataFrame({
        "alert_date_min": [2026.001] * 2, "alert_date_max": [2026.010] * 2, "alert_sources": ["GLAD-L"] * 2,
        "status": ["Not reviewed"] * 2, "alert_polygon": [None] * 2,
        "bounding_box": [box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01)], "point": [Point(0, 0), Point(1, 1)]}))
    for alert in (0, 1):
        state.actual_alert_id.set(alert)
        analysis_tile.view_actual_alert(widgets, state, AnalysisState(), SimpleNamespace(),
                                        SimpleNamespace(v_model=None))
    unsubscribe()
    for map_ in (widgets.map_before, widgets.map_after):
        vis = map_.find_layer("vis:s2", none_ok=True)
        assert vis is not None and vis.visible is False
        assert [lyr.name for lyr in map_.layers if not lyr.base].count("Alert BB") == 1  # the old alert's went


def test_a_removal_that_runs_after_a_later_re_add_leaves_the_layer(monkeypatch):
    """Runs may take the build lock out of order: a removal whose key a later
    sync wants again must not take the re-added layer off the maps."""
    built, removed, pending = [], [], []
    monkeypatch.setattr(vis_sync, "builder", lambda map_, layer, key, **kw:
                        (lambda: built.append((map_.name, key))))
    state = AppState()
    layer = GeeVisLayer(name="s2", asset="x", bands=["B4"], show_in_analysis=True)
    state.load_project_state(_p(layer))
    unsubscribe = vis_sync.wire(_maps(removed), state, spawn=lambda target, args=(): pending.append(target))
    pending.pop()()  # the first build
    state.update_project("a", lambda p: setattr(p, "vis_layers", {}))
    state.update_project("a", lambda p: setattr(p, "vis_layers", {"s2": layer}))
    unsubscribe()
    assert len(pending) == 2
    built.clear()
    removed.clear()
    pending[1]()  # the re-add runs first ...
    pending[0]()  # ... then the removal
    assert sorted(built) == [("after", "vis:s2"), ("before", "vis:s2")]
    assert sorted(removed) == [("after", "vis:s2"), ("before", "vis:s2")]  # only the build's own clear


def test_quick_successive_edits_never_build_at_once(monkeypatch):
    import threading
    import time

    running, peaks, threads = [0], [], []

    def slow_builder(map_, layer, key, **kw):
        def build():
            running[0] += 1
            peaks.append(running[0])
            time.sleep(0.05)
            running[0] -= 1
        return build

    def spawn(target, args=()):
        th = threading.Thread(target=target, args=args)
        threads.append(th)
        th.start()

    monkeypatch.setattr(vis_sync, "builder", slow_builder)
    maps = SimpleNamespace(map_before=SimpleNamespace(remove_layer=lambda k, none_ok=False: None),
                           map_after=SimpleNamespace(remove_layer=lambda k, none_ok=False: None))
    state = AppState()
    state.load_project_state(_p(GeeVisLayer(name="s2", asset="x", bands=["B4"], max=1, show_in_analysis=True)))
    unsubscribe = vis_sync.wire(maps, state, spawn=spawn)
    for top in (2, 3):  # two edits of the layer, right after each other
        state.update_project("a", lambda p, top=top: setattr(p, "vis_layers", {
            "s2": GeeVisLayer(name="s2", asset="x", bands=["B4"], max=top, show_in_analysis=True)}))
    for th in threads:
        th.join(5)
    unsubscribe()
    assert peaks and max(peaks) == 1  # one build at a time
