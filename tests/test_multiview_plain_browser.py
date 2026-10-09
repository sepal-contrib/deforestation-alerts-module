"""leaflet-multiview's core on a plain page (script-tag Leaflet + Geoman, no
Jupyter): N-view sync, the canonical view across hides and resizes, layer specs
per view, deny-only visibility rules, controls, labels, theme and teardown."""

import os
import shutil
import subprocess

import pytest

from tests._plain_page import (  # noqa: F401
    PKG,
    centers,
    clear_events,
    drag,
    events,
    make,
    mv_eval,
    pixel_deg,
    plain_browser,
    plain_page,
    plain_server,
    set_host_style,
    view_box,
)

TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"  # one transparent pixel: loads, no network
START = {"center": [-1.0, -60.0], "zoom": 14}
LEAFLET_ERROR = "leaflet-multiview needs Leaflet: pass options.L or load it globally"


def _opts(layout="grid", view=START, **kw):
    options = {"views": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "layout": layout, "basemap": None, "view": view}
    options.update(kw)
    return options


def _tile(id_, view="all", **kw):
    spec = {"id": id_, "view": view, "kind": "tile", "url": TILE, "name": id_, "visible": True, "opacity": 1.0, "z": 1}
    spec.update(kw)
    return spec


def _square(lng, lat, size, style=None):
    props = {"style": style} if style else {}
    ring = [[lng, lat], [lng + size, lat], [lng + size, lat + size], [lng, lat + size], [lng, lat]]
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def _geojson(id_, view="all", features=None, **kw):
    data = {"type": "FeatureCollection", "features": features or [_square(-60.0, -1.0, 0.01)]}
    spec = {"id": id_, "view": view, "kind": "geojson", "data": data, "name": id_, "visible": True, "z": 1,
            "style": {"color": "#0000ff", "weight": 2}}
    spec.update(kw)
    return spec


def _on_canonical(page, view, slack=1.5):
    """Every view knows its real size and sits on ``view`` = {center, zoom}, to
    within ``slack`` pixels."""
    sizes = mv_eval(page, """(mv) => mv.views.map(v => {
        const s = v.map.getSize(), el = v.map.getContainer();
        return [s.x, s.y, el.clientWidth, el.clientHeight];
    })""")
    for sx, sy, w, h in sizes:  # Leaflet re-measured: not a stale (or 0x0) size drawn in a corner
        assert (sx, sy) == (w, h) and w > 0 and h > 0
    lat0, lng0 = view["center"]
    tol = slack * pixel_deg(view["zoom"])
    for lat, lng, zoom in centers(page):
        assert zoom == view["zoom"]
        assert (lat, lng) == pytest.approx((lat0, lng0), abs=tol)


def _shown_on_map(page, view_id, layer_id):
    return mv_eval(page, "(mv, [v, id]) => { const l = mv.layer(v, id); return !!l && mv.views.find(x => x.id === v).map.hasLayer(l); }",
                   [view_id, layer_id])


def _entry_ids(page, view_id):
    return mv_eval(page, "(mv, v) => [...mv.layerEntries(v).keys()]", view_id)


# --- sync ------------------------------------------------------------------------


def test_dragging_view_a_moves_b_and_c(plain_page):
    make(plain_page, _opts())
    box = plain_page.locator('.mv-view[data-view="a"] .mv-map').bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    plain_page.mouse.move(x, y)
    plain_page.mouse.down()
    plain_page.mouse.move(x + 60, y + 30, steps=6)
    plain_page.wait_for_timeout(80)
    (a_lat, a_lng, _), *followers = centers(plain_page)
    assert (a_lat, a_lng) != pytest.approx((-1.0, -60.0), abs=pixel_deg(14))
    for lat, lng, _ in followers:  # the followers track the drag, before the release
        assert (lat, lng) == pytest.approx((a_lat, a_lng), abs=1.5 * pixel_deg(14))
    plain_page.mouse.move(x + 120, y + 60, steps=6)
    plain_page.wait_for_timeout(120)
    plain_page.mouse.up()
    plain_page.wait_for_timeout(700)
    (a_lat, a_lng, a_z), *_ = centers(plain_page)
    view = mv_eval(plain_page, "(mv) => mv.getView()")
    assert view["center"] == pytest.approx([a_lat, a_lng], abs=1e-9), "the leader's release is the canonical view"
    assert view["zoom"] == a_z == 14
    _on_canonical(plain_page, view)
    changes = events(plain_page, "viewchange")
    assert len(changes) == 1, "one debounced viewchange per gesture"
    assert changes[0]["center"] == pytest.approx(view["center"], abs=1e-9)


