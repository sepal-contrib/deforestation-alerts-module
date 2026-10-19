"""Load the committed multiview bundle into headless Chromium with a fake
anywidget model, so the widget's browser behaviour is tested without a kernel.

- ``mount(page, **state)`` renders the bundle's widget into ``#host`` with
  ``default_state(**state)``; the page keeps its own ``window.L``.
- ``py(page, k, v)`` is a Python-side trait write: the model changes and the
  frontend's listeners fire, as when the kernel sends an update.
- The frontend's own writes (``model.set``) are logged: ``writes(page, k)``
  lists the values it wrote to ``k``, ``saves(page)`` counts ``save_changes``.
  As in Backbone, a frontend ``set`` also fires the frontend's listeners, so
  an echo bug shows up here.
"""

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "gui" / "widget" / "static"
BUNDLE_JS = STATIC / "multiview.js"
BUNDLE_CSS = STATIC / "multiview.css"

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>%s</style></head>
<body style="margin:0"><div id="host" style="width:800px;height:400px"></div></body></html>"""

_MOUNT = r"""async ([src, state]) => {
  window.L = {outer: true};  // the page's own L (jupyter-leaflet's) must survive
  const handlers = {};
  const fire = k => (handlers['change:' + k] || []).slice().forEach(f => f());
  const model = {
    __state: state, __saves: 0, __writes: [],
    get(k) { return this.__state[k]; },
    set(k, v) { this.__writes.push([k, JSON.parse(JSON.stringify(v))]); this.__state[k] = v; fire(k); },
    __py(k, v) { this.__state[k] = v; fire(k); },
    on(ev, f) { for (const e of ev.split(' ')) (handlers[e] = handlers[e] || []).push(f); },
    off(ev, f) { for (const e of ev.split(' ')) handlers[e] = (handlers[e] || []).filter(g => g !== f); },
    __listeners() { return Object.values(handlers).reduce((n, list) => n + list.length, 0); },
    save_changes() { this.__saves++; },
    send() {},
  };
  window.__model = model;
  const url = URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
  const mod = await import(url);
  window.__cleanup = await mod.default.render({model, el: document.getElementById('host')});
  return true;
}"""


def default_state(**kw):
    """Every core trait plus the traits of every mixin ``AlertViews`` has."""
    from gui.widget.alert_views import AlertViews
    from gui.widget.multiview import plugin_order

    s = {
        "views": [{"id": "before"}, {"id": "after"}],
        "view_layout": "row",
        "basemap": {},
        "theme": "light",
        "labels": {},
        "layers": [],
        "plugins": plugin_order(AlertViews),
        "view": {"center": [-1.0, -60.0], "zoom": 14, "n": 1, "origin": "host"},
        "layer_menu_toggles": {},
        "layer_menu_sections": [],
        "basemap_switcher_basemaps": [],
        "basemap_switcher_selected": "",
        "fit_bounds_bounds": None,
        "fit_bounds_views": ["after"],
        "draw": {"fc": {"type": "FeatureCollection", "features": []}, "gen": 0, "seq": 0, "origin": "host"},
        "draw_edit_mode": False,
    }
    s.update(kw)
    return s


@pytest.fixture(scope="module")
def mv_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    if not BUNDLE_JS.is_file():
        pytest.skip("the multiview bundle is not built (cd js/app && npm run build)")
    try:
        pw = sync_api.sync_playwright().start()
    except sync_api.Error as exc:
        pytest.skip(f"Playwright cannot start: {exc}")
    try:
        browser = pw.chromium.launch(headless=True)
    except sync_api.Error as exc:  # no browser installed here
        pw.stop()
        pytest.skip(f"no headless Chromium: {exc}")
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def mv_page(mv_browser):
    page = mv_browser.new_page(viewport={"width": 900, "height": 500})
    page.set_content(_PAGE % BUNDLE_CSS.read_text())
    yield page
    page.close()


def mount(page, host_style=None, **state):
    if host_style is not None:
        page.evaluate("s => document.getElementById('host').setAttribute('style', s)", host_style)
    page.evaluate(_MOUNT, [BUNDLE_JS.read_text(), default_state(**state)])
    page.wait_for_timeout(150)


def unmount(page):
    page.evaluate("() => window.__cleanup()")


def py(page, key, value):
    page.evaluate("([k, v]) => window.__model.__py(k, v)", [key, value])
    page.wait_for_timeout(100)


def state(page, key):
    return page.evaluate("k => window.__model.__state[k]", key)


def saves(page):
    return page.evaluate("() => window.__model.__saves")


def writes(page, key):
    return page.evaluate("k => window.__model.__writes.filter(w => w[0] === k).map(w => w[1])", key)


def listeners(page):
    return page.evaluate("() => window.__model.__listeners()")


def mv_eval(page, js_fn_source, arg=None):
    """Evaluate ``(mv, arg) => ...`` against ``#host._multiview``."""
    return page.evaluate(
        "([src, arg]) => (0, eval)('(' + src + ')')(document.getElementById('host')._multiview, arg)",
        [js_fn_source, arg],
    )


def centers(page):
    return page.evaluate("""() => document.getElementById('host')._multiview.views.map(v => {
        const c = v.map.getCenter(); return [c.lat, c.lng, v.map.getZoom()]; })""")


def pixel_deg(zoom):
    """Degrees of longitude per screen pixel at ``zoom``. Views agree to within
    a pixel, not exactly (a follower's pan is truncated to whole pixels): compare
    view centres with 1.5 * pixel_deg(zoom); only ``mv.getView()`` is exact."""
    return 360.0 / (256 * 2**zoom)


def view_box(page, view_id):
    return page.locator(f'.mv-view[data-view="{view_id}"]').bounding_box()
