"""The multiview widget (committed bundle + anywidget adapter) in a real
browser: view sync and the view protocol, the views mounting while hidden,
the resize nudge, the page's own ``L``, the layers and basemap traits, the
layer-menu and fit-bounds bindings, the self re-render, unknown plugins,
install errors, theme, labels and cleanup."""

import pytest

from tests._multiview_page import (  # noqa: F401
    centers,
    listeners,
    mount,
    mv_browser,
    mv_eval,
    mv_page,
    pixel_deg,
    py,
    saves,
    unmount,
    view_box,
    writes,
)

TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"  # one transparent pixel: loads, no network


def _tile(id_, **kw):
    spec = {"id": id_, "view": "all", "kind": "tile", "url": f"{TILE}#{id_}", "name": id_, "visible": True,
            "opacity": 1.0, "z": 1, "group": "alert", "meta": {}}
    spec.update(kw)
    return spec


def _tiles(page, view_id):
    """The URL fragments of the data: tile layers on that view's map."""
    return mv_eval(page, """(mv, id) => { const out = []; const view = mv.views.find(v => v.id === id);
        view.map.eachLayer(l => { if (l instanceof mv.L.TileLayer && l._url.startsWith('data:')) out.push(l._url.split('#')[1]); });
        return out.sort(); }""", view_id)


def _leaflet_id(page, view_id, frag):
    return mv_eval(page, """(mv, [id, frag]) => { let found = null; const view = mv.views.find(v => v.id === id);
        view.map.eachLayer(l => { if (l instanceof mv.L.TileLayer && l._url.endsWith('#' + frag)) found = l._leaflet_id; });
        return found; }""", [view_id, frag])


def _on_view(page, center, zoom):
    """The canonical view is exactly ``center``/``zoom``; every map is on it to within a pixel."""
    assert mv_eval(page, "mv => mv.getView()") == {"center": list(center), "zoom": zoom}
    for lat, lng, z in centers(page):
        assert z == zoom and (lat, lng) == pytest.approx(tuple(center), abs=1.5 * pixel_deg(zoom))


def _drag(page, view_id, dx, dy, *, rest_ms=0):
    """A mouse drag from the centre of the view. ``rest_ms`` holds the button
    still before the release: Leaflet then adds no inertia, so the drag's
    moveend fires at the release."""
    box = view_box(page, view_id)
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=12)
    if rest_ms:
        page.wait_for_timeout(rest_ms)
    page.mouse.up()


def _toggle(page, view_id, layer_id):
    """A click on that view's layer-menu checkbox (the menu expands on hover)."""
    page.locator(f'.mv-view[data-view="{view_id}"] .mv-layer-menu').hover()
    page.locator(f'.mv-view[data-view="{view_id}"] .mv-layer-menu input[data-id="{layer_id}"]').click()
    page.wait_for_timeout(100)


def _fit_button(page, view_id):
    return page.locator(f'.mv-view[data-view="{view_id}"] a.mv-fit-bounds')


# --- views and the view protocol ---------------------------------------------------


def test_dragging_one_view_moves_the_other(mv_page):
    mount(mv_page)
    _drag(mv_page, "before", 120, 60)
    mv_page.wait_for_timeout(600)  # inertia settles
    (b_lat, b_lng, b_z), (a_lat, a_lng, a_z) = centers(mv_page)
    assert (b_lat, b_lng) != pytest.approx((-1.0, -60.0), abs=pixel_deg(14))
    assert a_z == b_z and (a_lat, a_lng) == pytest.approx((b_lat, b_lng), abs=1.5 * pixel_deg(14))


def test_a_user_drag_is_written_back_once_with_the_last_seen_n(mv_page):
    mount(mv_page)
    before = saves(mv_page)
    _drag(mv_page, "after", -80, 0)
    mv_page.wait_for_timeout(900)  # inertia + the 300 ms viewchange debounce
    views = writes(mv_page, "view")
    assert len(views) == 1, views
    canonical = mv_eval(mv_page, "mv => mv.getView()")
    assert views[0] == {"center": canonical["center"], "zoom": canonical["zoom"], "n": 1, "origin": "user"}, \
        "the frontend never invents an n"
    assert saves(mv_page) - before == 1


