"""Image-timeline walk: the one-lane timeline and its two handles
(``gui/widget/handle_strip.py``) in a real browser, on a copy of a reviewed
project.

    python -m tools.walk.timeline_walk --port 8951 --out /tmp/claude-1000/timeline-walk

Checks, each an entry in the JSON report with a screenshot:

 1. Analysis: the strip renders both handles, the lane, the cloud value; both
    map titles name an image
 2. dragging the before handle shows its flag and the glow mid-drag; letting
    go moves the before image (the before map's title changes)
 3. dragging the before handle past the after one stops short of it
 4. the keyboard: ArrowRight then Enter on the after handle moves the after image
 5. a stripe click left of the before handle moves the before handle
 6. leaving Analysis and coming back: the handles are still there and work
 7. dark theme: the strip and the titles

It needs Earth Engine and sepal.io (the image timeline), so it runs outside a
sandbox.
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
from tools.walk.questionnaire_walk import SEED, PROJECT, THEME_SWAP_ERROR, goto_analysis, toggle_dark

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_timeline"
HOST = ".analysis-timeline:visible"


def strip_state(page) -> dict:
    """Both handles' x (px, viewport), the flag and glow on show, the bracket
    label, the cloud value and the map titles."""
    return page.evaluate("""h => {
        const host = document.querySelector('.analysis-timeline');
        const vis = e => e && e.getBoundingClientRect().width > 0;
        const card = [...document.querySelectorAll('.analysis-timeline')].find(vis) || host;
        const q = s => card && card.querySelector(s);
        const box = e => { if (!vis(e)) return null; const r = e.getBoundingClientRect();
                           return {x: r.left, y: r.top, w: r.width, h: r.height}; };
        const b = q('.dah-before'), a = q('.dah-after');
        // the jaw's flat side is the date: the before jaw's right edge, the after's left
        const bx = vis(b) ? b.querySelector('svg').getBoundingClientRect().right : null;
        const ax = vis(a) ? a.querySelector('svg').getBoundingClientRect().left : null;
        const titles = [...document.querySelectorAll('.analysis-mapbox .v-card__title')]
            .filter(vis).map(e => e.innerText.replace(/\\s+/g, ' ').trim());
        // the slider's label carries the limit ("Max cloud 40%")
        const lab = q('.v-slider') && q('.v-slider').closest('.v-input').querySelector('label');
        const pct = lab && /\\d+%$/.test(lab.innerText) ? lab.innerText : null;
        return {root: box(q('.dah-root')), before: box(b), after: box(a), bx, ax,
                b_text: b && b.getAttribute('aria-valuetext'), a_text: a && a.getAttribute('aria-valuetext'),
                flag: [...(card ? card.querySelectorAll('.dah-flag') : [])].map(e => e.innerText),
                glow: box(q('.dah-glow')), gap: (q('.dah-gap') || {}).innerText || null,
                bracket: box(q('.dah-bracket')), cloud: pct || null, titles,
                dark: !!document.querySelector('.analysis-stage--dark')};
    }""", HOST)


def wait_strip(page, timeout=240):
    w.wait_for(lambda: (s := strip_state(page))["before"] and s["after"] and len(s["titles"]) == 2
               and s["b_text"] != w.en("analysis.timeline.unset")
               and s["a_text"] != w.en("analysis.timeline.unset"), timeout, "both handles placed")
    page.wait_for_timeout(1000)


def wait_title_change(page, idx, old, timeout=60):
    w.wait_for(lambda: strip_state(page)["titles"][idx] != old, timeout, f"map title {idx} to change")
    page.wait_for_timeout(1500)


def grab(page, side):
    """The centre of a handle's jaw, where a pointer picks it up."""
    svg = page.locator(f"{HOST} .dah-{'before' if side == 'b' else 'after'} svg").first
    r = svg.bounding_box()
    return r["x"] + r["width"] / 2, r["y"] + r["height"] / 2


# ------------------------------------------------------------------- checks
def check_1_render(page, rec, *, out):
    goto_analysis(page)
    wait_strip(page)
    s = strip_state(page)
    rec.update(s)
    w.shot(page, out, "01_strip")
    assert s["bx"] < s["ax"], s
    assert s["cloud"], "the cloud limit's value is not shown"
    assert s["bracket"] and s["gap"], "no bracket or gap label between the handles"
    assert not s["flag"], f"a flag shows at rest: {s['flag']}"
    assert all(w.en("analysis.timeline.unset") not in t for t in s["titles"]), s["titles"]


