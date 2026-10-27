"""``gui.tile.analysis.pane_layers``: the app's one writer of the Analysis
views (``AlertViews``). Headless: a real widget, no browser, no Earth Engine."""

import threading
from types import SimpleNamespace

import pytest
from pysepal.i18n import set_locale

from gui.tile.analysis.pane_layers import Z_BASE, analysis_basemaps, geojson_spec, pane_labels, tile_spec
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


def test_construction_sets_the_views_the_basemaps_the_sections_and_the_labels():
    panes = make_panes()
    assert panes.widget.views == [{"id": "before"}, {"id": "after"}]
    assert panes.widget.fit_bounds_views == ["after"], "zoom-to-alert sits on the after view only"
    assert panes.widget.basemap == {}, "the basemap switcher owns the basemap"
    assert panes.widget.basemap_switcher_basemaps == analysis_basemaps()
    assert panes.widget.basemap_switcher_selected == "sepal_light", "SEPAL Light is the default"
    assert panes.widget.layer_menu_sections == ["imagery", "alerts", "optional"]
    assert panes.widget.labels == pane_labels()
    assert set(pane_labels()) >= {"layerMenu.title", "fitBounds.button", "draw.hint"}
    assert pane_labels()["layerMenu.title"] == "Layers"


def test_the_basemaps_are_google_esri_and_sepals_light_and_dark():
    from pysepal.mapping import basemaps as pysepal_basemaps

    found = {b["id"]: b for b in analysis_basemaps()}
    assert list(found) == ["google", "esri", "sepal_light", "sepal_dark"]
    assert found["google"]["url"] == pysepal_basemaps.xyz_tiles["SATELLITE"]["url"]
    assert found["esri"]["url"] == ("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer"
                                    "/tile/{z}/{y}/{x}")
    assert found["sepal_light"]["url"] == pysepal_basemaps.BASEMAP_LIGHT
    assert found["sepal_dark"]["url"] == pysepal_basemaps.BASEMAP_DARK
    assert found["sepal_light"]["attribution"] == pysepal_basemaps.BASEMAP_ATTRIBUTION
    for b in found.values():
        assert b["attribution"] and b["name"] and b["maxZoom"] >= 16
        assert b["thumbnail"].startswith("https://") and "{" not in b["thumbnail"], "one concrete tile"


def test_the_basemap_and_section_labels_are_translated():
    english = pane_labels()
    assert english["basemapSwitcher.title"] == "Basemap"
    assert english["basemapSwitcher.sepal_dark"] == "SEPAL Dark"
    assert english["layerMenu.section.imagery"] == "Imagery"
    assert english["layerMenu.section.alerts"] == "Alerts"
    assert english["layerMenu.section.optional"] == "Optional layers"
    set_locale("es-ES")
    spanish = pane_labels()
    assert spanish["basemapSwitcher.title"] == "Mapa base"
    assert spanish["basemapSwitcher.sepal_dark"] == "SEPAL Oscuro"
    assert spanish["layerMenu.section.optional"] == "Capas opcionales"
    for locale in ("fr", "pt-BR"):
        set_locale(locale)
        translated = pane_labels()
        for key in ("basemapSwitcher.title", "basemapSwitcher.sepal_light", "layerMenu.section.imagery"):
            assert translated[key] != english[key], (locale, key)


def test_a_spec_section_is_its_menu_section():
    assert tile_spec("t", "all", URL, name="t", group="vis", section="optional")["meta"] == {
        "menuSection": "optional"}
    assert geojson_spec("g", "all", {}, name="g", group="alert", section="alerts",
                        hide_while_editing=True)["meta"] == {"hideWhileEditing": True, "menuSection": "alerts"}


def test_a_target_puts_its_layers_in_its_section_and_a_key_may_have_its_own(monkeypatch):
    from ipyleaflet import TileLayer

    panes = make_panes()
    monkeypatch.setattr(panes, "tile_url", lambda img, vis: URL)
    before = panes.target("before", group="alert", section="imagery", sections={"original-alert": "alerts"})
    before.addLayer("img", {}, "True Color", True)
    before.add_layer(TileLayer(url=URL, name="Original Alert"), key="original-alert")
    assert panes.spec("before:True Color")["meta"] == {"menuSection": "imagery"}
    assert panes.spec("before:original-alert")["meta"] == {"menuSection": "alerts"}


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


def test_earth_engine_tiles_load_through_the_tile_queue():
    """A project in restricted mode answers a burst of tile requests with 429:
    Earth Engine layers are paced and retried in the browser (``queued``)."""
    ee = "https://earthengine.googleapis.com/v1/projects/p/maps/abc/tiles/{z}/{x}/{y}"
    assert tile_spec("before:True Color", "before", ee, name="True Color", group="alert")["queued"] is True
    assert "queued" not in tile_spec("t", "all", URL, name="t", group="vis")


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
    assert Z_BASE["alert"] < Z_BASE["vis"] < Z_BASE["earlier"] < Z_BASE["alert_top"]
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