def test_a_python_view_with_a_higher_n_moves_every_view(mv_page):
    mount(mv_page)
    py(mv_page, "view", {"center": [10.0, 20.0], "zoom": 8, "n": 2, "origin": "host"})
    _on_view(mv_page, [10.0, 20.0], 8)
    py(mv_page, "view", {"center": [11.0, 21.0], "zoom": 9, "n": 7, "origin": "host"})  # any greater n
    _on_view(mv_page, [11.0, 21.0], 9)
    mv_page.wait_for_timeout(400)
    assert writes(mv_page, "view") == [], "a host view is not written back"


def test_a_python_view_with_a_stale_n_is_ignored(mv_page):
    mount(mv_page)
    py(mv_page, "view", {"center": [10.0, 20.0], "zoom": 8, "n": 5, "origin": "host"})
    py(mv_page, "view", {"center": [30.0, 40.0], "zoom": 5, "n": 3, "origin": "host"})
    py(mv_page, "view", {"center": [30.0, 40.0], "zoom": 5, "n": 5, "origin": "user"})  # an echo of a write-back
    _on_view(mv_page, [10.0, 20.0], 8)


def test_a_python_view_cancels_a_pending_write_back(mv_page):
    mount(mv_page)
    _drag(mv_page, "before", 60, 0, rest_ms=120)  # its viewchange is now pending (300 ms)
    py(mv_page, "view", {"center": [10.0, 20.0], "zoom": 8, "n": 2, "origin": "host"})
    mv_page.wait_for_timeout(700)
    assert writes(mv_page, "view") == []
    _on_view(mv_page, [10.0, 20.0], 8)


def test_a_view_set_while_hidden_is_there_when_shown_in_the_widget(mv_page):
    # Review Focus #2: the analysis screen is display:none when the widget
    # renders; Python sets the view before it is ever shown.
    mount(mv_page, host_style="display:none;width:800px;height:400px")
    py(mv_page, "view", {"center": [5.0, 6.0], "zoom": 11, "n": 2, "origin": "host"})
    mv_page.evaluate("() => document.getElementById('host').setAttribute('style', 'width:800px;height:400px')")
    mv_page.wait_for_timeout(300)
    _on_view(mv_page, [5.0, 6.0], 11)
    sizes = mv_eval(mv_page, "mv => mv.views.map(v => { const s = v.map.getSize(); return [s.x, s.y]; })")
    assert all(x > 100 and y > 100 for x, y in sizes), f"every map was re-measured: {sizes}"
    mv_page.wait_for_timeout(400)
    assert writes(mv_page, "view") == [], "the resize-driven moves are not the reviewer's view"


def test_hiding_the_views_nudges_a_window_resize(mv_page):
    # Review Focus #5: leaving the analysis screen re-measures the workflow map.
    mount(mv_page)
    mv_page.evaluate("() => { window.__resizes = 0; window.addEventListener('resize', () => window.__resizes++); }")
    mv_page.evaluate("() => document.getElementById('host').style.display = 'none'")
    mv_page.wait_for_timeout(200)
    assert mv_page.evaluate("() => window.__resizes") >= 1


def test_the_pages_own_L_is_left_alone(mv_page):
    mount(mv_page)
    assert mv_page.evaluate("() => window.L.outer === true && window.L.PM === undefined")
    assert mv_eval(mv_page, "mv => typeof mv.L.PM === 'object' && typeof mv.L.map === 'function'"), \
        "Geoman extended the bundle's own Leaflet"


# --- layers, basemap and the layer menu ------------------------------------------------