def test_a_host_set_view_moves_every_view_without_a_viewchange(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv) => mv.setView([10, 20], 8)")
    plain_page.wait_for_timeout(500)
    assert mv_eval(plain_page, "(mv) => mv.getView()") == {"center": [10, 20], "zoom": 8}
    _on_canonical(plain_page, {"center": [10, 20], "zoom": 8})
    assert events(plain_page, "viewchange") == []


def test_a_host_set_view_cancels_a_pending_viewchange(plain_page):
    make(plain_page, _opts())
    drag(plain_page, "b", 80, 0)
    mv_eval(plain_page, "(mv) => mv.setView([3, 4], 9)")  # inside the 300 ms debounce
    plain_page.wait_for_timeout(600)
    assert events(plain_page, "viewchange") == []
    assert mv_eval(plain_page, "(mv) => mv.getView()") == {"center": [3, 4], "zoom": 9}


# --- hidden mounts and resizes (Review Focus #2) -----------------------------


def test_a_view_set_while_hidden_is_there_when_shown(plain_page):
    make(plain_page, _opts(view={"center": [0.0, 0.0], "zoom": 2}), host_style="display:none")
    mv_eval(plain_page, "(mv) => mv.setView([5, 6], 11)")
    set_host_style(plain_page, "")
    assert mv_eval(plain_page, "(mv) => mv.getView()") == {"center": [5, 6], "zoom": 11}
    _on_canonical(plain_page, {"center": [5, 6], "zoom": 11})
    assert events(plain_page, "visibilitychange") == [{"shown": True}]
    assert events(plain_page, "viewchange") == [], "the re-measure is not a gesture"


def test_hide_show_cycles_keep_the_canonical_view(plain_page):
    make(plain_page, _opts())
    drag(plain_page, "a", 90, -40)
    plain_page.wait_for_timeout(500)
    canonical = mv_eval(plain_page, "(mv) => mv.getView()")
    assert canonical["center"] != pytest.approx(START["center"], abs=pixel_deg(14))
    clear_events(plain_page)
    for width in (700, 880, 600):  # each cycle comes back at another size
        set_host_style(plain_page, "display:none", wait_ms=150)
        set_host_style(plain_page, f"width:{width}px;height:500px")
        assert mv_eval(plain_page, "(mv) => mv.getView()") == canonical, "a hide/show never writes the canonical view"
        _on_canonical(plain_page, canonical)
    assert events(plain_page, "visibilitychange") == [{"shown": False}, {"shown": True}] * 3
    plain_page.wait_for_timeout(400)
    assert events(plain_page, "viewchange") == []


def test_a_grid_with_unequal_cells_keeps_every_view_on_the_canonical_center(plain_page):
    make(plain_page, _opts(), host_style="width:900px;height:420px")
    a, c = view_box(plain_page, "a"), view_box(plain_page, "c")
    assert c["width"] > 1.5 * a["width"], "the last view spans the grid's empty cell"
    mv_eval(plain_page, "(mv) => mv.setView([-3, -50], 12)")
    _on_canonical(plain_page, {"center": [-3, -50], "zoom": 12})
    drag(plain_page, "b", -70, 50)
    plain_page.wait_for_timeout(500)
    canonical = mv_eval(plain_page, "(mv) => mv.getView()")
    _on_canonical(plain_page, canonical)
    set_host_style(plain_page, "width:640px;height:520px")
    assert mv_eval(plain_page, "(mv) => mv.getView()") == canonical
    _on_canonical(plain_page, canonical)


def test_a_resize_driven_move_never_emits_viewchange(plain_page):
    make(plain_page, _opts(layout="row"))
    mv_eval(plain_page, "(mv) => mv.setView([12, 34], 10)")
    clear_events(plain_page)
    for style in ("width:600px;height:560px", "width:880px;height:300px", "width:90vw;height:80vh"):
        set_host_style(plain_page, style, wait_ms=100)
    plain_page.set_viewport_size({"width": 700, "height": 500})  # a window resize that resizes the views
    plain_page.wait_for_timeout(700)
    assert events(plain_page, "viewchange") == []
    assert mv_eval(plain_page, "(mv) => mv.getView()") == {"center": [12, 34], "zoom": 10}
    _on_canonical(plain_page, {"center": [12, 34], "zoom": 10})


