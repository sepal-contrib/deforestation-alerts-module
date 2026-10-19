"""The layerMenu and fitBounds plugins on the plain page (script-tag Leaflet,
no Jupyter): per-view toggles, the listed layers, labels, the fit button."""

import pytest

from tests._plain_page import (  # noqa: F401
    centers,
    events,
    make,
    mv_eval,
    plain_browser,
    plain_page,
    plain_server,
)

TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"  # one transparent pixel: loads, no network
VIEWS = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
START = {"center": [-1.0, -60.0], "zoom": 14}
BOUNDS = [[0.0, 0.0], [0.01, 0.01]]


def _opts():
    return {"views": VIEWS, "layout": "grid", "basemap": None, "view": START}


def _tile(id_, view="all", name=None, **kw):
    spec = {"id": id_, "view": view, "kind": "tile", "url": TILE, "name": name or id_, "visible": True,
            "opacity": 1.0, "z": 1}
    spec.update(kw)
    return spec


def _set_layers(page, specs):
    mv_eval(page, "(mv, specs) => mv.setLayers(specs)", specs)


def _shown(page, view_id, layer_id):
    return mv_eval(page, "(mv, [v, id]) => { const l = mv.layer(v, id); return !!l && mv.views.find(x => x.id === v).map.hasLayer(l); }",
                   [view_id, layer_id])


def _menu(page, view_id):
    return f'.mv-view[data-view="{view_id}"] .mv-layer-menu'


def _click_checkbox(page, view_id, layer_id):
    # A real click on the hidden checkbox: it toggles it and fires "change".
    page.evaluate("s => document.querySelector(s).click()", f'{_menu(page, view_id)} input[data-id="{layer_id}"]')
    page.wait_for_timeout(100)


def _menu_names(page, view_id):
    return page.evaluate("s => [...document.querySelectorAll(s)].map(n => n.textContent.trim())",
                         f"{_menu(page, view_id)} .leaflet-control-layers-overlays label > span > span")


def _fit_button(page, view_id):
    return page.locator(f'.mv-view[data-view="{view_id}"] .mv-fit-bounds')


# --- layerMenu -----------------------------------------------------------------


def test_a_layer_menu_toggle_hides_the_layer_in_that_view_only(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {})])
    _set_layers(plain_page, [_tile("t"), _tile("u")])
    _click_checkbox(plain_page, "b", "t")
    assert not _shown(plain_page, "b", "t")
    assert _shown(plain_page, "a", "t") and _shown(plain_page, "c", "t")
    assert _shown(plain_page, "b", "u"), "only the clicked layer"
    assert events(plain_page, "layertoggle") == [{"view": "b", "id": "t", "on": False}]
    assert mv_eval(plain_page, "(mv) => mv.overrides()") == {"b": {"t": False}}
    assert plain_page.evaluate("s => document.querySelector(s).checked",
                               f'{_menu(plain_page, "b")} input[data-id="t"]') is False
    _click_checkbox(plain_page, "b", "t")
    assert _shown(plain_page, "b", "t")
    assert events(plain_page, "layertoggle")[-1] == {"view": "b", "id": "t", "on": True}


def test_the_menu_lists_only_the_views_layers_by_name(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {})])
    assert plain_page.locator(_menu(plain_page, "a")).is_visible() is False, "no layers: no menu"
    _set_layers(plain_page, [_tile("x", view="a", name="Imagery A"), _tile("y", view=["b", "c"], name="Shared"),
                             _tile("z", name="Everywhere", visible=False)])
    assert _menu_names(plain_page, "a") == ["Imagery A", "Everywhere"]
    assert _menu_names(plain_page, "b") == ["Shared", "Everywhere"]
    assert _menu_names(plain_page, "c") == ["Shared", "Everywhere"]
    checked = plain_page.evaluate("s => [...document.querySelectorAll(s)].map(i => [i.dataset.id, i.checked])",
                                  f'{_menu(plain_page, "a")} input')
    assert checked == [["x", True], ["z", False]], "the ticks follow what the map shows"
    assert plain_page.locator(_menu(plain_page, "a")).is_visible()


