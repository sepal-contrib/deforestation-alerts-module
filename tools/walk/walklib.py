"""Playwright helpers for the gate walks (Voila on a local jupyter-server).

A walk script (``tools/walk/slice<N>_walk.py``) starts the app with
``start_server``, drives it with these helpers, writes a JSON report and
screenshots under its ``--out`` folder, and always calls ``stop_server``.
Selectors are the ones the slice-1 walks proved: ``[data-step-id=N]`` for
rail items, ``[data-step-key=KEY]`` for the pipeline header, ``.right-panel``
for the workflow panel (``PANEL``: that panel, or the analysis screen's
console ``.defo-analysis-right`` while that screen shows), ``.dialog-card`` for
rail dialogs and
``.toast-alert``/``.toast-message`` for pysepal toasts. Every other dialog (a
step's form, Manage projects, a confirmation) is ``MODAL``: the rail dialog is
an active ``v-dialog`` too, so a bare ``.v-dialog--active`` can land on it.
The app starts on the Project rail dialog; ``open_app`` closes it. Labels come
from the English catalogue (``en``), not copies of it. Never match text
page-wide: detached tiles leave hidden duplicates in the DOM; scope to a
visible container or add ``:visible``.

Analysis is a screen of its own (``.defo-analysis-screen``), not a workflow
step. It sits right of MapApp's rail, which stays usable on it (the rail
dialogs, the theme toggle, the language selector); MapApp's main map and
right panel (the pipeline header, the step panels) stay mounted, hidden,
while it shows. ``goto_step(page, "analysis")`` opens it through
Evaluation's *Start analysis*; ``goto_step`` leaves it first with
``back_to_workflow`` for any other step, and ``panel_text`` reads the
screen's console there. The rail helpers work on it as they are.
"""

import json
import math
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# The app environment's bin folder (run_ui.sh's jupyter and voila):
# $DEFO_ALERTS_ENV_BIN, else the micromamba env the runbook creates.
ENV_BIN = Path(os.environ.get("DEFO_ALERTS_ENV_BIN") or Path.home() / "micromamba" / "envs" / "defo-alerts" / "bin")
MESSAGES_EN = REPO / "deforestation_alerts" / "message" / "en"

#: Every open dialog but MapApp's rail dialog (its v-dialog has the content
#: class ``dialog-container``): a step's form, Manage projects, a confirmation.
MODAL = ".v-dialog--active:not(.dialog-container)"
#: The rail dialog's card (About, Project, Overview).
RAIL_DIALOG = ".dialog-card"
#: The Project dialog's Manage projects button (both of its states show it).
MANAGE_BUTTON = "button:has(.mdi-folder-multiple-outline)"
#: The open form's Enter default (``gui.widget.enter_key.ENTER_DEFAULT``):
#: one per form, none while the form's check runs.
ENTER_BUTTON = "button.da-enter-default"
#: The panel the user works in: the workflow's right panel, or the analysis
#: screen's right column (the review console) while that screen shows. MapApp
#: stays mounted beside the analysis screen with its right panel hidden, so a
#: bare ``.right-panel`` would match the hidden workflow panel there.
PANEL = (":is(.defo-analysis-screen--on .defo-analysis-right, .defo-analysis-screen--on .defo-analysis-topbar,"
         " body:not(:has(.defo-analysis-screen--on)) .right-panel)")
#: The analysis screen while it shows (``analysis_tile.build_screen``).
SCREEN_ON = ".defo-analysis-screen--on"

