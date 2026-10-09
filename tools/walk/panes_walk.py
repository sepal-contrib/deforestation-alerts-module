"""Analysis views walk: the two views are one ``AlertViews`` widget
(``leaflet-multiview``) in today's Analysis stage, on a copy of a reviewed
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
from tools.walk.questionnaire_walk import PROJECT, SEED, goto_analysis

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
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"] = [f"{n}: {e}" for n, rec in report.get("phases", {}).items()
                                         for e in rec.get("page_errors", [])]
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
