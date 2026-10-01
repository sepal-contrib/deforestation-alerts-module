"""Overview on the active alert set (spec §9.2, §9.3), rendered.

``OverviewTile`` is rendered with ``reacton.render`` against a real
``AppState``/``Project``, a real ``SepalMap(gee=False)`` and a real
``MapLayers``. The registry's builds are queued instead of started, so each
test runs them (``ov.run_jobs``) as the worker thread would. The tile build
(``set_store.ensure_pmtiles``) and the PMTiles layer (``add_set_pmtiles``) are
stand-ins: tests/test_set_store.py and tests/test_pmtiles.py cover them.

No Earth Engine, no browser.
"""

from datetime import date, datetime
from types import SimpleNamespace

import geopandas as gpd
import ipyleaflet
import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.mapping import SepalMap
from pysepal.solara.notifications import NotificationProvider
from pysepal.solara.notifications.bus import get_current_bus
from shapely.geometry import box

from deforestation_alerts.engine.fingerprint import fingerprint, harm_key, set_inputs
from deforestation_alerts.message import cm
from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Artifact,
    Period,
    Project,
    add_predefined_source,
)
from deforestation_alerts.project.models import set_id as make_set_id
from gui.i18n import t
from gui.scripts import map_layers, set_store
from gui.scripts.map_layers import MapLayers
from gui.scripts.pmtiles_map import STATUS_COLORS
from gui.store.state_manager import AppState
from gui.tile import overview_tile

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
SET_DIR = f"periods/{JAN.id}/sets/glad-l"
KEY = "overview:set"


def _alerts(set_id=None):
    """Four alerts, one per status. ``set_id``: the column the engine writes
    (integrate.py), naming the set the alerts belong to."""
    gdf = gpd.GeoDataFrame(
        {"alert_id": [0, 1, 2, 3],
         "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
         "area_ha": [1.5, 0.25, 2.0, 0.75]},
        geometry=[box(0, 0, 0.01, 0.01), box(1, 1, 1.01, 1.01), box(2, 2, 2.01, 2.01), box(3, 3, 3.01, 3.01)],
        crs="EPSG:4326",
    )
    if set_id is not None:
        gdf["set_id"] = set_id
    return gdf


def _project(tmp_path, name="d", *, current=True):
    """A project with one GLAD-L set for January, its store on disk.

    ``current``: its harmonized input is recorded, so the set's fingerprint
    matches (Status.CURRENT); otherwise it is out of date (Status.STALE).
    """
    now = datetime.now()
    p = Project(name=name, created_at=now, updated_at=now)
    add_predefined_source(p, "GLAD-L")
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    p.artifacts[harm_key(JAN.id, "GLAD-L")] = Artifact(
        key=harm_key(JAN.id, "GLAD-L"), kind="harmonized", rel_path="h.tif", fingerprint="x", inputs={},
        content_id="h1", created_at=now)
    params = AlertSetParams()
    s = AlertSet(id=make_set_id(JAN.id, ["GLAD-L"]), period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                 params=params, store_rel_path=f"{SET_DIR}/alert_db.parquet", combined_rel_path=f"{SET_DIR}/c.tif",
                 footprint_rel_path=f"{SET_DIR}/footprint.tif", n_alerts=4, n_reviewed=3,
                 fingerprint=fingerprint(set_inputs(p, JAN, ["GLAD-L"], params)), created_at=now)
    if not current:
        p.artifacts[harm_key(JAN.id, "GLAD-L")].content_id = "h2"  # re-harmonized since
    p.alert_sets[s.id] = s
    p.active_set = s.id
    store = tmp_path / name / s.store_rel_path
    store.parent.mkdir(parents=True, exist_ok=True)
    _alerts().to_parquet(store)
    return p


def _state(p, gdf, view="overview"):
    state = AppState()
    state.new_project_state(p)
    state.alerts_gdf.set(gdf)
    state.panel_view.set(view)
    return state


@solara.component
def _Harness(app_state, map_, workspace, layers):
    NotificationProvider()  # the tile's use_notifications() needs it rendered first
    overview_tile.OverviewTile(app_state=app_state, map_=map_, workspace=workspace, layers=layers)


