"""Drive ``js/leaflet-multiview/examples/plain.html`` in headless Chromium: the
package with no Jupyter and no bundle.

ES modules do not load from ``file://``, so a background ``http.server`` thread
serves the package root; the example reaches Leaflet and Geoman under
``../node_modules`` (the package's dev dependencies, ``npm install`` in
``js/leaflet-multiview``). ``plain.html?manual`` imports ``src/index.js`` into
``window.__mvlib`` and builds nothing; ``make(page, options, plugins)`` builds a
multiview into ``#host`` and keeps it in ``window.__mv``.
"""

import functools
import http.server
import threading
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "js" / "leaflet-multiview"

#: The events ``make`` records into ``window.__events`` as ``[name, payload]``.
RECORDED_EVENTS = ("viewchange", "visibilitychange", "layerschange", "labelschange", "themechange", "layertoggle",
                   "basemapchange")


class _Handler(http.server.SimpleHTTPRequestHandler):
    # A module script served with a non-JavaScript type is refused, and the
    # system mime table is not guaranteed to know .js, so the types are explicit.
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".html": "text/html",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }

    def log_message(self, *args):  # keep the pytest output clean
        pass


@pytest.fixture(scope="module")
def plain_server():
    if not (PKG / "node_modules" / "leaflet" / "dist" / "leaflet.js").is_file():
        pytest.skip("js/leaflet-multiview/node_modules is missing: cd js/leaflet-multiview && npm install")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(PKG)))
    thread = threading.Thread(target=server.serve_forever, name="plain-page-server", daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def plain_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except sync_api.Error as exc:  # no browser installed here
        pw.stop()
        pytest.skip(f"no headless Chromium: {exc}")
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def plain_page(plain_browser, plain_server):
    page = plain_browser.new_page(viewport={"width": 900, "height": 600})
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{plain_server}/examples/plain.html?manual")
    page.wait_for_function("() => window.__mvlib !== undefined", timeout=15000)
    load_error = page.evaluate("() => window.__mvError || null")
    if load_error:
        page.close()
        pytest.fail(f"examples/plain.html could not import src/index.js:\n{load_error}")
    yield page
    page.close()
    assert errors == [], f"uncaught errors in the page: {errors}"


_MAKE = r"""([options, plugins, hostStyle, recorded]) => {
  const host = document.getElementById('host');
  if (window.__mv) { window.__mv.destroy(); window.__mv = undefined; }
  if (hostStyle !== null) host.setAttribute('style', hostStyle);
  const mv = window.__mvlib.createMultiView(host, options);
  window.__mv = mv;
  window.__events = [];
  for (const name of recorded) {
    mv.on(name, payload => window.__events.push([name, JSON.parse(JSON.stringify(payload ?? null))]));
  }
  for (const [name, opts] of plugins) {
    const plugin = window.__mvlib[name];
    if (!plugin) throw new Error('src/index.js does not export ' + name);
    mv.use(plugin, opts || {});
  }
}"""


def make(page, options, plugins=(), host_style=None):
    """``createMultiView(#host, options)``, then ``mv.use(__mvlib[name], opts)`` for
    each ``(name, opts)``; the result is ``window.__mv``. ``host_style`` replaces
    ``#host``'s style attribute first (the page's default is 880x560)."""
    page.evaluate(_MAKE, [options, [[name, opts] for name, opts in plugins], host_style, list(RECORDED_EVENTS)])
    page.wait_for_timeout(150)


def set_host_style(page, style, wait_ms=300):
    """Replace ``#host``'s style attribute (hide, show, resize), then wait."""
    page.evaluate("s => document.getElementById('host').setAttribute('style', s)", style)
    page.wait_for_timeout(wait_ms)


def mv_eval(page, js_fn_source, arg=None):
    """Evaluate ``(mv, arg) => ...`` against ``window.__mv``."""
    return page.evaluate("(arg) => (" + js_fn_source + ")(window.__mv, arg)", arg)


def events(page, name):
    """The payloads of the recorded ``name`` events, oldest first."""
    return [payload for ev, payload in page.evaluate("() => window.__events") if ev == name]


def clear_events(page):
    page.evaluate("() => { window.__events.length = 0; }")


def centers(page):
    """``[[lat, lng, zoom], ...]`` of every view, in view order."""
    return page.evaluate("""() => window.__mv.views.map(v => {
        const c = v.map.getCenter();
        return [c.lat, c.lng, v.map.getZoom()];
    })""")


def pixel_deg(zoom):
    """Degrees of longitude per screen pixel at ``zoom``: the tolerance between
    views. A follower's setView pans by whole pixels (Leaflet truncates the
    offset), so views agree to within a pixel, not exactly."""
    return 360.0 / (256 * 2**zoom)


def view_box(page, view_id):
    """The bounding box of ``.mv-view[data-view=<id>]``."""
    return page.locator(f'.mv-view[data-view="{view_id}"]').bounding_box()


def drag(page, view_id, dx, dy):
    """A mouse drag from the centre of that view's ``.mv-map``. It pauses before
    releasing, so Leaflet adds no inertia and the gesture ends at the release."""
    box = page.locator(f'.mv-view[data-view="{view_id}"] .mv-map').bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=12)
    page.wait_for_timeout(120)
    page.mouse.up()