def test_a_window_resize_event_is_not_a_gesture(plain_page):
    # The app's resizeNudge (T6) dispatches a window "resize" when the views
    # hide. Leaflet's own handler (trackResize) would re-measure each map in a
    # frame of its own and fire a debounced moveend 200 ms later, which would
    # look like the end of a gesture. The core turns it off: the
    # ResizeObserver owns every re-measure.
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv) => mv.setView([7, 8], 12)")
    clear_events(plain_page)
    plain_page.evaluate("""() => {
        document.getElementById('host').style.width = '600px';
        window.dispatchEvent(new Event('resize'));
    }""")
    plain_page.wait_for_timeout(500)
    plain_page.evaluate("""() => {
        document.getElementById('host').style.display = 'none';
        window.dispatchEvent(new Event('resize'));
    }""")
    plain_page.wait_for_timeout(500)
    set_host_style(plain_page, "")
    assert events(plain_page, "viewchange") == []
    assert mv_eval(plain_page, "(mv) => mv.getView()") == {"center": [7, 8], "zoom": 12}
    _on_canonical(plain_page, {"center": [7, 8], "zoom": 12})


def test_fit_bounds_fits_every_view_as_a_user_move(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv) => mv.fitBounds([[0, 0], [0.01, 0.01]])")
    plain_page.wait_for_timeout(500)
    view = mv_eval(plain_page, "(mv) => mv.getView()")
    assert view["center"] == pytest.approx([0.005, 0.005], abs=1e-3)
    _on_canonical(plain_page, view)
    assert len(events(plain_page, "viewchange")) == 1


def test_fit_bounds_while_hidden_applies_when_shown(plain_page):
    make(plain_page, _opts(), host_style="display:none")
    mv_eval(plain_page, "(mv) => mv.fitBounds([[0, 0], [0.01, 0.01]])")
    assert mv_eval(plain_page, "(mv) => mv.getView()") == START, "nothing to fit while hidden"
    set_host_style(plain_page, "", wait_ms=500)
    view = mv_eval(plain_page, "(mv) => mv.getView()")
    assert view["center"] == pytest.approx([0.005, 0.005], abs=1e-3)
    _on_canonical(plain_page, view)


def test_fit_bounds_right_after_show_uses_the_real_size(plain_page):
    # Shown, but not yet re-measured: Leaflet still believes every map is 0x0.
    # The fit must wait for the re-measure, not fit into a 0x0 map (zoom 0).
    make(plain_page, _opts(), host_style="display:none")
    plain_page.evaluate("""() => {
        document.getElementById('host').setAttribute('style', '');
        window.__mv.fitBounds([[0, 0], [0.01, 0.01]]);
    }""")
    plain_page.wait_for_timeout(600)
    view = mv_eval(plain_page, "(mv) => mv.getView()")
    assert view["zoom"] == 15
    assert view["center"] == pytest.approx([0.005, 0.005], abs=1e-3)
    _on_canonical(plain_page, view)
    assert [v["zoom"] for v in events(plain_page, "viewchange")] == [15]


def test_a_set_view_drops_a_fit_deferred_while_restoring(plain_page):
    make(plain_page, _opts())
    view = plain_page.evaluate("""() => new Promise(resolve => {
        const mv = window.__mv;
        // Created after the multiview's observer, so it runs after it in the same
        // delivery: the multiview is restoring, and the fit is deferred a frame.
        const ro = new ResizeObserver(() => {
          ro.disconnect();
          mv.fitBounds([[0, 0], [0.01, 0.01]]);
          mv.setView([10, 20], 8);
          setTimeout(() => resolve(mv.getView()), 600);
        });
        document.getElementById('host').style.width = '600px';
        ro.observe(document.getElementById('host'));
    })""")
    assert view == {"center": [10, 20], "zoom": 8}, "the later setView wins over the deferred fit"
    _on_canonical(plain_page, view)
    assert events(plain_page, "viewchange") == []


# --- layers --------------------------------------------------------------------