def check_2_drag(page, rec, *, out):
    s0 = strip_state(page)
    x, y = grab(page, "b")
    root = s0["root"]
    target = root["x"] + root["w"] * 0.08  # far left: an earlier image than the default
    page.mouse.move(x, y)
    page.mouse.down()
    for i in range(1, 9):
        page.mouse.move(x + (target - x) * i / 8, y)
        page.wait_for_timeout(60)
    mid = strip_state(page)
    rec["mid"] = mid
    w.shot(page, out, "02_mid_drag")
    page.mouse.up()
    assert mid["flag"], "no flag while dragging"
    assert mid["glow"], "no glow on the candidate while dragging"
    assert mid["gap"] is None, "the gap label shows while a handle moves"
    wait_title_change(page, 0, s0["titles"][0])
    after = strip_state(page)
    rec["after"] = after
    w.shot(page, out, "02_dropped")
    assert after["titles"][1] == s0["titles"][1], "the after image changed"
    assert not after["flag"], "the flag stayed after the drop"


def check_3_clamp(page, rec, *, out):
    s0 = strip_state(page)
    x, y = grab(page, "b")
    root = s0["root"]
    page.mouse.move(x, y)
    page.mouse.down()
    end = root["x"] + root["w"] * 0.98
    for i in range(1, 11):
        page.mouse.move(x + (end - x) * i / 10, y)
        page.wait_for_timeout(50)
    mid = strip_state(page)
    rec["mid"] = mid
    w.shot(page, out, "03_clamped")
    page.mouse.up()
    page.wait_for_timeout(6000)
    s1 = strip_state(page)
    rec["after"] = s1
    assert mid["bx"] <= mid["ax"] + 0.5, f"the before handle passed the after one: {mid['bx']} > {mid['ax']}"
    assert s1["bx"] < s1["ax"], s1


def check_4_keyboard(page, rec, *, out):
    s0 = strip_state(page)
    page.locator(f"{HOST} .dah-after").first.focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(500)
    mid = strip_state(page)
    rec["mid"] = mid
    w.shot(page, out, "04_key_walk")
    if mid["a_text"] == s0["a_text"]:  # the after image was the last one: walk back instead
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(500)
        mid = strip_state(page)
        rec["mid_left"] = mid
    assert mid["flag"], "no flag while the keyboard walks"
    assert mid["titles"][1] == s0["titles"][1], "an arrow alone changed the image"
    page.keyboard.press("Enter")
    wait_title_change(page, 1, s0["titles"][1])
    rec["after"] = strip_state(page)
    w.shot(page, out, "04_key_enter")


def check_5_click(page, rec, *, out):
    ph.pick_images(page, rec, out=out)
    rec["state"] = strip_state(page)


def check_6_rebuild(page, rec, *, out):
    w.goto_step(page, "questionnaire", 2500)
    w.close_dialog(page)
    goto_analysis(page)
    wait_strip(page)
    s0 = strip_state(page)
    rec["state"] = s0
    w.shot(page, out, "06_back")
    page.locator(f"{HOST} .dah-before").first.focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(300)
    page.keyboard.press("Enter")
    wait_title_change(page, 0, s0["titles"][0])
    rec["after"] = strip_state(page)


def check_7_dark(page, rec, *, out):
    assert toggle_dark(page), "could not switch to the dark theme"
    page.wait_for_timeout(2000)
    goto_analysis(page)
    wait_strip(page)
    s = strip_state(page)
    rec.update(s)
    rec["before_fill"] = page.evaluate(
        "s => getComputedStyle(document.querySelector(s + ' .dah-before polygon')).fill", ".analysis-timeline")
    w.shot(page, out, "07_dark")
    assert s["dark"], "the stage has no dark marker"


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8951)
    ap.add_argument("--out", type=Path, default=Path("/tmp/claude-1000/timeline-walk"))
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
                ("c1_render", watched(lambda pg, r: check_1_render(pg, r, out=out))),
                ("c2_drag", watched(lambda pg, r: check_2_drag(pg, r, out=out))),
                ("c3_clamp", watched(lambda pg, r: check_3_clamp(pg, r, out=out))),
                ("c4_keyboard", watched(lambda pg, r: check_4_keyboard(pg, r, out=out))),
                ("c5_click", watched(lambda pg, r: check_5_click(pg, r, out=out))),
                ("c6_rebuild", watched(lambda pg, r: check_6_rebuild(pg, r, out=out))),
                ("c7_dark", watched(lambda pg, r: check_7_dark(pg, r, out=out))),
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"] = [f"{n}: {e}" for n, rec in report.get("phases", {}).items()
                                         for e in rec.get("page_errors", [])
                                         if not (n == "c7_dark" and e == THEME_SWAP_ERROR)]
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
