"""Analysis views walk: the two views are one ``AlertViews`` widget
(``leaflet-multiview``) on the analysis screen, on a copy of a reviewed
project.

    python -m tools.walk.panes_walk --port 8953 --out /tmp/claude-1000/panes-walk

Checks, each an entry in the JSON report (``phases.panes``) with a screenshot:

 1. both views load tiles, and no ``.mv-error`` strip is shown
 2. Python's layers arrive: the alert box (``alert-bb``) and both True Color
    images (``before:True Color``, ``after:True Color``); the box is on both
    views; the layer menus carry the translated title
 3. the zoom-to-alert button is on the after view only, and the alert box is
    really visible (inside the view, a visible stroke, no pane above it)
 4. a drag on the before view moves the after view with it (to within 1.5
    pixels) and moves no view header; the zoom-to-alert button brings both
    views back over the alert box
 5. *Start Drawing*: edit mode on, the toolbar and the hint on both views,
    the alert box hidden, the stored polygon's vertices on show
 6. a polygon drawn on the before view shows on the after view too, and the
    footer asks for the drawing to be saved (Python heard the edit)
 7. *Stop Drawing*: edit mode off, no toolbar, no hint, the drawing cleared,
    the alert box back, and the footer says the alert has unsaved changes

Then ``check_screen`` (phase ``screen``, Review Focus 5):

 8. the analysis screen sits right of MapApp's rail, full height; the rail
    and its collapse arrow are on top of it; MapApp's main map, its right
    panel and the floating legend are hidden
 9. a window resize while the screen shows, then *← Workflow*: back on the
    workflow, the main map has loaded tiles over its whole area (the views'
    hide nudge re-measured it), the right panel is back and shows
    Questionnaire
10. *Start analysis* again: the screen beside the rail, tiles on both views;
    the right panel closed once on the screen: its tab stays hidden there and
    is back on the workflow

Then ``check_rail`` (phase ``rail``, R30 and Review Focus 4): MapApp's rail,
used on the analysis screen:

11. a rail dialog (About) opens over the screen and closes; the screen stays
12. the rail's language selector, French then English: the screen stays, and
    its top bar and the views' labels (each layer menu's title, the
    zoom-to-alert button) follow each language
13. the rail's theme toggle, dark then light: the screen stays, and the
    screen and the views follow each theme

It needs Earth Engine and sepal.io (the imagery), so it runs outside a
sandbox. ``run_ui.sh`` reads ``.env`` from the checkout it runs in.
"""

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.questionnaire_walk import PROJECT, SEED, THEME_SWAP_ERROR, goto_analysis

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_panes"
HOST = ".analysis-views"

#: The views' state, read through the debug handle ``_multiview`` the widget
#: sets on its element (``el._multiview = mv``).
PANES_STATE = """() => {
  const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent)
      || document.querySelector('.analysis-views');
  if (!host) return null;
  const mv = host._multiview || (host.querySelector('.mv-root') || {})._multiview;
  if (!mv) return null;
  const draw = mv.plugin('draw');
  const per = fn => mv.views.map(fn);
  return {
    view_ids: per(v => v.id),
    centers: per(v => { const c = v.map.getCenter(); return [c.lat, c.lng, v.map.getZoom()]; }),
    layer_ids: mv.layers().map(s => s.id),
    tiles_loaded: per(v => v.container.querySelectorAll('.leaflet-tile-loaded').length),
    bb_on: per(v => v.map.hasLayer(mv.layer(v.id, 'alert-bb'))),
    bb_paths: per(v => v.container.querySelectorAll('.leaflet-overlay-pane path[stroke="yellow"]').length),
    drawn: per(v => { let n = 0; v.map.eachLayer(l => { if (l._mvDrawId !== undefined) n++; }); return n; }),
    vertex_markers: per(v => v.container.querySelectorAll('.leaflet-marker-icon.marker-icon').length),
    toolbars: per(v => v.container.querySelectorAll('.leaflet-pm-toolbar').length),
    hints: per(v => [...v.container.querySelectorAll('.mv-draw-hint')].map(e => e.textContent.trim())),
    menu_titles: per(v => [...v.container.querySelectorAll('.mv-layer-menu .mv-layers-title')]
        .map(e => e.textContent.trim())),
    fit_shown: per(v => v.container.querySelectorAll('.mv-fit-bounds-bar:not(.mv-fit-bounds--none)').length),
    errors: [...host.querySelectorAll('.mv-error')].filter(e => e.offsetParent).map(e => e.textContent),
    edit_mode: draw ? draw.editMode() : null,
    drawing_n: draw ? draw.features().features.length : null,
  };
}"""