def test_set_labels_relabels_the_menu(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {})])
    _set_layers(plain_page, [_tile("t")])
    titles = f"{_menu(plain_page, 'a')} .mv-layers-title"
    assert plain_page.evaluate("s => document.querySelector(s).textContent", titles) == "Layers"
    mv_eval(plain_page, "(mv) => mv.setLabels({'layerMenu.title': 'Capas'})")
    assert plain_page.evaluate("() => [...document.querySelectorAll('.mv-layers-title')].map(n => n.textContent)") == ["Capas"] * 3
    assert plain_page.get_attribute(f"{_menu(plain_page, 'a')} .leaflet-control-layers-toggle", "title") == "Capas"


def test_a_menu_toggle_survives_an_update_of_that_layer_and_goes_with_it(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {})])
    _set_layers(plain_page, [_tile("t")])
    _click_checkbox(plain_page, "b", "t")
    _set_layers(plain_page, [_tile("t", url=TILE + "#v2")])  # a new Leaflet layer, same id
    assert not _shown(plain_page, "b", "t") and _shown(plain_page, "a", "t")
    _set_layers(plain_page, [])
    assert "t" not in (mv_eval(plain_page, "(mv) => mv.overrides()").get("b") or {}), "pruned once the layer is gone"


def _menu_rows(page, view_id):
    """The menu's rows in order: ``"# <heading>"``, a layer's name, or ``"---"`` for a separator."""
    return page.evaluate("""s => [...document.querySelector(s).children].map(n =>
        n.classList.contains('leaflet-control-layers-separator') ? '---'
        : n.classList.contains('mv-layers-section') ? '# ' + n.textContent : n.textContent.trim())""",
                         f"{_menu(page, view_id)} .leaflet-control-layers-overlays")


def _in(section, **kw):
    return {"meta": {"menuSection": section}, **kw}


def test_sections_list_their_layers_under_a_heading_in_the_configured_order(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {"sections": ["imagery", "alerts", "optional"]})])
    _set_layers(plain_page, [_tile("a1", name="Alert BB", **_in("alerts")),
                             _tile("i1", name="True Color", **_in("imagery")),
                             _tile("u", name="Loose"),
                             _tile("i2", name="False Color", **_in("imagery")),
                             _tile("x", name="Extra", **_in("extra"))])
    assert _menu_rows(plain_page, "a") == [
        "Loose", "---", "# imagery", "True Color", "False Color", "---", "# alerts", "Alert BB",
        "---", "# extra", "Extra",
    ], "unsectioned first, then the configured order, then the others; an empty section is not shown"
    _click_checkbox(plain_page, "a", "i2")
    assert not _shown(plain_page, "a", "i2"), "a sectioned layer toggles like any other"


def test_section_headings_follow_set_labels(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {"sections": ["imagery"]})])
    _set_layers(plain_page, [_tile("i1", name="True Color", **_in("imagery"))])
    mv_eval(plain_page, "(mv) => mv.setLabels({'layerMenu.section.imagery': 'Imágenes'})")
    assert _menu_rows(plain_page, "b") == ["# Imágenes", "True Color"]


def test_hovering_the_menu_expands_it(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {})])
    _set_layers(plain_page, [_tile("t", name="True colour")])
    menu = plain_page.locator(_menu(plain_page, "a"))
    assert plain_page.locator(f"{_menu(plain_page, 'a')} .mv-layers-title").is_visible() is False
    menu.hover()
    assert plain_page.locator(f"{_menu(plain_page, 'a')} .mv-layers-title").is_visible()
    plain_page.locator(f'{_menu(plain_page, "a")} input[data-id="t"]').click()
    plain_page.wait_for_timeout(100)
    assert not _shown(plain_page, "a", "t")


# --- basemapSwitcher ---------------------------------------------------------------