def test_layers_targeting_one_view_appear_only_there(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)",
            [_geojson("only-b", view="b"), _tile("a-and-c", view=["a", "c", "nope"]), _tile("everywhere")])
    assert sorted(_entry_ids(plain_page, "a")) == ["a-and-c", "everywhere"]
    assert sorted(_entry_ids(plain_page, "b")) == ["everywhere", "only-b"]
    assert sorted(_entry_ids(plain_page, "c")) == ["a-and-c", "everywhere"]
    assert _shown_on_map(plain_page, "b", "only-b") and not _shown_on_map(plain_page, "a", "only-b")
    old = mv_eval(plain_page, "(mv) => mv.layer('a', 'everywhere')._leaflet_id")
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)", [_tile("everywhere", opacity=0.5)])
    assert mv_eval(plain_page, "(mv) => mv.layer('a', 'everywhere')._leaflet_id") != old, "a changed spec is a new layer"
    assert _entry_ids(plain_page, "b") == ["everywhere"]
    last = events(plain_page, "layerschange")[-1]
    assert [s["id"] for s in last["layers"]] == ["everywhere"]
    assert last["diff"]["b"] == {"added": [], "replaced": ["everywhere"], "removed": ["only-b"]}
    assert last["diff"]["a"] == {"added": [], "replaced": ["everywhere"], "removed": ["a-and-c"]}
    mv_eval(plain_page, "(mv) => mv.setLayers([])")
    assert all(_entry_ids(plain_page, v) == [] for v in "abc")


def test_a_visibility_change_alone_keeps_the_leaflet_layer(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)", [_tile("t")])
    old = mv_eval(plain_page, "(mv) => mv.layer('a', 't')._leaflet_id")
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)", [_tile("t", visible=False)])
    assert mv_eval(plain_page, "(mv) => mv.layer('a', 't')._leaflet_id") == old
    assert not any(_shown_on_map(plain_page, v, "t") for v in "abc")


def test_geojson_overlays_are_pm_ignored_and_not_interactive(plain_page):
    make(plain_page, _opts())
    red = _square(-60.0, -1.0, 0.004, style={"color": "#ff0000"})
    plain = _square(-59.995, -1.0, 0.004)
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)", [_geojson("overlay", features=[red, plain])])
    info = mv_eval(plain_page, """(mv) => {
        const group = mv.layer('a', 'overlay');
        const kids = group.getLayers();
        mv.views.forEach(v => v.map.pm.enableGlobalEditMode());
        return {
          geoman: typeof mv.L.PM === 'object',
          group: [group.options.pmIgnore, group.pm === undefined],
          kids: kids.map(k => [k.options.pmIgnore, k.options.interactive, k.pm === undefined,
                               k.getElement().classList.contains('leaflet-interactive'), k.options.color]),
          vertexMarkers: document.querySelectorAll('#host .marker-icon').length,
        };
    }""")
    assert info["geoman"], "the plain page loads Geoman"
    assert info["group"] == [True, True]
    assert info["kids"] == [[True, False, True, False, "#ff0000"], [True, False, True, False, "#0000ff"]]
    assert info["vertexMarkers"] == 0, "Geoman's global edit mode never touches an overlay"


def test_a_deny_only_visibility_rule_works_through_add_visibility_rule(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)",
            [_tile("plain"), _tile("secret", meta={"secret": True}), _tile("off", visible=False)])
    mv_eval(plain_page, """(mv) => {
        window.__deny = true;
        window.__removeRule = mv.addVisibilityRule((spec, view) =>
            window.__deny && spec.meta && spec.meta.secret && view === 'b' ? false : undefined);
        window.__removeTrue = mv.addVisibilityRule(() => true);  // a rule cannot force a layer on
    }""")
    assert _shown_on_map(plain_page, "a", "secret") and not _shown_on_map(plain_page, "b", "secret")
    assert not any(_shown_on_map(plain_page, v, "off") for v in "abc")
    mv_eval(plain_page, "(mv) => mv.setLayerOverride('b', 'secret', true)")
    assert not _shown_on_map(plain_page, "b", "secret"), "a deny beats the override"
    mv_eval(plain_page, "(mv) => { window.__deny = false; mv.refreshVisibility(); }")
    assert _shown_on_map(plain_page, "b", "secret"), "refreshVisibility re-reads the rules"
    mv_eval(plain_page, "(mv) => { window.__deny = true; mv.refreshVisibility(); window.__removeRule(); }")
    assert _shown_on_map(plain_page, "b", "secret"), "removing the rule shows it again (the override applies)"
    assert mv_eval(plain_page, "(mv) => mv.overrides()") == {"b": {"secret": True}}