#: ``[x, y]`` (page px) of ``[lat, lng]`` on view ``id``.
_XY = """([id, lat, lng]) => {
  const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent);
  const mv = host._multiview || host.querySelector('.mv-root')._multiview;
  const v = mv.views.find(x => x.id === id);
  const p = v.map.latLngToContainerPoint([lat, lng]);
  const r = v.map.getContainer().getBoundingClientRect();
  return [r.left + p.x, r.top + p.y];
}"""

#: The alert box's centre ``[lat, lng]``, from its layer on the after view.
_BB_CENTER = """() => {
  const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent);
  const mv = host._multiview || host.querySelector('.mv-root')._multiview;
  const c = mv.layer('after', 'alert-bb').getBounds().getCenter();
  return [c.lat, c.lng];
}"""


#: Whether the alert box can be seen on each view: its path lies inside the map
#: box, has a size and a visible stroke, and no pane painted above its pane
#: covers it (pysepal's base.css forces every ``.leaflet-pane`` to
#: ``z-index: 2 !important``, so panes stack by DOM order). The imagery's tile
#: pane must be above the basemap pane the same way.
_BB_VISIBLE = """() => {
  const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent);
  const mv = host._multiview || host.querySelector('.mv-root')._multiview;
  const z = e => { const v = parseInt(getComputedStyle(e).zIndex, 10); return Number.isNaN(v) ? 0 : v; };
  const above = (a, b) => z(a) !== z(b) ? z(a) > z(b)
      : !!(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING);
  return mv.views.map(v => {
    const box = v.container.querySelector('.mv-map').getBoundingClientRect();
    const path = v.container.querySelector('.leaflet-overlay-pane path[stroke="yellow"]');
    const r = path ? path.getBoundingClientRect() : null;
    const cs = path ? getComputedStyle(path) : null;
    const pane = c => v.container.querySelector('.leaflet-' + c + '-pane');
    const base = pane('mvBasemap');
    return {
      id: v.id,
      inside: !!r && r.width > 0 && r.height > 0 && r.right > box.left && r.left < box.right
          && r.bottom > box.top && r.top < box.bottom,
      stroke: cs ? [cs.stroke, cs.strokeOpacity, cs.strokeWidth, cs.display, cs.visibility] : null,
      box_covered: !!base && above(base, pane('overlay')),
      imagery_covered: !!base && above(base, pane('tile')),
    };
  });
}"""


def panes_state(page):
    """The views' state (``PANES_STATE``), or None before the widget is up."""
    return page.evaluate(PANES_STATE)


def header_tops(page):
    """The top (px) of each visible view header (``.analysis-mapbox``)."""
    return page.evaluate("""() => [...document.querySelectorAll('.analysis-mapbox')]
        .filter(e => e.offsetParent).map(e => e.getBoundingClientRect().top)""")


def pixel_deg(zoom):
    """Degrees of longitude per screen pixel at ``zoom``."""
    return 360.0 / (256 * 2 ** zoom)


def same_center(a, b, pixels=1.5):
    """Two ``[lat, lng, zoom]`` within ``pixels`` of each other: a follower's
    pan is truncated to whole pixels, so views agree to about one pixel."""
    tol = pixels * pixel_deg(a[2])
    return a[2] == b[2] and abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def drag_view(page, view_id, dx, dy):
    """A mouse drag from the middle of view ``view_id``'s map, then 800 ms
    (the views write their view back 300 ms after a gesture)."""
    box = page.locator(f'{HOST} .mv-view[data-view="{view_id}"] .mv-map').bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=15)
    page.wait_for_timeout(150)  # hold still, so the release ends the gesture where it is
    page.mouse.up()
    page.wait_for_timeout(800)


