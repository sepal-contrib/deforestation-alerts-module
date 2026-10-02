"""Playwright helpers for the gate walks (Voila on a local jupyter-server).

A walk script (``tools/walk/slice<N>_walk.py``) starts the app with
``start_server``, drives it with these helpers, writes a JSON report and
screenshots under its ``--out`` folder, and always calls ``stop_server``.
Selectors are the ones the slice-1 walks proved: ``[data-step-id=N]`` for
rail items, ``[data-step-key=KEY]`` for the pipeline header, ``.right-panel``
for the workflow panel, ``.dialog-card`` for rail dialogs and
``.toast-alert``/``.toast-message`` for pysepal toasts. Never match text
page-wide: detached tiles leave hidden duplicates in the DOM; scope to a
visible container or add ``:visible``.
"""

import math
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENV_BIN = Path.home() / "micromamba" / "envs" / "defo-alerts" / "bin"

TOAST_SPY = """
(() => {
  window.__toasts = [];
  const seen = new Set();
  setInterval(() => {
    for (const a of document.querySelectorAll('.toast-alert')) {
      const m = a.querySelector('.toast-message');
      if (!m) continue;
      const kind = [...a.classList].find(c => c.startsWith('toast-') && c !== 'toast-alert') || '';
      const key = kind + '|' + m.innerText;
      if (!seen.has(key)) { seen.add(key); window.__toasts.push({t: Date.now(), kind, msg: m.innerText}); }
    }
  }, 200);
})();
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    return page.evaluate("""() => { const p = document.querySelector('.right-panel');
        return p ? p.innerText : ''; }""")


def dialog_visible(page):
    return page.evaluate("""() => [...document.querySelectorAll('.dialog-card')]
        .some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0
                     && getComputedStyle(e).visibility !== 'hidden'; })""")


def dialog_text(page):
    return page.evaluate("""() => { const c = [...document.querySelectorAll('.dialog-card')]
        .find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
        return c ? c.innerText : ''; }""")


def close_dialog(page):
    if dialog_visible(page):
        btn = page.locator(".dialog-card .v-card__title button:visible").first
        if btn.count():
            btn.click(timeout=5000)
            page.wait_for_timeout(700)
    if dialog_visible(page):
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)
    return not dialog_visible(page)


def open_app(page, port):
    page.goto(app_url(port), wait_until="load", timeout=180000)
    wait_for(lambda: dialog_visible(page), 300, "About dialog")
    page.wait_for_timeout(2500)
    assert close_dialog(page), "the About dialog did not close"


def open_project(page, name):
    page.locator('[data-step-id="2"]').first.click()
    page.wait_for_timeout(1500)
    # ``has_text`` alone also matches every ancestor wrapper (the dialog body,
    # the table, the row) down to the bare name cell; in DOM (preorder) order
    # that cell is last and has no buttons. Scope to a container that also
    # has the open button, so ``.last`` lands on the row, not the name cell.
    row = page.locator(".dialog-card div:visible", has_text=name).filter(
        has=page.locator("button:has(.mdi-table-eye)")).last
    row.locator("button:has(.mdi-table-eye)").first.click()
    wait_for(lambda: title(page).endswith(f"· {name}"), 60, f"title {name}")
    page.wait_for_timeout(1500)
    if dialog_visible(page):
        close_dialog(page)


def goto_step(page, key, wait=1500):
    page.locator(f'[data-step-key="{key}"]').first.click()
    page.wait_for_timeout(wait)


def click_in_panel(page, text):
    page.locator(f'.right-panel button:visible:has-text("{text}")').first.click()


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
              !e.closest('.defo-map-hidden') && getComputedStyle(e).visibility !== 'hidden'; });
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