def test_overrides_change_identity_only_when_they_change(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, "(mv, specs) => mv.setLayers(specs)", [_tile("t"), _tile("u", view="a")])
    same = mv_eval(plain_page, """(mv) => {
        const before = mv.overrides();
        mv.setLayerOverride('a', 't', false);
        const toggled = mv.overrides();
        mv.setLayerOverride('a', 't', false);  // no change
        const again = mv.overrides();
        mv.setLayers(mv.layers());  // nothing to prune
        const kept = mv.overrides();
        mv.setLayers(mv.layers().filter(s => s.id !== 't'));  // prunes (a, t)
        return [before === toggled, toggled === again, again === kept, kept === mv.overrides()];
    }""")
    assert same == [False, True, True, False]


# --- controls, headers, labels, theme, plugins --------------------------------


def test_headers_show_titles_and_collapse_when_empty(plain_page):
    make(plain_page, _opts(views=[{"id": "a", "title": "Before"}, {"id": "b"}, {"id": "c"}]))
    heights = plain_page.evaluate("""() => ['a', 'b'].map(v =>
        document.querySelector(`.mv-view[data-view="${v}"] .mv-header`).getBoundingClientRect().height)""")
    assert heights[0] > 0 and heights[1] == 0
    assert plain_page.locator('.mv-view[data-view="a"] .mv-title').inner_text() == "Before"
    assert mv_eval(plain_page, "(mv) => mv.views.map(v => [v.id, v.header.classList.contains('mv-header'), v.container.dataset.view])") == [
        ["a", True, "a"], ["b", True, "b"], ["c", True, "c"]]


def test_add_control_targets_views_and_its_remove_takes_them_off(plain_page):
    make(plain_page, _opts())
    mv_eval(plain_page, """(mv) => {
        window.__removeControl = mv.addControl(['a', 'c'], view => {
          const control = mv.L.control({position: 'bottomright'});
          control.onAdd = () => { const d = mv.L.DomUtil.create('div', 'test-control'); d.textContent = view.id; return d; };
          return control;
        });
    }""")
    found = plain_page.evaluate("() => [...document.querySelectorAll('.test-control')].map(d => d.closest('.mv-view').dataset.view + ':' + d.textContent)")
    assert found == ["a:a", "c:c"]
    mv_eval(plain_page, "(mv) => { window.__removeControl(); window.__removeControl(); }")
    assert plain_page.locator(".test-control").count() == 0


def test_labels_theme_and_plugins_through_the_multiview(plain_page):
    make(plain_page, _opts())
    out = mv_eval(plain_page, """(mv) => {
        const demo = {name: 'demo', requires: [], defaults: {n: 1}, labels: {hello: 'Hello'},
          install(m, options) { return {api: {n: () => options.n, hi: () => m.label('demo', 'hello')}, destroy() {}}; }};
        const needy = {name: 'needy', requires: ['missing'], defaults: {}, labels: {}, install: () => ({api: {}})};
        const api = mv.use(demo, {n: 5});
        let err = null;
        try { mv.use(needy); } catch (e) { err = e.message; }
        const before = api.hi();
        mv.setLabels({'demo.hello': 'Hola'});
        mv.setTheme('dark');
        const dark = document.getElementById('host').classList.contains('mv-dark');
        mv.setTheme('light');
        return {n: api.n(), same: mv.plugin('demo') === api, before, after: api.hi(), err, dark,
                light: !document.getElementById('host').classList.contains('mv-dark')};
    }""")
    assert out == {"n": 5, "same": True, "before": "Hello", "after": "Hola",
                   "err": "needy requires missing, which is not installed yet", "dark": True, "light": True}
    assert events(plain_page, "labelschange") == [{"labels": {"demo.hello": "Hola"}}]
    assert events(plain_page, "themechange") == [{"theme": "dark"}, {"theme": "light"}]


def test_a_dark_theme_from_the_options(plain_page):
    make(plain_page, _opts(theme="dark"))
    assert plain_page.evaluate("() => document.getElementById('host').classList.contains('mv-dark')")


def test_bad_views_or_layout_throw_before_touching_the_page(plain_page):
    messages = plain_page.evaluate("""() => {
        const host = document.getElementById('host');
        const attempt = options => { try { window.__mvlib.createMultiView(host, options); return 'no error'; }
                                     catch (e) { return e.message; } };
        return [attempt({views: []}), attempt({views: [{id: 'a'}, {id: 'a'}]}), attempt({views: [{id: 'all'}]}),
                attempt({views: [{id: 'a'}], layout: 'swipe'}), host.childElementCount];
    }""")
    assert messages == [
        "leaflet-multiview needs at least one view",
        'leaflet-multiview: duplicate view id "a"',
        'leaflet-multiview: "all" is reserved and cannot be a view id',
        'leaflet-multiview: unknown layout "swipe" (use "row" or "grid")',
        0,
    ]