def draw_triangle(page, view_id, center, d=0.001):
    """Draw a triangle around ``center`` (``[lat, lng]``) on ``view_id`` with
    the toolbar's *Draw polygon* button: three clicks, then the first vertex
    again to close it."""
    page.locator(f'{HOST} .mv-view[data-view="{view_id}"] .leaflet-pm-icon-polygon').first.click()
    page.wait_for_timeout(300)
    lat, lng = center
    for p_lat, p_lng in ((lat - d, lng - d), (lat - d, lng + d), (lat + d, lng), (lat - d, lng - d)):
        x, y = page.evaluate(_XY, [view_id, p_lat, p_lng])
        page.mouse.click(x, y)
        page.wait_for_timeout(120)
    page.wait_for_timeout(1000)


def footer_text(page):
    return page.evaluate("""() => { const f = [...document.querySelectorAll('.defo-analysis-footer')]
        .find(e => e.offsetParent); return f ? f.innerText : ''; }""")


def check_panes(page, out, report):
    """The checks of the module docstring, recorded in ``report``."""
    # 1. tiles on both views, no error strip
    w.wait_for(lambda: (s := panes_state(page)) and len(s["tiles_loaded"]) == 2
               and all(n > 0 for n in s["tiles_loaded"]), 90, "tiles on both views")
    s = panes_state(page)
    report["view_ids"] = s["view_ids"]
    assert s["view_ids"] == ["before", "after"], s["view_ids"]
    assert s["errors"] == [], s["errors"]

    # 2. Python's layers
    wanted = ("alert-bb", "before:True Color", "after:True Color")
    w.wait_for(lambda: all(i in panes_state(page)["layer_ids"] for i in wanted), 240,
               "the alert box and both True Color images")
    page.wait_for_timeout(1500)
    s = panes_state(page)
    report["layers"] = s["layer_ids"]
    report["bb_paths"] = s["bb_paths"]
    assert s["bb_on"] == [True, True], s["bb_on"]
    assert s["menu_titles"] == [[w.en("analysis.panes.layers")]] * 2, s["menu_titles"]
    w.shot(page, out, "01_layers")

    # 3. zoom-to-alert on the after view only
    assert s["fit_shown"] == [0, 1], s["fit_shown"]

    # 3b. the alert box can be seen (not just counted), and the imagery is above the basemap
    vis = page.evaluate(_BB_VISIBLE)
    report["bb_visible"] = vis
    for v in vis:
        assert v["inside"], ("the alert box is off its view or empty", v)
        assert v["stroke"] and v["stroke"][0] != "none" and float(v["stroke"][1]) > 0 \
            and v["stroke"][3] != "none" and v["stroke"][4] == "visible", ("the alert box has no visible stroke", v)
        assert not v["box_covered"], ("a pane above the overlay pane covers the alert box", v)
        assert not v["imagery_covered"], ("the basemap pane covers the imagery's tile pane", v)

    # 4. a drag syncs the views and moves no header; zoom-to-alert comes back
    tops = header_tops(page)
    assert len(tops) == 2, tops
    drag_view(page, "before", 150, 80)
    s = panes_state(page)
    report["centers_after_drag"] = s["centers"]
    assert same_center(*s["centers"]), s["centers"]
    assert header_tops(page) == tops, "a header moved when the view was dragged"
    w.shot(page, out, "02_after_drag")
    page.locator(f'{HOST} .mv-view[data-view="after"] a.mv-fit-bounds').click()
    page.wait_for_timeout(1500)
    s = panes_state(page)
    bb = page.evaluate(_BB_CENTER)
    report["centers_after_fit"] = s["centers"]
    assert same_center(*s["centers"]), s["centers"]
    assert same_center(s["centers"][1], [bb[0], bb[1], s["centers"][1][2]], pixels=3), (s["centers"], bb)

    # 5. Start Drawing
    ph.open_section(page, w.en("analysis_tile.sections.refine"))
    page.get_by_role("button", name=w.en("analysis_tile.edition_labels.start_edit_button")).click()
    w.wait_for(lambda: panes_state(page)["edit_mode"] is True, 20, "edit mode on")
    page.wait_for_timeout(1000)  # Geoman makes a rebuilt drawing editable after its ~100 ms throttle
    s = panes_state(page)
    report["start"] = s
    assert all(n >= 1 for n in s["toolbars"]), s["toolbars"]
    assert s["hints"] == [[w.en("analysis.panes.vertex_hint")]] * 2, s["hints"]
    assert s["bb_on"] == [False, False], "the alert box is not hidden while editing"
    if s["drawing_n"]:
        assert all(n > 0 for n in s["vertex_markers"]), s["vertex_markers"]
    else:
        report["start_vertices"] = "skipped: the alert has no stored polygon"
    w.shot(page, out, "03_edit_mode")

    # 6. a polygon drawn on the before view, on both views, heard by Python
    before_n = s["drawing_n"]
    bb = page.evaluate(_BB_CENTER)
    draw_triangle(page, "before", bb)
    s = panes_state(page)
    report["drawn"] = s
    assert s["drawing_n"] == before_n + 1, (before_n, s["drawing_n"])
    assert s["drawn"][0] == s["drawn"][1] == s["drawing_n"], s["drawn"]
    # ``on_drawing_edit`` -> ``drawing_changed``: the footer asks for *Save drawing*
    w.wait_for(lambda: w.en("analysis_tile.edition_labels.unapplied_drawing") in footer_text(page), 20,
               "the footer asking for the drawing to be saved")
    w.shot(page, out, "04_drawn")

    # 7. Stop Drawing
    page.get_by_role("button", name=w.en("analysis_tile.edition_labels.stop_edit_button")).click()
    w.wait_for(lambda: panes_state(page)["edit_mode"] is False, 20, "edit mode off")
    page.wait_for_timeout(800)
    s = panes_state(page)
    report["stop"] = s
    assert s["toolbars"] == [0, 0], s["toolbars"]
    assert s["hints"] == [[], []], s["hints"]
    assert s["drawing_n"] == 0 and s["drawn"] == [0, 0], (s["drawing_n"], s["drawn"])
    assert s["vertex_markers"] == [0, 0], s["vertex_markers"]
    assert s["bb_on"] == [True, True], "the alert box did not come back"
    # stopping discards the unsaved drawing, and the edit still left the alert changed
    w.wait_for(lambda: w.en("analysis_tile.verdict.unsaved") in footer_text(page), 20,
               "the footer saying the alert has unsaved changes")
    w.shot(page, out, "05_stopped")