@pytest.fixture
def ov(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    ns = SimpleNamespace(jobs=[], ensured=[], drawn=[], pm=tmp_path / "alerts.pmtiles",
                         ensure_error=None, draw_error=None, during_ensure=None)
    # MapLayers' builds are queued, not started: the test runs them.
    monkeypatch.setattr(map_layers, "spawn_in_context", lambda target, args=(), **kw: ns.jobs.append((target, args)))

    def ensure(p, s, workspace):
        ns.ensured.append((p.name, s.id, workspace))
        if ns.during_ensure is not None:
            ns.during_ensure()
        if ns.ensure_error is not None:
            raise ns.ensure_error
        return ns.pm

    def draw(map_, path, *, key, name, faded=False):
        # like pmtiles_map.add_set_pmtiles: a lookup by key, then a keyed add
        ns.drawn.append((path, key, name))
        if ns.draw_error is not None:
            raise ns.draw_error
        map_.remove_layer(key, none_ok=True)
        map_.add_layer(ipyleaflet.TileLayer(name=name), key=key)

    monkeypatch.setattr(set_store, "ensure_pmtiles", ensure)
    monkeypatch.setattr(overview_tile, "add_set_pmtiles", draw)

    def run_jobs():
        while ns.jobs:
            target, args = ns.jobs.pop(0)
            target(*args)

    def render(state, workspace="WS"):
        map_ = SepalMap(gee=False)
        layers = MapLayers(map_)
        _, rc = reacton.render(_Harness(app_state=state, map_=map_, workspace=workspace, layers=layers),
                               handle_error=False)
        ns.bus = get_current_bus()
        return map_, layers, rc

    ns.run_jobs = run_jobs
    ns.render = render
    return ns


def _strings(widget):
    out = []
    for child in getattr(widget, "children", None) or []:
        out.extend([child] if isinstance(child, str) else _strings(child))
    return out


def _texts(rc):
    return _strings(rc.container)


def _summary(rc):
    """The summary table as {label: count}."""
    [table] = rc.find(v.SimpleTable).widgets
    [tbody] = table.children
    return {str(row.children[0].children[0]): int(row.children[1].children[0]) for row in tbody.children}


def _export_cards(rc):
    return [w for w in rc.find(v.Card).widgets
            if any(isinstance(c, v.CardTitle) and c.children == [cm.overview_tile.export_label]
                   for c in w.children)]


def _toasts(ns, kind):
    return [toast.message for toast in ns.bus.toasts.value if toast.type.value == kind]


def _click(map_, lon, lat):
    map_._handle_leaflet_event(None, {"event": "interaction", "type": "click", "coordinates": [lat, lon]}, None)


def _popups(map_):
    return [layer for layer in map_.layers if isinstance(layer, ipyleaflet.Popup)]


# ---------------------------------------------------------------------------
# Summary, set label, stale banner and exports
# ---------------------------------------------------------------------------
def test_the_summary_counts_follow_the_alerts(ov, tmp_path):
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        counts = _summary(rc)
        assert counts == {
            str(cm.overview_tile.total): 4, str(cm.overview_tile.reviewed): 3,
            str(cm.overview_tile.confirmed): 1, str(cm.overview_tile.false_positives): 1,
            str(cm.overview_tile.revision): 1,
        }
        state.alerts_gdf.set(None)
        assert set(_summary(rc).values()) == {0}
    finally:
        rc.close()


def test_the_set_label_and_the_export_card_only_with_alerts(ov, tmp_path):
    p = _project(tmp_path)
    state = _state(p, _alerts())
    map_, layers, rc = ov.render(state)
    try:
        assert t("steps.overview.set_label", period=JAN.id, sources="GLAD-L", n=4, reviewed=3) in _texts(rc)
        assert len(_export_cards(rc)) == 1
        assert t("steps.overview.stale") not in _texts(rc)  # a current set: no banner

        state.alerts_gdf.set(_alerts().iloc[0:0])  # an empty set
        assert _export_cards(rc) == []
        state.alerts_gdf.set(None)
        assert _export_cards(rc) == []

        state.close_project_state()  # no project, no set
        assert _export_cards(rc) == []
        assert not [s for s in _texts(rc) if s.startswith(JAN.id)]
    finally:
        rc.close()


def test_the_export_card_writes_into_the_set_folder_named_by_sources_and_dates(ov, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(overview_tile, "build_export_card", lambda *a: calls.append(a) or v.Card(children=[]))
    alerts = _alerts()
    state = _state(_project(tmp_path), alerts)
    map_, layers, rc = ov.render(state)
    try:
        assert calls == [(alerts, "WS", str(tmp_path / "d" / SET_DIR), ["GLAD-L"], "2026-01-01", "2026-01-31", "d")]
        state.panel_view.set("workflow")  # a re-render with the same inputs keeps the card
        state.update_project("d", lambda p: setattr(p, "look_back_days", 30))
        assert len(calls) == 1
    finally:
        rc.close()


def test_an_out_of_date_set_shows_the_warning_banner(ov, tmp_path):
    state = _state(_project(tmp_path, current=False), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        assert t("steps.overview.stale") in _texts(rc)
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# The set's tiles on the shared map
# ---------------------------------------------------------------------------
def test_the_set_layer_is_drawn_only_while_the_overview_is_open(ov, tmp_path):
    alerts = _alerts()
    state = _state(_project(tmp_path), alerts, view="workflow")
    map_, layers, rc = ov.render(state)
    try:
        ov.run_jobs()
        assert ov.ensured == [] and not layers.is_on(KEY)

        state.panel_view.set("overview")
        assert layers.is_on(KEY)
        ov.run_jobs()
        [(name, sid, workspace)] = ov.ensured
        assert (name, sid) == ("d", make_set_id(JAN.id, ["GLAD-L"])) and workspace == "WS"
        assert ov.drawn == [(ov.pm, KEY, t("steps.overview.layer_name"))]
        assert map_.find_layer(KEY, none_ok=True) is not None

        state.panel_view.set("workflow")
        assert not layers.is_on(KEY) and map_.find_layer(KEY, none_ok=True) is None
        assert _toasts(ov, "warning") == [] and _toasts(ov, "error") == []
    finally:
        rc.close()


def test_a_build_that_finds_the_overview_closed_does_nothing(ov, tmp_path):
    """Ruling #57: leaving Overview before its build runs skips tippecanoe."""
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        state.panel_view.set("workflow")
        ov.run_jobs()
        assert ov.ensured == [] and ov.drawn == []
        assert map_.find_layer(KEY, none_ok=True) is None
    finally:
        rc.close()


@pytest.mark.parametrize("tippecanoe", [True, False])
def test_leaving_while_tippecanoe_runs_draws_nothing(ov, tmp_path, tippecanoe):
    """Ruling #57: a build whose view closed during tippecanoe neither draws
    (tiles or centroids) nor warns."""
    if not tippecanoe:
        ov.pm = None
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    ov.during_ensure = lambda: state.panel_view.set("workflow")
    try:
        ov.run_jobs()
        assert len(ov.ensured) == 1 and ov.drawn == []
        assert map_.find_layer(KEY, none_ok=True) is None
        assert _toasts(ov, "warning") == [] and _toasts(ov, "error") == []
    finally:
        rc.close()


def test_an_empty_set_or_no_set_draws_nothing(ov, tmp_path):
    state = _state(_project(tmp_path), _alerts().iloc[0:0])
    map_, layers, rc = ov.render(state)
    try:
        ov.run_jobs()
        assert ov.ensured == [] and not layers.is_on(KEY)
        state.update_project("d", lambda p: setattr(p, "active_set", None))
        state.alerts_gdf.set(_alerts())
        ov.run_jobs()
        assert ov.ensured == [] and not layers.is_on(KEY)
    finally:
        rc.close()


def _record_radd_set(p, tmp_path):
    """What Integrate's ``apply_result`` does for a second set (RADD, January):
    record it and make it the active set. Its files are on disk first, as a run
    writes them before recording the set. Its fingerprint is not the point here."""
    add_predefined_source(p, "RADD")
    sid = make_set_id(JAN.id, ["RADD"])
    folder = f"periods/{JAN.id}/sets/radd"
    s = AlertSet(id=sid, period_id=JAN.id, sources=["RADD"], digit_map={1: "RADD"}, params=AlertSetParams(),
                 store_rel_path=f"{folder}/alert_db.parquet", combined_rel_path=f"{folder}/c.tif",
                 footprint_rel_path=f"{folder}/footprint.tif", n_alerts=4, fingerprint="x", created_at=datetime.now())
    store = tmp_path / p.name / s.store_rel_path
    store.parent.mkdir(parents=True, exist_ok=True)
    _alerts(set_id=sid).to_parquet(store)
    p.alert_sets[sid] = s
    p.active_set = sid
    return sid


def test_a_new_active_set_waits_for_its_own_alerts(ov, tmp_path):
    """Integrate records a new set before it publishes that set's alerts
    (integrate_jobs.run_integration_job), and the set selector (spec §9.3)
    loads them in a worker after the selection. Until they arrive, the alerts
    on hand are the previous set's: nothing of the new set is tiled, drawn,
    counted, exported or clicked from them."""
    p = _project(tmp_path)
    a = p.active_set
    a_alerts = _alerts(set_id=a)
    state = _state(p, a_alerts)
    map_, layers, rc = ov.render(state)
    try:
        ov.run_jobs()
        [(_, sid, _ws)] = ov.ensured
        assert sid == a

        b = None

        def _record(live):
            nonlocal b
            b = _record_radd_set(live, tmp_path)

        assert state.update_project("d", _record)  # set B is active; A's alerts are still published
        assert not layers.is_on(KEY) and map_.find_layer(KEY, none_ok=True) is None
        ov.run_jobs()
        assert len(ov.ensured) == 1  # nothing was tiled for B
        assert _export_cards(rc) == [] and set(_summary(rc).values()) == {0}
        _click(map_, 1.005, 1.005)  # on one of A's alerts
        assert _popups(map_) == []

        b_alerts = _alerts(set_id=b)
        state.alerts_gdf.set(b_alerts)  # _publish_alerts
        assert layers.is_on(KEY)
        ov.run_jobs()
        [(_, sid, _ws)] = ov.ensured[1:]
        assert sid == b
        assert map_.find_layer(KEY, none_ok=True) is not None and len(_export_cards(rc)) == 1
    finally:
        rc.close()


def test_stale_tiles_wait_for_the_projects_writer_and_are_rebuilt_after(ov, tmp_path):
    """Overview opened while an Integrate re-runs the same set (or a Save
    rewrites its store): the tiles are not rebuilt from what is on hand, the
    centroids stand in without a toast, and once the writer releases the
    project the build is asked for again (I5)."""
    from gui.store.project_writers import is_writing, writing

    ov.ensure_error = set_store.StoreBusy("project 'd' is being written")
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        with writing("d"):
            ov.run_jobs()
            assert len(ov.ensured) == 1 and ov.drawn == []
            _centroid_layer(map_)
            assert _toasts(ov, "warning") == [] and _toasts(ov, "error") == []
            assert layers.is_on(KEY)
            ov.ensure_error = None  # the writer finishes: the store on disk is the re-run's
        assert not is_writing("d")

        ov.run_jobs()  # the effect re-ran when the mark was released
        assert len(ov.ensured) == 2 and ov.drawn == [(ov.pm, KEY, t("steps.overview.layer_name"))]
        assert isinstance(map_.find_layer(KEY, none_ok=True), ipyleaflet.TileLayer)

        with writing("d"):  # a later Save: current tiles are not touched, nothing is rebuilt
            pass
        ov.run_jobs()
        assert len(ov.ensured) == 2
    finally:
        rc.close()


def _centroid_layer(map_):
    layer = map_.find_layer(KEY, none_ok=True)
    assert isinstance(layer, ipyleaflet.GeoJSON)
    return layer


def _browser_style(layer, feature):
    """The style the browser draws ``feature`` with: ipyleaflet's GeoJSON view
    merges ``{...feature.properties.style, ...layer.style}`` (the layer's own
    style wins) and Leaflet applies it over ``point_style``."""
    return {**layer.point_style, **feature["properties"]["style"], **layer.style}


def test_without_tippecanoe_the_alerts_fall_back_to_centroid_points(ov, tmp_path):
    """Ruling #19 (spec §9.2, §12): a warning toast and a GeoJSON centroid layer
    under the same key, never an error."""
    ov.pm = None
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        ov.run_jobs()
        assert ov.drawn == []
        layer = _centroid_layer(map_)
        expected = set_store.centroid_geojson(_alerts())["features"]
        assert [f["geometry"] for f in layer.data["features"]] == [f["geometry"] for f in expected]
        for feature in layer.data["features"]:  # filled with the tiles' point colours, as drawn
            code = feature["properties"]["status_code"]
            drawn = _browser_style(layer, feature)
            assert drawn["color"] == drawn["fillColor"] == STATUS_COLORS[code]
            assert drawn["fill"] and drawn["fillOpacity"] > 0 and drawn["radius"] == 4
        assert [f["properties"]["status_code"] for f in layer.data["features"]] == [1, 2, 3, 0]
        assert layer.name == t("steps.overview.layer_name")
        assert _toasts(ov, "warning") == [t("steps.integrate.no_tippecanoe")]
        assert _toasts(ov, "error") == [] and layers.is_on(KEY)

        state.panel_view.set("workflow")  # the fallback leaves with the view too
        assert map_.find_layer(KEY, none_ok=True) is None
    finally:
        rc.close()


@pytest.mark.parametrize("stage", ["build", "serve"])
def test_a_tile_failure_falls_back_to_centroid_points(ov, tmp_path, stage):
    if stage == "build":
        ov.ensure_error = RuntimeError("tippecanoe exited with 1")
    else:
        ov.draw_error = RuntimeError("tile server down")
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        ov.run_jobs()
        _centroid_layer(map_)
        error = "tippecanoe exited with 1" if stage == "build" else "tile server down"
        assert _toasts(ov, "warning") == [t("steps.overview.tiles_failed", error=error)]
        assert _toasts(ov, "error") == [] and layers.is_on(KEY)
    finally:
        rc.close()


# ---------------------------------------------------------------------------
# Click to review (spec §9.3)
# ---------------------------------------------------------------------------
def test_a_click_on_an_alert_opens_a_popup_whose_review_opens_analysis(ov, tmp_path):
    state = _state(_project(tmp_path), _alerts())
    state.current_page_view.set("overview_tile")
    map_, layers, rc = ov.render(state)
    try:
        _click(map_, 1.005, 1.005)
        [popup] = _popups(map_)
        assert popup.key == "overview:popup" and popup.location == [1.005, 1.005]
        summary, go = popup.child.children
        assert summary.children == [t("steps.overview.popup", id=1, status="False Positive", area="0.25")]
        assert go.children == [t("steps.overview.go")]

        go.fire_event("click", None)
        assert state.actual_alert_id.value == 1
        assert state.current_page_view.value == "analysis_tile"
    finally:
        rc.close()


def test_a_click_off_the_alerts_closes_the_popup(ov, tmp_path):
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        _click(map_, 2.005, 2.005)
        assert len(_popups(map_)) == 1
        _click(map_, 3.013, 3.005)  # just outside alert 3, within the tolerance: a new popup
        [popup] = _popups(map_)
        assert popup.child.children[0].children == [
            t("steps.overview.popup", id=3, status="Not reviewed", area="0.75")]
        _click(map_, 50, 50)  # far from every alert
        assert _popups(map_) == []
    finally:
        rc.close()


def test_leaving_the_overview_removes_the_popup_and_stops_listening(ov, tmp_path):
    """Ruling #82: the cleanup removes an open popup, and a click elsewhere in
    the workflow opens nothing."""
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        _click(map_, 0.005, 0.005)
        assert len(_popups(map_)) == 1
        state.panel_view.set("workflow")
        assert _popups(map_) == []
        _click(map_, 0.005, 0.005)
        assert _popups(map_) == []

        state.panel_view.set("overview")
        _click(map_, 0.005, 0.005)
        assert len(_popups(map_)) == 1
        state.alerts_gdf.set(_alerts())  # other alerts: the popup's index is not theirs
        assert _popups(map_) == []
    finally:
        rc.close()


def test_an_open_popup_does_not_break_the_layer_build(ov, tmp_path):
    """Controller drift notes: a Popup added without a key breaks pysepal's key
    lookups; the set's layer, built while a popup is open, must still land."""
    state = _state(_project(tmp_path), _alerts())
    map_, layers, rc = ov.render(state)
    try:
        _click(map_, 0.005, 0.005)  # the popup opens while the set's layer is building
        ov.run_jobs()
        assert map_.find_layer(KEY, none_ok=True) is not None
        assert _toasts(ov, "error") == []
        state.panel_view.set("workflow")
        assert map_.find_layer(KEY, none_ok=True) is None and _popups(map_) == []
    finally:
        rc.close()