def test_the_layers_trait_adds_replaces_and_removes(mv_page):
    mount(mv_page)
    py(mv_page, "layers", [_tile("a"), _tile("b", view="after")])
    assert _tiles(mv_page, "before") == ["a"] and _tiles(mv_page, "after") == ["a", "b"]
    old = _leaflet_id(mv_page, "before", "a")
    py(mv_page, "layers", [_tile("a", opacity=0.5)])
    assert _leaflet_id(mv_page, "before", "a") != old, "a changed spec is a new Leaflet layer"
    assert _tiles(mv_page, "after") == ["a"], "b is gone"
    py(mv_page, "layers", [])
    assert _tiles(mv_page, "before") == [] and _tiles(mv_page, "after") == []


def test_the_basemap_trait_is_applied_without_a_rebuild(mv_page):
    mount(mv_page)
    mv_page.evaluate("() => { window.__first = document.getElementById('host')._multiview; }")
    py(mv_page, "basemap", {"url": f"{TILE}#base", "attribution": "test"})
    assert _tiles(mv_page, "before") == ["base"] and _tiles(mv_page, "after") == ["base"]
    assert mv_page.evaluate("() => document.getElementById('host')._multiview === window.__first")
    py(mv_page, "basemap", {})
    assert _tiles(mv_page, "before") == [] and _tiles(mv_page, "after") == []


def test_a_layer_menu_toggle_writes_layer_menu_toggles(mv_page):
    mount(mv_page, layers=[_tile("a")])
    _toggle(mv_page, "before", "a")
    assert writes(mv_page, "layer_menu_toggles")[-1] == {"before": {"a": False}}
    assert _tiles(mv_page, "before") == [] and _tiles(mv_page, "after") == ["a"]
    py(mv_page, "layers", [_tile("a", opacity=0.5)])  # a Python update of the same layer
    assert _tiles(mv_page, "before") == [], "the toggle survives the update"


def test_a_pruned_toggle_is_written_back_when_its_layer_disappears(mv_page):
    mount(mv_page, layers=[_tile("a"), _tile("b")])
    _toggle(mv_page, "before", "a")
    py(mv_page, "layers", [_tile("b")])
    assert writes(mv_page, "layer_menu_toggles")[-1] == {"before": {}}


def test_python_seeded_toggles_are_applied_at_render(mv_page):
    mount(mv_page, layers=[_tile("a")], layer_menu_toggles={"after": {"a": False}})
    assert _tiles(mv_page, "before") == ["a"] and _tiles(mv_page, "after") == []
    assert writes(mv_page, "layer_menu_toggles") == [], "nothing was pruned, so nothing is written"


# --- fit bounds ---------------------------------------------------------------------------


def test_fit_bounds_bounds_show_the_button_on_after_only(mv_page):
    mount(mv_page)
    assert _fit_button(mv_page, "before").count() == 0
    assert not _fit_button(mv_page, "after").is_visible(), "hidden while there are no bounds"
    py(mv_page, "fit_bounds_bounds", [[0.0, 0.0], [0.01, 0.01]])
    assert _fit_button(mv_page, "after").is_visible()
    assert _fit_button(mv_page, "before").count() == 0
    py(mv_page, "fit_bounds_bounds", None)
    assert not _fit_button(mv_page, "after").is_visible()


def test_the_fit_bounds_button_fits_the_bounds_and_writes_the_view_back(mv_page):
    mount(mv_page)
    py(mv_page, "fit_bounds_bounds", [[0.0, 0.0], [0.01, 0.01]])
    _fit_button(mv_page, "after").click()
    mv_page.wait_for_timeout(600)  # the fit, then the 300 ms viewchange debounce
    for lat, lng, _ in centers(mv_page):
        assert (lat, lng) == pytest.approx((0.005, 0.005), abs=1e-3)
    (written,) = writes(mv_page, "view")
    assert (written["n"], written["origin"]) == (1, "user"), "a fit is the reviewer's move"
    assert written["center"] == mv_eval(mv_page, "mv => mv.getView().center")


# --- re-render, plugins, theme, labels, cleanup ----------------------------------------------