#: The main map (MapApp's ``#map-container``): its size, and whether loaded
#: tiles cover its centre and four points near its corners. Leaflet measures a
#: hidden map as 0 x 0 on a window resize and keeps only the tiles of that
#: empty view; a map measured again once shown has its whole area covered.
MAIN_MAP_COVERED = """() => {
  const mc = document.querySelector('#map-container');
  const c = mc && mc.querySelector('.leaflet-container');
  if (!c) return {found: false, covered: false};
  const r = c.getBoundingClientRect();
  const tiles = [...c.querySelectorAll('img.leaflet-tile-loaded')].map(t => t.getBoundingClientRect())
      .filter(b => b.width > 0 && b.height > 0);
  const points = [[0.5, 0.5], [0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]]
      .map(([fx, fy]) => [r.left + fx * r.width, r.top + fy * r.height]);
  const hit = points.map(([x, y]) => tiles.some(b => b.left <= x && x <= b.right && b.top <= y && y <= b.bottom));
  // the extent of the loaded tiles against the map's own client box
  const ext = tiles.length ? [Math.min(...tiles.map(b => b.left)), Math.min(...tiles.map(b => b.top)),
                              Math.max(...tiles.map(b => b.right)), Math.max(...tiles.map(b => b.bottom))] : null;
  return {found: true, box: [Math.round(r.width), Math.round(r.height)], client: [c.clientWidth, c.clientHeight],
          tiles: tiles.length, hit, ext: ext && ext.map(Math.round),
          covered: c.clientWidth > 300 && c.clientHeight > 300 && hit.every(Boolean)
              && !!ext && ext[0] <= r.left + 2 && ext[1] <= r.top + 2 && ext[2] >= r.right - 2 && ext[3] >= r.bottom - 2};
}"""