TOAST_SPY = """
(() => {
  window.__toasts = [];
  // keyed by the toast element, not its text: the same message toasted again
  // (a second mask switch) is a new toast and must be recorded again
  const seen = new WeakMap();
  setInterval(() => {
    for (const a of document.querySelectorAll('.toast-alert')) {
      const m = a.querySelector('.toast-message');
      if (!m) continue;
      const kind = [...a.classList].find(c => c.startsWith('toast-') && c !== 'toast-alert') || '';
      const key = kind + '|' + m.innerText;
      if (seen.get(a) !== key) { seen.set(a, key); window.__toasts.push({t: Date.now(), kind, msg: m.innerText}); }
    }
  }, 200);
})();
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def en(dotted: str) -> str:
    """The English text of the catalogue key ``dotted`` (a ``gui.i18n.t``
    key such as ``steps.alerts.add``): a walk clicks the label the app shows,
    so a reworded label does not break it. A plural node is not a text."""
    for path in sorted(MESSAGES_EN.glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node
    raise KeyError(dotted)


def app_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/voila/render/ui.ipynb"


def start_server(port: int, data_dir: Path, log_path: Path) -> subprocess.Popen:
    """``./run_ui.sh --port PORT`` with the walk's data dir, in its own process group."""
    env = dict(os.environ)
    env["DEFORESTATION_ALERTS_DATA_DIR"] = str(data_dir)
    env["SOLARA_ASSETS_PROXY"] = "true"
    env["PATH"] = f"{ENV_BIN}:{env.get('PATH', '')}"
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "w")
    proc = subprocess.Popen(["./run_ui.sh", "--port", str(port)], cwd=REPO, env=env,
                            stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api", timeout=2)
            return proc
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(1)
    stop_server(proc)
    raise RuntimeError(f"jupyter-server did not start on {port}; see {log_path}")


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def new_page(playwright, report: dict):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    page.add_init_script(TOAST_SPY)
    report.setdefault("console", [])
    report.setdefault("page_errors", [])
    report.setdefault("bad_responses", [])
    page.on("console", lambda m: report["console"].append(f"{m.type}: {m.text[:400]}")
            if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: report["page_errors"].append(str(e)[:400]))
    page.on("response", lambda r: report["bad_responses"].append((r.status, r.url[:160]))
            if r.status >= 400 else None)
    return browser, page


#: Page errors the walks' environment raises on every load, before the app
#: renders. Not the app's: no app code (gui/, deforestation_alerts/, tools/)
#: references MathJax, and nothing loads it. Where the bare reference comes
#: from is unidentified: the default ``solara.html.j2`` loads
#: ``main-vuetify.js``, not the ``main.js`` that calls
#: ``voila.renderMathJax()``, and solara's Markdown guards its MathJax use with
#: ``window.MathJax &&``.
AMBIENT_PAGE_ERRORS = ("MathJax is not defined",)


def app_page_errors(report: dict) -> list:
    """The page errors ``new_page`` recorded, less the ambient ones: a walk
    that gates on page errors gates on these."""
    return [e for e in report.get("page_errors", []) if e.strip() not in AMBIENT_PAGE_ERRORS]


def wait_for(fn, timeout, what, poll=0.5):
    t0, last = time.time(), None
    while time.time() - t0 < timeout:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = f"exc: {e}"
        time.sleep(poll)
    raise AssertionError(f"timeout waiting for {what} (last={last!r})")


def shot(page, out_dir, name):
    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(out_dir) / f"{name}.png"), full_page=False)
    except Exception as e:  # noqa: BLE001
        log(f"screenshot {name} failed: {e}")


def now_ms(page) -> int:
    return page.evaluate("() => Date.now()")


def toasts_since(page, t0_ms=0):
    try:
        return [x for x in page.evaluate("() => window.__toasts || []") if x["t"] >= t0_ms]
    except Exception:  # noqa: BLE001
        return []


def title(page):
    loc = page.locator("span.title.font-weight-medium").first
    return loc.inner_text() if loc.count() else ""


def panel_text(page):
    """The text of the panel the user works in (``PANEL``): on the analysis
    screen, its top bar (the queue and the navigation) and its console."""
    return page.evaluate("s => [...document.querySelectorAll(s)].map(p => p.innerText).join('\\n')", PANEL)


def dialog_visible(page):
    return page.evaluate("""() => [...document.querySelectorAll('.dialog-card')]
        .some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0
                     && getComputedStyle(e).visibility !== 'hidden'; })""")


def dialog_text(page):
    return page.evaluate("""() => { const c = [...document.querySelectorAll('.dialog-card')]
        .find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
        return c ? c.innerText : ''; }""")


def _closed(page, timeout=5.0) -> bool:
    """Wait up to ``timeout`` s for the rail dialog to close; whether it did."""
    t0 = time.time()
    while dialog_visible(page):
        if time.time() - t0 > timeout:
            return False
        time.sleep(0.1)
    return True


