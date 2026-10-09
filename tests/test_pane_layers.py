"""``gui.tile.analysis.pane_layers``: the app's one writer of the Analysis
views (``AlertViews``). Headless: a real widget, no browser, no Earth Engine."""

import threading
from types import SimpleNamespace

import pytest
from pysepal.i18n import set_locale

from gui.tile.analysis.pane_layers import Z_BASE, geojson_spec, pane_labels, tile_spec
from gui.tile.analysis.state import AnalysisState
from tests._pane_helpers import make_panes, reviewer_edits

URL = "https://example.invalid/{z}/{x}/{y}.png"
SQUARE = {"type": "Feature", "properties": {}, "geometry": {
    "type": "Polygon", "coordinates": [[[0, 0], [0, 0.01], [0.01, 0.01], [0.01, 0], [0, 0]]]}}


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def test_construction_sets_the_views_the_basemap_and_the_labels():
    from pysepal.mapping.basemaps import xyz_tiles

    panes = make_panes()
    assert panes.widget.views == [{"id": "before"}, {"id": "after"}]
    assert panes.widget.fit_bounds_views == ["after"], "zoom-to-alert sits on the after view only"
    assert panes.widget.basemap == {"url": xyz_tiles["SATELLITE"]["url"],
                                    "attribution": xyz_tiles["SATELLITE"]["attribution"]}
    assert panes.widget.labels == pane_labels()
    assert set(pane_labels()) >= {"layerMenu.title", "fitBounds.button", "draw.hint"}
    assert pane_labels()["layerMenu.title"] == "Layers"


def test_specs_carry_every_field_the_frontend_reads():
    s = tile_spec("before:True Color", "before", URL, name="True Color", group="alert", visible=False,
                  report_errors=True)
    assert s == {"id": "before:True Color", "view": "before", "kind": "tile", "url": URL,
                 "name": "True Color", "visible": False, "opacity": 1.0, "z": None, "group": "alert",
                 "attribution": "", "meta": {"reportErrors": True}}
    data = {"type": "Point", "coordinates": [0, 0]}
    g = geojson_spec("alert-bb", "all", data, name="Alert BB", group="alert", style={"color": "yellow"},
                     hide_while_editing=True)
    assert g == {"id": "alert-bb", "view": "all", "kind": "geojson", "data": data,
                 "style": {"color": "yellow"}, "name": "Alert BB", "visible": True, "opacity": 1.0,
                 "z": None, "group": "alert", "meta": {"hideWhileEditing": True}}
    assert tile_spec("t", "all", URL, name="t", group="vis")["meta"] == {}
    assert geojson_spec("g", "all", data, name="g", group="earlier")["meta"] == {}


def test_an_added_spec_takes_its_group_band_and_an_explicit_z_is_kept():
    panes = make_panes()
    panes.add(tile_spec("a", "all", URL, name="a", group="alert"))
    panes.add(tile_spec("e", "all", URL, name="e", group="earlier", z=5))
    assert panes.spec("a")["z"] == Z_BASE["alert"]
    assert panes.spec("e")["z"] == 5
    assert panes.ids() == ["a", "e"]
    panes.clear("alert")
    panes.remove("e")
    assert panes.ids() == []


def test_set_view_bumps_n_and_focus_bounds_are_south_west_north_east():
    panes = make_panes()
    n0 = panes.widget.view["n"]
    panes.set_view((-1.5, -60.25), 16)
    first = panes.widget.view["n"]
    panes.set_view((-1.5, -60.25), 16)
    second = panes.widget.view["n"]
    assert n0 < first < second, "every host view gets a new n, so the frontend applies it"
    assert panes.widget.view == {"center": [-1.5, -60.25], "zoom": 16, "n": second, "origin": "host"}
    panes.set_focus_bounds((-60.3, -1.6, -60.2, -1.4))  # shapely (minx, miny, maxx, maxy)
    assert panes.widget.fit_bounds_bounds == [[-1.6, -60.3], [-1.4, -60.2]]
    panes.set_focus_bounds(None)
    assert panes.widget.fit_bounds_bounds is None


def test_set_drawing_copies_and_drawing_geojson_strips_the_style():
    panes = make_panes()
    feature = {"type": "Feature", "properties": {"style": {"color": "lime"}, "n": 1},
               "geometry": {"type": "Point", "coordinates": [0, 0]}}
    panes.set_drawing([feature])
    feature["properties"]["n"] = 99
    assert panes.drawing_features()[0]["properties"]["n"] == 1
    panes.drawing_features()[0]["properties"]["n"] = 42
    assert panes.drawing_features()[0]["properties"]["n"] == 1, "a read is a copy"
    out = panes.drawing_geojson()
    assert out["type"] == "FeatureCollection"
    assert out["features"][0]["properties"] == {"n": 1}
    assert panes.drawing_features()[0]["properties"]["style"] == {"color": "lime"}, "the stored drawing keeps it"


def test_a_python_write_of_the_drawing_is_not_an_edit():
    """Review Focus 1: Python's own writes (seeding, *Add to map*, an alert
    change) never come back as an edit; the reviewer's finished edits do."""
    panes = make_panes()
    heard = []
    off = panes.on_drawing_edit(lambda: heard.append(True))
    panes.set_drawing([SQUARE])
    assert heard == []
    reviewer_edits(panes, [SQUARE, SQUARE])
    assert heard == [True]
    assert len(panes.drawing_features()) == 2
    panes.set_drawing([])
    assert heard == [True], "Python's write after an edit is not an edit either"
    assert panes.drawing_features() == []
    off()
    reviewer_edits(panes, [SQUARE])
    assert heard == [True], "unsubscribed"