#: ``[left, top, width, height]`` of the analysis screen while it shows, else None.
SCREEN_BOX = """() => { const e = document.querySelector('.defo-analysis-screen--on');
  if (!e) return null; const r = e.getBoundingClientRect();
  return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }"""

#: ``[left, top, width, height]`` of the first element matching a selector
#: that has a box on screen, else None.
BOX = """s => { const e = [...document.querySelectorAll(s)].find(x => x.getBoundingClientRect().width > 0);
  if (!e) return null; const r = e.getBoundingClientRect();
  return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; }"""

#: Whether the topmost element at ``[x, y]`` (page px) is inside ``selector``
#: (``[selector, x, y]``): what a click there would reach.
ON_TOP = """([s, x, y]) => { const e = document.elementFromPoint(x, y); return !!(e && e.closest(s)); }"""

#: The views' translated labels: each layer menu's title (``menus``, one per
#: view) and the zoom-to-alert button's tooltip (``fit``, the after view only).
VIEW_LABELS = """() => {
  const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent);
  if (!host) return null;
  return {menus: [...host.querySelectorAll('.mv-layer-menu .mv-layers-title')].map(e => e.textContent.trim()),
          fit: [...host.querySelectorAll('a.mv-fit-bounds')].map(e => e.getAttribute('title'))};
}"""

#: The analysis screen's theme while it shows, else None: its theme classes,
#: sorted (``theme--light``, or ``analysis-stage--dark`` and ``theme--dark``),
#: and whether each views root (``.mv-root``) has ``mv-dark``.
SCREEN_THEME = """() => {
  const s = document.querySelector('.defo-analysis-screen--on');
  if (!s) return null;
  return {screen: [...s.classList].filter(c => c.startsWith('theme--') || c === 'analysis-stage--dark').sort(),
          views: [...s.querySelectorAll('.mv-root')].map(e => e.classList.contains('mv-dark'))};
}"""

#: MapApp's theme toggle, in its rail (pysepal's ThemeToggle: its icon shows
#: the mode, and a click moves dark → light → auto).
RAIL_THEME = (".left-drawer button:has(.mdi-weather-night), .left-drawer button:has(.mdi-white-balance-sunny),"
              " .left-drawer button:has(.mdi-weather-sunny), .left-drawer button:has(.mdi-auto-fix)")
#: MapApp's language selector, in its rail (pysepal's LocaleSelect: it opens
#: a dialog listing "<name> (<code>)", and a pick closes it).
RAIL_LANGUAGE = ".left-drawer button:has(.mdi-translate)"


def shown(page, selector) -> int:
    """How many elements matching ``selector`` have a box on screen."""
    return page.evaluate("""s => [...document.querySelectorAll(s)].filter(e => {
        const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length""", selector)


def beside_rail(page):
    """``(rail, expected)``: MapApp's rail box, and the box the analysis
    screen must have: right of the rail, from the top of the window to its
    bottom and its right edge (R30)."""
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    rail = page.evaluate(BOX, ".left-drawer")
    assert rail and rail[0] == 0 and rail[2] > 0, f"MapApp's rail is not shown: {rail}"
    edge = rail[0] + rail[2]
    return rail, [edge, 0, vw - edge, vh]


def near(box, expected, px=1):
    """Two ``[left, top, width, height]`` boxes within ``px`` of each other."""
    return bool(box) and len(box) == len(expected) and all(abs(a - b) <= px for a, b in zip(box, expected))