def close_dialog(page):
    if dialog_visible(page):
        btn = page.locator(".dialog-card .v-card__title button:visible").first
        if btn.count():
            btn.click(timeout=5000)
            _closed(page)
    if dialog_visible(page):
        page.keyboard.press("Escape")
        _closed(page)
    return not dialog_visible(page)


def open_app(page, port):
    """Load the app. It starts on the Project rail dialog (rail step 2): wait
    for its Manage projects button, then close the dialog."""
    page.goto(app_url(port), wait_until="load", timeout=180000)
    manage = page.locator(f"{RAIL_DIALOG} {MANAGE_BUTTON}:visible").first
    wait_for(lambda: dialog_visible(page) and manage.count(), 300, "the Project dialog")
    page.wait_for_timeout(2500)
    assert close_dialog(page), "the Project dialog did not close"


def open_manage(page):
    """Open Manage projects from the Project rail dialog, opening that first.
    Another rail dialog disables the rail, so it is closed before the click."""
    manage = page.locator(f"{RAIL_DIALOG} {MANAGE_BUTTON}:visible").first
    if not manage.count():
        close_dialog(page)
        page.locator('[data-step-id="2"]').first.click()
        wait_for(lambda: manage.count(), 30, "the Project dialog")
    manage.click()
    wait_for(lambda: page.locator(MODAL).count(), 15, "Manage projects")
    page.wait_for_timeout(800)


def manage_item(page, name):
    """The Manage projects row of ``name``: its title is the name, then an
    "open" chip on the open project (``north_s6`` must not match ``north_s6_b``)."""
    title = page.locator(".v-list-item__title", has_text=re.compile(rf"^\s*{re.escape(name)}(\s|$)"))
    return page.locator(f"{MODAL} .v-list-item").filter(has=title).first


def open_project(page, name):
    """Project → Manage projects → select ``name`` → Open (the Enter default).
    The app then names the project in its title and closes the dialog."""
    open_manage(page)
    item = manage_item(page, name)
    wait_for(lambda: item.count() and item.is_visible(), 30, f"{name} listed in Manage projects")
    # The open project is selected already: a click would unselect it.
    if "v-list-item--active" not in (item.get_attribute("class") or ""):
        item.locator(".v-list-item__title").first.click()
    open_ = page.locator(f"{MODAL} {ENTER_BUTTON}:visible").last
    wait_for(lambda: open_.count() and open_.is_enabled(), 15, f"Open enabled for {name}")
    open_.click()
    wait_for(lambda: title(page).endswith(f"· {name}"), 60, f"title {name}")
    page.wait_for_timeout(1500)
    if dialog_visible(page):
        close_dialog(page)


def back_to_workflow(page, wait=1000):
    """Leave the analysis screen through its *← Workflow* button, when it
    shows; on the workflow, do nothing. MapApp's right panel (the pipeline
    header, the step panels) and main map are hidden while that screen shows;
    its rail is not, so the rail helpers (dialogs, theme, language) need no
    call to this."""
    if not page.locator(SCREEN_ON).count():
        return
    page.locator(".defo-back-to-workflow:visible").first.click()
    wait_for(lambda: not page.locator(SCREEN_ON).count(), 30, "the workflow back")
    page.wait_for_timeout(wait)


def step_active(page, key) -> bool:
    """Whether the Workflow panel shows step ``key``: the pipeline header's
    position reads "<its number> / <steps>" (any language)."""
    return page.evaluate("""key => {
        const keys = [...document.querySelectorAll('[data-step-key]')].map(e => e.dataset.stepKey);
        const label = `${keys.indexOf(key) + 1} / ${keys.length}`;
        return keys.includes(key) && [...document.querySelectorAll('.right-panel span')].some(e =>
            e.childElementCount === 0 && e.textContent.trim() === label && e.getBoundingClientRect().width > 0);
    }""", key)