BASEMAPS = [
    {"id": "g", "name": "Google Satellite", "url": f"{TILE}#g", "attribution": "Google", "thumbnail": TILE},
    {"id": "e", "name": "Esri World Imagery", "url": f"{TILE}#e", "attribution": "Esri", "maxZoom": 19,
     "thumbnail": TILE},
]


def _switcher(page, view_id):
    return f'.mv-view[data-view="{view_id}"] .mv-basemaps'


def _basemap_urls(page):
    """Per view, the URL fragments of the tile layers in the basemap pane."""
    return mv_eval(page, """(mv) => mv.views.map(v => { const out = [];
        v.map.eachLayer(l => { if (l instanceof mv.L.TileLayer && l.options.pane === 'mvBasemap') out.push(l._url.split('#')[1]); });
        return out; })""")


def _selected(page, view_id):
    return page.evaluate("s => [...document.querySelectorAll(s)].map(b => b.dataset.id)",
                         f"{_switcher(page, view_id)} .mv-basemap--on")


def test_the_basemap_switcher_lists_every_basemap_and_shows_the_selected_one(plain_page):
    make(plain_page, _opts(), plugins=[("basemapSwitcher", {"basemaps": BASEMAPS, "selected": "e"})])
    for v in "abc":
        names = plain_page.evaluate("s => [...document.querySelectorAll(s)].map(b => b.textContent.trim())",
                                    f"{_switcher(plain_page, v)} .mv-basemap")
        assert names == ["Google Satellite", "Esri World Imagery"]
        assert _selected(plain_page, v) == ["e"]
    assert _basemap_urls(plain_page) == [["e"]] * 3
    assert mv_eval(plain_page, "(mv) => mv.plugin('basemapSwitcher').selected()") == "e"
    native = mv_eval(plain_page, """(mv) => { let z = null; mv.views[0].map.eachLayer(l => {
        if (l.options.pane === 'mvBasemap') z = l.options.maxNativeZoom; }); return z; }""")
    assert native == 19, "the basemap's maxZoom is its native maximum"


def test_a_click_switches_the_basemap_of_every_view_once(plain_page):
    make(plain_page, _opts(), plugins=[("basemapSwitcher", {"basemaps": BASEMAPS})])
    assert _basemap_urls(plain_page) == [["g"]] * 3, "no selected option: the first"
    plain_page.locator(f'{_switcher(plain_page, "b")} .mv-basemap[data-id="e"]').click()
    plain_page.wait_for_timeout(100)
    assert _basemap_urls(plain_page) == [["e"]] * 3
    assert [_selected(plain_page, v) for v in "abc"] == [["e"]] * 3, "every view's switcher follows"
    assert events(plain_page, "basemapchange") == [{"id": "e"}]
    plain_page.locator(f'{_switcher(plain_page, "a")} .mv-basemap[data-id="e"]').click()
    plain_page.wait_for_timeout(100)
    assert events(plain_page, "basemapchange") == [{"id": "e"}], "the selected one again: no change"
    assert centers(plain_page)[0][2] == START["zoom"], "a click on the switcher does not zoom the map"


def test_select_switches_without_an_event_and_ignores_unknown_ids(plain_page):
    make(plain_page, _opts(), plugins=[("basemapSwitcher", {"basemaps": BASEMAPS, "selected": "nope"})])
    assert _basemap_urls(plain_page) == [["g"]] * 3, "an unknown selected option: the first"
    mv_eval(plain_page, "(mv) => mv.plugin('basemapSwitcher').select('e')")
    assert _basemap_urls(plain_page) == [["e"]] * 3 and _selected(plain_page, "c") == ["e"]
    mv_eval(plain_page, "(mv) => mv.plugin('basemapSwitcher').select('nope')")
    assert _basemap_urls(plain_page) == [["e"]] * 3
    assert events(plain_page, "basemapchange") == []