def catalogue_text(code, dotted):
    """The text of the catalogue key ``dotted`` in locale ``code``
    (``walklib.en`` for any locale)."""
    for path in sorted((w.MESSAGES_EN.parent / code).glob("*.json")):
        node = json.loads(path.read_text(encoding="utf-8"))
        for part in dotted.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node
    raise KeyError(f"{code}: {dotted}")


def rail_language(page, code):
    """Pick ``code`` in the rail's language selector, on the analysis screen,
    and wait until the screen's back button and the views' labels read that
    language and the selector's dialog is closed."""
    page.locator(RAIL_LANGUAGE).first.click()
    page.locator(f'{w.MODAL} .v-list-item:has-text("({code})")').first.click()
    back = catalogue_text(code, "app.panel_workflow_title")
    labels = {"menus": [catalogue_text(code, "analysis.panes.layers")] * 2,
              "fit": [catalogue_text(code, "analysis.panes.zoom_alert")]}
    w.wait_for(lambda: back in page.locator(".defo-back-to-workflow").first.inner_text(), 60,
               f"{code}: the screen's back button")
    w.wait_for(lambda: page.evaluate(VIEW_LABELS) == labels, 30, f"{code}: the views' labels")
    w.wait_for(lambda: not page.locator(w.MODAL).count(), 10, f"{code}: the language dialog closed")
    page.wait_for_timeout(500)


def rail_theme(page, dark):
    """Click the rail's theme toggle, on the analysis screen, until the screen
    has the dark (``dark=True``) or the light theme; whether it got there. The
    toggle moves dark → light → auto, so it takes at most three clicks."""
    want = "theme--dark" if dark else "theme--light"

    def there():
        return want in ((page.evaluate(SCREEN_THEME) or {}).get("screen") or [])

    for _ in range(4):
        if there():
            return True
        page.locator(RAIL_THEME).first.click()
        page.wait_for_timeout(1500)
    return there()