def goto_step(page, key, wait=1500):
    """A workflow step by key, from the workflow or from the analysis screen.

    ``"analysis"`` is not a step: it is the analysis screen, which
    Evaluation's *Start analysis* opens (enabled once an alert set exists).
    """
    back_to_workflow(page)
    if key == "analysis":
        page.locator('[data-step-key="evaluators"]').first.click()
        # The button is found once the panel shows Evaluation: one found on
        # the step before it hides before the click lands.
        wait_for(lambda: step_active(page, "evaluators"), 30, "the Evaluation step")
        page.wait_for_timeout(500)
        start = page.locator(".defo-start-analysis:visible").first
        wait_for(lambda: start.count() and start.is_enabled(), 30, "Start analysis enabled")
        start.click()
        wait_for(lambda: page.locator(SCREEN_ON).count(), 30, "the analysis screen")
        page.wait_for_timeout(wait)
        return
    page.locator(f'[data-step-key="{key}"]').first.click()
    page.wait_for_timeout(wait)


# ---------------------------------------------------------------- map math
_PATTERNS = [
    (re.compile(r"[?&]x=(\d+)&y=(\d+)&z=(\d+)"), ("x", "y", "z")),
    (re.compile(r"/tile/(\d+)/(\d+)/(\d+)"), ("z", "y", "x")),
    (re.compile(r"/(\d+)/(\d+)/(\d+)(?:@2x)?\.(?:png|jpg|jpeg|webp)"), ("z", "x", "y")),
]


def _parse(src):
    for rx, order in _PATTERNS:
        m = rx.search(src)
        if m:
            d = dict(zip(order, (int(g) for g in m.groups())))
            return d["z"], d["x"], d["y"]
    return None


def map_tiles(page):
    return page.evaluate("""() => {
      const cs = [...document.querySelectorAll('.leaflet-container')].filter(e => {
          const r = e.getBoundingClientRect(); return r.width > 300 && r.height > 300 &&
              !e.closest('.defo-analysis-screen') && getComputedStyle(e).visibility !== 'hidden'; });
      cs.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
      const c = cs[0];
      if (!c) return [];
      return [...c.querySelectorAll('img.leaflet-tile-loaded')].map(t => {
          const r = t.getBoundingClientRect();
          const op = parseFloat(getComputedStyle(t.closest('.leaflet-tile-container') || t).opacity || '1');
          return {src: t.src, x: r.left, y: r.top, w: r.width, h: r.height, op};
      }).filter(t => t.w > 0);
    }""")


def _global_px(lon, lat, z):
    n = 256 * (2 ** z)
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def _current_zoom_ref(page):
    tiles = [t for t in map_tiles(page) if _parse(t["src"])]
    if not tiles:
        return None
    zs = {}
    for t in tiles:
        zs.setdefault(_parse(t["src"])[0], []).append(t)
    z = max(zs, key=lambda k: (len([t for t in zs[k] if t["op"] > 0.99]), k))
    return z, zs[z][0]


def lonlat_to_screen(page, lon, lat):
    ref = _current_zoom_ref(page)
    if ref is None:
        raise AssertionError("no map tiles to locate the view")
    z, t = ref
    _, tx, ty = _parse(t["src"])
    gx, gy = _global_px(lon, lat, z)
    scale = t["w"] / 256.0
    return z, t["x"] + (gx - tx * 256) * scale, t["y"] + (gy - ty * 256) * scale


def _drag(page, x0, y0, x1, y1):
    page.mouse.move(x0, y0)
    page.mouse.down()
    for i in range(1, 13):
        page.mouse.move(x0 + (x1 - x0) * i / 12, y0 + (y1 - y0) * i / 12)
        page.wait_for_timeout(20)
    page.mouse.up()
    page.wait_for_timeout(700)


def fly_to(page, lon, lat, zoom, free=(70, 20, 910, 980)):
    cx, cy = (free[0] + free[2]) / 2, (free[1] + free[3]) / 2
    for _ in range(30):
        z, x, y = lonlat_to_screen(page, lon, lat)
        if not (free[0] < x < free[2] and free[1] < y < free[3]):
            page.locator(".leaflet-control-zoom-out:visible").first.click()
            page.wait_for_timeout(900)
            continue
        if abs(x - cx) > 40 or abs(y - cy) > 40:
            _drag(page, x, y, cx, cy)
            continue
        if z >= zoom:
            return z, x, y
        page.mouse.dblclick(x, y)
        page.wait_for_timeout(1200)
    raise AssertionError("fly_to did not converge")
