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
    make(plain_page, _opts(), plugins=[("layerMenu", {}), ("fitBounds", {})])
    mv_eval(plain_page, "(mv) => mv.destroy()")
    assert plain_page.locator(".mv-layer-menu").count() == 0
    assert plain_page.locator(".mv-fit-bounds").count() == 0