def check_screen(page, out, report):
    """Review Focus 5 and R30 in the browser: checks 8-10 of the module docstring."""
    # 8. the screen right of the rail, full height; the rail and its arrow on
    # top; MapApp's main map, its right panel and the legend hidden
    rail, expected = beside_rail(page)
    report["rail_box"] = rail
    report["screen_box"] = page.evaluate(SCREEN_BOX)
    assert near(report["screen_box"], expected), (report["screen_box"], expected)
    vh = page.viewport_size["height"]
    assert page.evaluate(ON_TOP, [".left-drawer", rail[2] // 2, vh // 3]), "the analysis screen covers MapApp's rail"
    arrow = page.evaluate(BOX, ".sidebar-controls .control-btn")
    assert arrow and page.evaluate(ON_TOP, [".sidebar-controls", arrow[0] + arrow[2] // 2, arrow[1] + arrow[3] // 2]),         f"the analysis screen covers the rail's collapse arrow: {arrow}"
    # the arrow straddles the screen's left edge: probe the half over the
    # screen too (elementFromPoint, what a click there reaches), not only the rail column
    edge = rail[0] + rail[2]
    over = [x for x in (arrow[0] + arrow[2] - 2, arrow[0] + arrow[2] * 3 // 4)
            if x > edge] if arrow else []
    report["arrow_probe_over_screen"] = bool(over)
    assert over, f"the collapse arrow has no part over the analysis screen: arrow {arrow}, rail edge {edge}"
    for x in over:
        assert page.evaluate(ON_TOP, [".sidebar-controls", x, arrow[1] + arrow[3] // 2]), \
            f"the analysis screen covers the part of the collapse arrow over it, at x={x}"
    assert page.locator("#map-container").count() >= 1, "no #map-container: the hiding rule has nothing to hide"
    assert shown(page, "#map-container") == 0, "the main map shows under the analysis screen"
    assert shown(page, ".right-panel") == 0, "the right panel shows beside the analysis screen"
    assert shown(page, ".right-panel-tab") == 0, "the right panel's tab shows beside the analysis screen"
    assert shown(page, ".sepal-legend") == 0, "the legend shows on the analysis screen"
    assert shown(page, ".defo-analysis-right") == 1, "no review console on the analysis screen"
    w.shot(page, out, "06_screen")

    # 9. a window resize while the screen shows (Leaflet measures the hidden
    # main map as 0 x 0), then back: the views' hide nudge re-measures it
    page.set_viewport_size({"width": 1300, "height": 900})
    page.wait_for_timeout(1500)
    report["main_map_while_on_screen"] = page.evaluate(MAIN_MAP_COVERED)  # hidden: 0 x 0, no tile covers it
    w.back_to_workflow(page)
    report["main_map_after_back"] = w.wait_for(
        lambda: (m := page.evaluate(MAIN_MAP_COVERED)) and m["covered"] and m, 60,
        "back on the workflow, the main map has loaded tiles")
    assert not report["main_map_while_on_screen"].get("covered"), report["main_map_while_on_screen"]
    # Leaflet's own size of the main map is the container's client box again,
    # and the tiles loaded after leaving cover that whole box (MAIN_MAP_COVERED)
    assert report["main_map_after_back"]["client"][0] >= 300, report["main_map_after_back"]
    assert shown(page, ".right-panel") >= 1, "the right panel did not come back"
    assert "Questionnaire" in w.panel_text(page), w.panel_text(page)[:300]
    w.shot(page, out, "07_back_on_the_workflow")
    page.set_viewport_size({"width": 1400, "height": 1000})
    page.wait_for_timeout(1500)

    # 10. Start analysis again: the screen beside the rail, with tiles on both views
    goto_analysis(page)
    _, expected = beside_rail(page)
    report["screen_again"] = page.evaluate(SCREEN_BOX)
    assert near(report["screen_again"], expected), (report["screen_again"], expected)
    w.wait_for(lambda: (s := panes_state(page)) and all(n > 0 for n in s["tiles_loaded"]), 90,
               "tiles on both views after coming back")
    w.shot(page, out, "08_screen_again")

    # close the right panel once while the screen shows (it is hidden: the
    # panel's own close button, clicked from the page), and its tab, which the
    # closed panel adds to the DOM, must stay hidden
    page.evaluate("() => document.querySelector('.right-panel .pin-btn').click()")
    w.wait_for(lambda: page.locator(".right-panel-tab").count() == 1, 15, "the closed panel's tab is in the DOM")
    page.wait_for_timeout(500)
    assert shown(page, ".right-panel-tab") == 0, "the closed right panel's tab shows on the analysis screen"
    assert shown(page, ".right-panel") == 0, "the closed right panel shows on the analysis screen"
    w.shot(page, out, "09_screen_panel_closed")
    # back on the workflow the tab is the way to the panel again
    w.back_to_workflow(page)
    w.wait_for(lambda: shown(page, ".right-panel-tab") == 1, 15, "back on the workflow, the closed panel's tab shows")
    page.locator(".right-panel-tab .control-btn").first.click()
    w.wait_for(lambda: shown(page, ".right-panel") >= 1, 15, "the right panel reopened")
    goto_analysis(page)
    assert near(page.evaluate(SCREEN_BOX), expected), (page.evaluate(SCREEN_BOX), expected)


def check_rail(page, out, report):
    """R30 and Review Focus 4 in the browser: checks 11-13 of the module
    docstring. MapApp's rail is used on the analysis screen; the check starts
    and ends there, in English and in the light theme."""
    assert page.locator(w.SCREEN_ON).count() == 1, "check_rail starts on the analysis screen"

    # 11. a rail dialog (About) opens over the screen and closes
    page.locator('.left-drawer [data-step-id="1"]').first.click()
    w.wait_for(lambda: w.dialog_visible(page), 30, "the About dialog over the analysis screen")
    page.wait_for_timeout(1000)
    card = page.evaluate(BOX, w.RAIL_DIALOG)
    report["about_dialog"] = card
    assert card and page.evaluate(ON_TOP, [w.RAIL_DIALOG, card[0] + card[2] // 2, card[1] + card[3] // 2]),         "the About dialog is not on top of the analysis screen"
    # probe near the card's four corners as well: over the screen's body, over its top bar, beside the rail
    for fx, fy in ((0.05, 0.05), (0.95, 0.05), (0.05, 0.95), (0.95, 0.95)):
        x, y = card[0] + int(card[2] * fx), card[1] + int(card[3] * fy)
        assert page.evaluate(ON_TOP, [w.RAIL_DIALOG, x, y]), f"the About dialog is under the screen at ({x}, {y})"
    assert page.locator(w.SCREEN_ON).count() == 1, "opening a rail dialog left the analysis screen"
    w.shot(page, out, "rail_about")
    assert w.close_dialog(page), "the About dialog did not close"
    assert page.locator(w.SCREEN_ON).count() == 1, "closing a rail dialog left the analysis screen"

    # 12. the rail's language selector: French, then English again
    for code in ("fr", "en"):
        rail_language(page, code)
        assert page.locator(w.SCREEN_ON).count() == 1, f"{code}: the language switch left the analysis screen"
        title = page.locator(".defo-analysis-title").first.inner_text()
        report[f"{code}_title"] = title
        report[f"{code}_labels"] = page.evaluate(VIEW_LABELS)
        assert title.startswith(catalogue_text(code, "app.drawer_item.analysis")), title
        if code == "fr":
            w.shot(page, out, "rail_french")

    # 13. the rail's theme toggle: dark, then light again
    for dark in (True, False):
        mode = "dark" if dark else "light"
        assert rail_theme(page, dark), f"the rail's theme toggle did not reach the {mode} theme"
        assert page.locator(w.SCREEN_ON).count() == 1, f"{mode}: the theme toggle left the analysis screen"
        want = ["analysis-stage--dark", "theme--dark"] if dark else ["theme--light"]
        report[mode] = w.wait_for(
            lambda: (s := page.evaluate(SCREEN_THEME)) and s["screen"] == want and s["views"]
            and all(v == dark for v in s["views"]) and s, 30, f"the screen and the views {mode}")
        if dark:
            w.shot(page, out, "rail_dark")
    _, expected = beside_rail(page)
    assert near(page.evaluate(SCREEN_BOX), expected), (page.evaluate(SCREEN_BOX), expected)


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8953)
    ap.add_argument("--out", type=Path, default=Path("/tmp/claude-1000/panes-walk"))
    ap.add_argument("--data", type=Path, default=DATA)
    args = ap.parse_args(argv)
    data, out = args.data, args.out
    if data == DATA:
        shutil.rmtree(data, ignore_errors=True)
    data.mkdir(parents=True, exist_ok=True)
    if not (data / PROJECT).exists():
        shutil.copytree(SEED, data / PROJECT)  # a copy: the original is never written
    out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, data, out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)

            def watched(fn):
                def run(pg, rec):
                    before = len(w.app_page_errors(report))
                    try:
                        fn(pg, rec)
                    finally:
                        rec["page_errors"] = w.app_page_errors(report)[before:]
                return run

            ok = ph.run_phases(page, [
                ("open", watched(lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT)))),
                ("panes", watched(lambda pg, r: (goto_analysis(pg), check_panes(pg, out, r)))),
                ("screen", watched(lambda pg, r: check_screen(pg, out, r))),
                ("rail", watched(lambda pg, r: check_rail(pg, out, r))),
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"] = [f"{n}: {e}" for n, rec in report.get("phases", {}).items()
                                         for e in rec.get("page_errors", [])
                                         if not (n == "rail" and e == THEME_SWAP_ERROR)]
            if not ok:
                w.shot(page, out, "failure")
            browser.close()
        with open(out / "report.json", "w") as f:
            json.dump(report, f, indent=1, ensure_ascii=False, default=str)
        ok = ok and not report["error_toasts"] and not report["app_page_errors"]
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