def test_changing_views_rebuilds_into_three_maps_and_drops_the_old_listeners(mv_page):
    mount(mv_page, layers=[_tile("a")])
    count = listeners(mv_page)
    py(mv_page, "views", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    assert mv_page.locator("#host .leaflet-container").count() == 3
    assert mv_page.locator("#host .mv-widget").count() == 1, "the old build is gone"
    assert mv_eval(mv_page, "mv => mv.views.map(v => v.id)") == ["a", "b", "c"]
    assert _tiles(mv_page, "c") == ["a"], "the layers trait is applied to the new build"
    assert listeners(mv_page) == count, "the old build's model listeners were removed"
    py(mv_page, "view_layout", "grid")
    assert mv_page.locator("#host .mv-root.mv-layout-grid").count() == 1
    assert listeners(mv_page) == count


def test_an_unknown_plugin_name_only_warns(mv_page):
    warnings = []
    mv_page.on("console", lambda msg: msg.type == "warning" and warnings.append(msg.text))
    mount(mv_page, plugins=["nope", "layerMenu"])
    assert "leaflet-multiview: no plugin registered as nope" in warnings
    assert mv_page.locator("#host .leaflet-container").count() == 2
    assert mv_page.locator("#host .mv-error").count() == 0
    assert mv_eval(mv_page, "mv => mv.plugin('layerMenu') !== undefined")


def test_a_plugin_install_error_shows_the_error_strip_and_the_views_keep_working(mv_page):
    mount(mv_page, plugins=["layerMenu", "layerMenu"])
    strip = mv_page.locator("#host .mv-error")
    assert strip.count() == 1
    assert "layerMenu: layerMenu is already installed" in strip.inner_text()
    py(mv_page, "view", {"center": [10.0, 20.0], "zoom": 8, "n": 2, "origin": "host"})
    _on_view(mv_page, [10.0, 20.0], 8)


def test_no_views_shows_an_error_until_views_arrive(mv_page):
    mount(mv_page, views=[])
    assert "needs at least one view" in mv_page.locator("#host .mv-error").inner_text()
    py(mv_page, "views", [{"id": "only"}])
    assert mv_page.locator("#host .mv-error").count() == 0
    assert mv_page.locator("#host .leaflet-container").count() == 1


def test_theme_and_labels_follow_the_traits(mv_page):
    mount(mv_page)
    assert mv_page.locator("#host .mv-root.mv-dark").count() == 0
    py(mv_page, "theme", "dark")
    assert mv_page.locator("#host .mv-root.mv-dark").count() == 1
    py(mv_page, "labels", {"layerMenu.title": "Capas"})
    assert mv_eval(mv_page, "mv => mv.label('layerMenu', 'title')") == "Capas"


def test_the_cleanup_removes_every_listener_and_the_maps(mv_page):
    mount(mv_page, layers=[_tile("a")])
    unmount(mv_page)
    assert listeners(mv_page) == 0
    assert mv_page.locator("#host .leaflet-container").count() == 0
    assert mv_page.locator("#host .mv-widget").count() == 0
    assert mv_page.evaluate("() => document.getElementById('host')._multiview") is None


def _bad_geojson():
    """A layer whose GeoJSON makes ``L.geoJSON`` throw inside ``setLayers``."""
    return {"id": "bad", "view": "all", "kind": "geojson", "data": {"type": "Feature", "geometry": {"type": "Nope", "coordinates": []}},
            "name": "bad", "visible": True, "opacity": 1.0, "z": 1, "group": "alert", "meta": {}}


def test_a_failing_first_mount_shows_the_strip_and_still_cleans_up(mv_page):
    mount(mv_page, layers=[_bad_geojson()])
    assert mv_page.locator("#host .mv-error").inner_text().startswith("leaflet-multiview: ")
    unmount(mv_page)
    assert listeners(mv_page) == 0
    assert mv_page.locator("#host .mv-widget").count() == 0


def test_a_rebuild_after_a_failing_mount_leaves_one_widget(mv_page):
    mount(mv_page, layers=[_bad_geojson()])
    py(mv_page, "views", [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    py(mv_page, "view_layout", "grid")
    assert mv_page.locator("#host .mv-widget").count() == 1
    unmount(mv_page)
    assert listeners(mv_page) == 0
    assert mv_page.locator("#host .mv-widget").count() == 0