# --- Leaflet injection and teardown --------------------------------------------


def test_create_multi_view_without_leaflet_throws(plain_page):
    out = plain_page.evaluate("""() => {
        const host = document.getElementById('host');
        const attempt = options => { try { window.__mvlib.createMultiView(host, options); return 'no error'; }
                                     catch (e) { return e.message; } };
        const saved = window.L;
        window.L = undefined;
        try {
          return [attempt({views: [{id: 'a'}]}), attempt({L: {outer: true}, views: [{id: 'a'}]}), host.childElementCount];
        } finally { window.L = saved; }
    }""")
    assert out == [LEAFLET_ERROR, LEAFLET_ERROR, 0]


def test_the_core_uses_only_the_injected_leaflet(plain_page):
    # Geoman's script-tag build reads the global L at runtime (L.PM, L.Util, ...),
    # so the global cannot be removed: it becomes a proxy that records every
    # property read through it, while the real L is passed in as options.L.
    out = plain_page.evaluate("""([tile]) => {
        const real = window.L;
        const reads = new Set();
        window.L = new Proxy(real, {get(target, key) { reads.add(String(key)); return target[key]; }});
        try {
          const mv = window.__mvlib.createMultiView(document.getElementById('host'),
            {L: real, views: [{id: 'a'}, {id: 'b'}], basemap: {url: tile}, view: {center: [1, 2], zoom: 5}});
          mv.setLayers([{id: 't', view: 'all', kind: 'tile', url: tile, name: 't', visible: true, z: 1},
                        {id: 'g', view: 'b', kind: 'geojson', name: 'g', visible: true, z: 2,
                         data: {type: 'FeatureCollection', features: [{type: 'Feature', properties: {},
                           geometry: {type: 'Point', coordinates: [2, 1]}}]}}]);
          mv.setView([3, 4], 6);
          mv.fitBounds([[0, 0], [1, 1]]);
          const result = {same: mv.L === real, entries: [...mv.layerEntries('b').keys()].sort(), reads: [...reads].sort()};
          mv.destroy();
          return result;
        } finally { window.L = real; }
    }""", [TILE])
    assert out["same"] and out["entries"] == ["g", "t"]
    # Geoman reads PM, Util, DomUtil, ... through it; the core's factories never.
    core_factories = {"map", "tileLayer", "geoJSON", "circleMarker", "latLngBounds", "control"}
    assert core_factories.isdisjoint(out["reads"]), out["reads"]


def test_destroy_removes_maps_and_observers(plain_page):
    plain_page.evaluate("""() => {
        const Native = window.ResizeObserver;
        window.__ro = {live: 0};
        window.ResizeObserver = class extends Native {
          constructor(cb) { super(cb); this.__live = true; window.__ro.live++; }
          disconnect() { if (this.__live) { this.__live = false; window.__ro.live--; } super.disconnect(); }
        };
    }""")
    make(plain_page, _opts())
    assert plain_page.evaluate("() => window.__ro.live") == 1
    drag(plain_page, "a", 60, 0)  # schedules a viewchange
    out = mv_eval(plain_page, """(mv) => {
        mv.destroy();
        mv.destroy();  // a second destroy is a no-op
        const host = document.getElementById('host');
        return {
          children: host.childElementCount,
          classes: host.className,
          handle: host._multiview === undefined,
          maps: mv.views.map(v => v.map.getContainer()._leaflet_id === undefined),
        };
    }""")
    assert out == {"children": 0, "classes": "", "handle": True, "maps": [True, True, True]}
    assert plain_page.evaluate("() => window.__ro.live") == 0, "the ResizeObserver was disconnected"
    set_host_style(plain_page, "display:none", wait_ms=500)
    assert events(plain_page, "viewchange") == [], "the pending viewchange was cancelled"
    assert events(plain_page, "visibilitychange") == []


def test_the_package_node_tests_pass():
    node = shutil.which("node", path=f"{os.path.expanduser('~/.local/bin')}{os.pathsep}{os.environ.get('PATH', '')}")
    if node is None:
        pytest.skip("node is not installed")
    tests = sorted(str(p) for p in (PKG / "test").glob("*.test.mjs"))
    result = subprocess.run([node, "--test", *tests], cwd=PKG, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