def test_the_basemap_switcher_follows_set_labels(plain_page):
    make(plain_page, _opts(), plugins=[("basemapSwitcher", {"basemaps": BASEMAPS})])
    assert plain_page.get_attribute(_switcher(plain_page, "a"), "aria-label") == "Basemap"
    mv_eval(plain_page, "(mv) => mv.setLabels({'basemapSwitcher.title': 'Mapa base', 'basemapSwitcher.e': 'Esri Imágenes'})")
    assert plain_page.get_attribute(_switcher(plain_page, "a"), "aria-label") == "Mapa base"
    button = f'{_switcher(plain_page, "a")} .mv-basemap[data-id="e"]'
    assert plain_page.text_content(button).strip() == "Esri Imágenes"
    assert plain_page.get_attribute(button, "title") == "Esri Imágenes"


def test_no_basemaps_no_switcher(plain_page):
    make(plain_page, _opts(), plugins=[("basemapSwitcher", {})])
    assert plain_page.locator(".mv-basemaps").count() == 0
    assert _basemap_urls(plain_page) == [[]] * 3


# --- fitBounds -------------------------------------------------------------------


def test_fit_bounds_shows_on_the_configured_views_once_bounds_are_set(plain_page):
    make(plain_page, _opts(), plugins=[("fitBounds", {"views": ["b"]})])
    assert _fit_button(plain_page, "a").count() == 0 and _fit_button(plain_page, "c").count() == 0
    assert _fit_button(plain_page, "b").count() == 1
    assert _fit_button(plain_page, "b").is_visible() is False, "hidden until there are bounds"
    mv_eval(plain_page, "(mv, b) => mv.plugin('fitBounds').setBounds(b)", BOUNDS)
    assert _fit_button(plain_page, "b").is_visible()
    assert mv_eval(plain_page, "(mv) => mv.plugin('fitBounds').bounds()") == BOUNDS
    _fit_button(plain_page, "b").click()
    plain_page.wait_for_timeout(600)
    for lat, lng, _ in centers(plain_page):  # every view follows the fit
        assert (lat, lng) == pytest.approx((0.005, 0.005), abs=1e-3)
    assert len(events(plain_page, "viewchange")) == 1, "a fit is a user move"
    mv_eval(plain_page, "(mv) => mv.plugin('fitBounds').setBounds(null)")
    assert _fit_button(plain_page, "b").is_visible() is False
    assert mv_eval(plain_page, "(mv) => mv.plugin('fitBounds').bounds()") is None


def test_fit_bounds_defaults_to_every_view_and_follows_set_labels(plain_page):
    make(plain_page, _opts(), plugins=[("fitBounds", {})])
    assert [_fit_button(plain_page, v).count() for v in "abc"] == [1, 1, 1]
    assert _fit_button(plain_page, "a").get_attribute("title") == "Zoom to area"
    mv_eval(plain_page, "(mv) => mv.setLabels({'fitBounds.button': 'Acercar'})")
    assert [_fit_button(plain_page, v).get_attribute("title") for v in "abc"] == ["Acercar"] * 3


def test_a_second_use_of_the_same_plugin_throws(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {}), ("fitBounds", {})])
    messages = mv_eval(plain_page, """(mv) => ['layerMenu', 'fitBounds'].map(name => {
        try { mv.use(window.__mvlib[name]); return 'no error'; } catch (e) { return e.message; }
    })""")
    assert messages == ["layerMenu is already installed", "fitBounds is already installed"]
    assert plain_page.locator(".mv-layer-menu").count() == 3, "the failed use added nothing"


def test_destroy_removes_the_plugins_controls(plain_page):
    make(plain_page, _opts(), plugins=[("layerMenu", {}), ("fitBounds", {}),
                                       ("basemapSwitcher", {"basemaps": BASEMAPS})])
    mv_eval(plain_page, "(mv) => mv.destroy()")
    assert plain_page.locator(".mv-layer-menu").count() == 0
    assert plain_page.locator(".mv-fit-bounds").count() == 0
    assert plain_page.locator(".mv-basemaps").count() == 0