def test_an_edit_from_before_a_python_write_is_dropped():
    """A user write that carries the generation Python has since replaced is
    stale: it is not an edit, and Python's drawing stays."""
    panes = make_panes()
    heard = []
    panes.on_drawing_edit(lambda: heard.append(True))
    panes.set_drawing([SQUARE])
    stale_gen = panes.widget.draw["gen"]
    panes.set_drawing([])  # e.g. an alert change, before the browser's edit arrived
    panes.widget.set_state({"draw": {"fc": {"type": "FeatureCollection", "features": [SQUARE, SQUARE]},
                                     "gen": stale_gen, "seq": 1, "origin": "user"}})
    assert heard == []
    assert panes.drawing_features() == []
    assert panes.widget.draw["origin"] == "host", "Python re-asserted its drawing"
    assert panes.widget.draw["fc"]["features"] == []


def test_edit_mode_is_a_plain_switch():
    panes = make_panes()
    panes.set_edit_mode(True)
    assert panes.widget.draw_edit_mode is True
    panes.set_edit_mode(0)
    assert panes.widget.draw_edit_mode is False


def test_relabel_follows_the_language():
    panes = make_panes()
    set_locale("es-ES")
    assert panes.widget.labels["layerMenu.title"] == "Layers", "only relabel re-reads them"
    panes.relabel()
    assert {"layerMenu.title": "Capas", "fitBounds.button": "Acercar a la alerta",
            "draw.hint": "Clic derecho en un vértice para borrarlo"}.items() <= panes.widget.labels.items()


def test_a_target_adds_ee_layers_as_tile_specs_with_view_prefixed_ids(monkeypatch):
    panes = make_panes()
    monkeypatch.setattr(panes, "tile_url", lambda img, vis: f"{URL}?{img}")
    before = panes.target("before", group="alert")
    before.addLayer("img", {"min": 0}, "True Color", True)
    before.addLayer("img2", {}, "False Color", False)
    everywhere = panes.target("all", group="vis")
    everywhere.add_ee_layer("img3", {}, name="S2", key="vis:s2", shown=False)
    assert panes.ids() == ["before:True Color", "before:False Color", "vis:s2"]
    tc, fc, s2 = (panes.spec(i) for i in panes.ids())
    assert tc["url"] == f"{URL}?img" and tc["visible"] is True and fc["visible"] is False
    assert tc["view"] == "before" and tc["group"] == "alert"
    assert tc["z"] < fc["z"] < Z_BASE["vis"] <= s2["z"], "later adds on top; vis over alert imagery"
    assert s2["view"] == "all" and s2["group"] == "vis" and s2["visible"] is False
    assert tc["attribution"] == "Google Earth Engine"
    assert tc["meta"] == {} and s2["meta"] == {}


def test_a_target_takes_a_tileserver_layer_and_removes_by_key():
    from ipyleaflet import TileLayer

    panes = make_panes()
    after = panes.target("after", group="alert")
    after.add_layer(TileLayer(url=URL, name="Original Alert", opacity=0.5, visible=False), key="original-alert")
    spec = panes.spec("after:original-alert")
    assert spec["url"] == URL and spec["opacity"] == 0.5 and spec["visible"] is False
    assert spec["name"] == "Original Alert" and spec["view"] == "after"
    after.remove_layer("original-alert")
    assert panes.ids() == []
    after.remove_layer("original-alert", none_ok=True)
    with pytest.raises(ValueError):
        after.remove_layer("original-alert")


def test_an_imagery_target_marks_its_tiles_to_report_errors(monkeypatch):
    from ipyleaflet import TileLayer

    panes = make_panes()
    monkeypatch.setattr(panes, "tile_url", lambda img, vis: URL)
    target = panes.target("before", group="alert", report_errors=True)
    assert (target.view, target.group, target.report_errors) == ("before", "alert", True)
    target.addLayer("img", {}, "True Color", True)
    target.add_layer(TileLayer(url=URL, name="Original Alert"), key="original-alert")
    assert panes.spec("before:True Color")["meta"] == {"reportErrors": True}
    assert panes.spec("before:original-alert")["meta"] == {"reportErrors": True}


def test_a_target_runs_its_adds_through_the_gate_on_this_thread_only(monkeypatch):
    panes = make_panes()
    monkeypatch.setattr(panes, "tile_url", lambda img, vis: URL)
    target = panes.target("before", group="alert")
    with target.gated(lambda write: None):
        target.addLayer("img", {}, "dropped", True)
        th = threading.Thread(target=lambda: target.addLayer("img", {}, "other thread", True))
        th.start()
        th.join(5)
    target.addLayer("img", {}, "after", True)
    assert panes.ids() == ["before:other thread", "before:after"]


def test_the_alert_gate_drops_a_spec_built_for_a_left_alert():
    """The gate the imagery worker passes: a write that lands after the
    alert changed is dropped, under the alert lock."""
    s = AnalysisState()
    panes = make_panes()
    token = s.alert_token.value
    gate = lambda write: s.apply_if_current(token, write)  # noqa: E731
    panes.add(tile_spec("before:a", "before", URL, name="a", group="alert"), gate=gate)
    s.reset_alert()
    panes.add(tile_spec("before:b", "before", URL, name="b", group="alert"), gate=gate)
    assert panes.ids() == ["before:a"]


def test_the_facade_is_the_only_writer_a_widget_needs():
    """The attributes the analysis modules reach for, on one object."""
    panes = make_panes()
    for name in ("add", "remove", "clear", "spec", "ids", "tile_url", "set_view", "set_focus_bounds",
                 "set_edit_mode", "set_drawing", "drawing_features", "drawing_geojson", "on_drawing_edit",
                 "relabel", "target", "widget"):
        assert hasattr(panes, name), name
    assert SimpleNamespace(panes=panes).panes.widget is panes.widget