def test_the_hole_tool_labels_are_translated():
    labels = pane_labels()
    assert (labels["holes.fill"], labels["holes.remove"]) == ("Fill holes", "Remove hole")
    assert labels["holes.hint"] == "Click inside a hole to remove it · Esc to stop"
    set_locale("es-ES")
    assert pane_labels()["holes.fill"] == "Rellenar huecos"
    set_locale("fr")
    assert pane_labels()["holes.remove"] == "Supprimer un trou"


BULK_DELETE_ENGLISH = {
    # The draw plugin's own English defaults (js/leaflet-multiview/src/plugins/draw/index.js).
    "draw.selectArea": "Select area",
    "draw.clearAll": "Clear all",
    "draw.selectAreaHint": "Drag a box around the shapes to delete · Esc to cancel",
    "draw.confirmOne": "Delete 1 shape?",
    "draw.confirmMany": "Delete {n} shapes?",
    "draw.confirmAll": "Delete all {n} shapes?",
    "draw.cancel": "Cancel",
    "draw.delete": "Delete",
}


def test_the_bulk_delete_labels_are_translated():
    labels = pane_labels()
    assert {k: labels[k] for k in BULK_DELETE_ENGLISH} == BULK_DELETE_ENGLISH
    for locale in ("es-ES", "fr", "pt-BR"):
        set_locale(locale)
        translated = pane_labels()
        for key in BULK_DELETE_ENGLISH:
            assert translated[key] != BULK_DELETE_ENGLISH[key], (locale, key)
        for key in ("draw.confirmMany", "draw.confirmAll"):
            assert "{n}" in translated[key], (locale, key)
    set_locale("es-ES")
    assert pane_labels()["draw.selectArea"] == "Seleccionar área"


def test_the_tile_error_badge_label_is_translated():
    assert pane_labels()["tileErrors.badge"] == "Imagery failed to load"
    set_locale("es-ES")
    assert pane_labels()["tileErrors.badge"] == "No se pudieron cargar las imágenes"
    set_locale("fr")
    assert pane_labels()["tileErrors.badge"] == "Les images n'ont pas pu être chargées"


def test_earlier_alerts_draw_over_the_imagery_and_under_the_alert_box_and_polygon():
    """Spec 2026-10-20 §6.3: the earlier outlines sit under the current
    alert's box and reviewed polygon, over its imagery."""
    from types import SimpleNamespace

    import geopandas as gpd
    from ipyleaflet import TileLayer
    from shapely.geometry import box

    from gui.scripts.growth_index import EarlierView
    from gui.tile.analysis import earlier
    from gui.tile.analysis.edit_tools import DEFO_ID, wire_defo_layer
    from gui.tile.analysis.pane_layers import ALERT_TOP_Z
    from gui.tile.analysis.state import AnalysisState

    panes = make_panes()
    panes.target("after", group="alert").add_layer(TileLayer(url=URL, name="True Color"), key="tc")
    panes.add(earlier.layer_spec(EarlierView((), 0, 0, ())))
    state = AnalysisState()
    unsubscribe = wire_defo_layer(SimpleNamespace(panes=panes), state)
    state.defo_layer.set(gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs="EPSG:4326"))
    unsubscribe()
    imagery, overlay, polygon = (panes.spec(i)["z"] for i in ("after:tc", earlier.EARLIER_ID, DEFO_ID))
    assert imagery < overlay < ALERT_TOP_Z < polygon


# ---- pick (group review) -----------------------------------------------------------

def test_the_nearby_band_sits_between_earlier_alerts_and_the_alert_top():
    assert Z_BASE["earlier"] < Z_BASE["nearby"] == 3_500_000 < Z_BASE["alert_top"]


def test_layer_off_reads_the_layer_menu_toggles():
    panes = make_panes()
    panes.widget.layer_menu_toggles = {"before": {"analysis-nearby": False}}
    assert panes.layer_off("before", "analysis-nearby") is True
    assert panes.layer_off("after", "analysis-nearby") is False
    assert panes.layer_off("before", "other") is False


def test_set_pick_tag_writes_the_trait():
    panes = make_panes()
    panes.set_pick_tag("7:1")
    assert panes.widget.pick_tag == "7:1"


def test_on_pick_calls_back_and_unsubscribes():
    panes = make_panes()
    seen = []
    off = panes.on_pick(seen.append)
    event = {"tag": "7:1", "view": "after", "lat": 1.0, "lng": 2.0, "zoom": 14, "seq": 1}
    panes.widget.set_state({"pick_event": event})
    assert seen == [event]
    off()
    panes.widget.set_state({"pick_event": {**event, "seq": 2}})
    assert seen == [event]
