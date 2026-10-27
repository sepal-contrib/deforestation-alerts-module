"""Group review end-to-end walk (group review plan, Task 14): reviewing nearby
alerts together in a real browser, on a copy of the questionnaire walk's seed
project.

    python -m tools.walk.group_walk --port 8953 --out /tmp/claude-1000/group-walk

The walk copies ``~/module_results/deforestation_alerts/repro_p`` into its own
data dir and makes its 90-alert set (August) the active one. It then picks,
from the store, the first unreviewed alert with at least three neighbours
within 500 m (``review_groups.nearby``, the function and the rules the panel
uses) and walks the six phases of the spec's section 12, each an entry in the
JSON report with a screenshot:

 1. enable:  Evaluation: *Review nearby alerts together* ticked, distance 500,
             the note shows
 2. group3:  Analysis: open the alert, tick two neighbours in the Nearby list,
             the dock reads "Save for 3 alerts", a verdict, Save: toast, and
             the banner "Reviewed as a group of 3"
 3. click4:  a click on a third neighbour on the *after* view ticks it, "Save
             for 4 alerts", Save: no confirmation, a group of 4
 4. untick:  untick one member, Save: the confirmation lists one "leaves the
             group" line, OK
 5. member:  open a member: the banner and *Remove from group*, Remove,
             confirm: its status is Not reviewed
 6. locked:  Evaluation again: the grouping checkbox is disabled, with the
             "groups are saved" note

After every save the store is loaded and ``review_groups.check_groups`` runs on
it. It needs Earth Engine and sepal.io (the image timeline), so it runs outside
a sandbox.
"""

import argparse
import json
import re
import shutil
import sys
import traceback
from pathlib import Path

import geopandas as gpd
from playwright.sync_api import sync_playwright

from deforestation_alerts.project import review_groups as rg
from gui.tile.analysis.nearby import hit_row
from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.panes_walk import _XY
from tools.walk.questionnaire_walk import PROJECT, SEED, THEME_SWAP_ERROR, goto_analysis

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_group"
#: the seed's August set: 90 unreviewed alerts, a dozen of them within 500 m of each other
WORK_SET = "2026-08-01_2026-08-31__glad-l"
RADIUS = 500.0
RIGHT = ".right-panel:visible"
FOOTER = ".defo-analysis-footer"
GROUP_OPTION = ".defo-group-option"
LEAVES = "leaves the group"


# ------------------------------------------------------------------ helpers
def use_work_set(data: Path) -> None:
    """Make the August set the copy's active one (the seed opens on a 13-alert set)."""
    path = data / PROJECT / "project.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m["active_set"] = WORK_SET
    m["active_period"] = m["alert_sets"][WORK_SET]["period_id"]
    path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


def load_store(data: Path) -> gpd.GeoDataFrame:
    m = ph.manifest(data, PROJECT)
    return gpd.read_parquet(data / PROJECT / m["alert_sets"][m["active_set"]]["store_rel_path"])


def pick_main(data: Path) -> dict:
    """The first unreviewed alert with three or more unblocked neighbours within
    500 m whose third one a map click can hit on its own: ``{main, near}``, the
    neighbours nearest first (0-based positions)."""
    gdf = load_store(data)
    crs = (ph.manifest(data, PROJECT).get("grid") or {}).get("epsg")
    for p in range(len(gdf)):
        if rg.status_of(gdf, p) != rg.NOT_REVIEWED:
            continue
        near = [r.position for r in rg.nearby(gdf, p, RADIUS, crs).rows if not r.blocked]
        if len(near) < 3:
            continue
        # the click: a point inside the third neighbour hits it and nothing else
        pt = gdf.geometry.iloc[near[2]].representative_point()
        if hit_row(gdf, near, pt.y, pt.x, 14.0) == near[2]:
            return {"main": p, "near": near}
    raise AssertionError(f"no unreviewed alert with 3 neighbours within {RADIUS:g} m in the August set")


def check_store(data: Path, rec: dict) -> gpd.GeoDataFrame:
    """Load the store as it is on disk and run ``check_groups`` on it."""
    gdf = load_store(data)
    rg.check_groups(gdf)
    rec["n_groups"] = rg.n_groups(gdf)
    return gdf


def group_ids(gdf, positions) -> list:
    values = rg.group_values(gdf)
    return [None if values.isna().iloc[p] else int(values.iloc[p]) for p in positions]


def is_group(gdf, main, members) -> bool:
    got_main, got = rg.group_of(gdf, main)
    return got_main == main and set(got) == set(members)


def list_row(page, position):
    """The Nearby list row of the alert at 0-based ``position`` (shown as #position+1)."""
    return page.locator(f'{w.PANEL} div.d-flex.align-center.flex-wrap:has(> span:text-is("#{position + 1}"))').first


def row_ticked(page, position) -> bool:
    return list_row(page, position).locator(".v-input--is-dirty").count() > 0


def tick_row(page, position):
    w.wait_for(lambda: list_row(page, position).count(), 60, f"alert #{position + 1} in the Nearby list")
    list_row(page, position).locator(".v-input--selection-controls__ripple").first.click()
    page.wait_for_timeout(800)


def save_text(page) -> str:
    """The dock's Save button text (Save, or Save for N alerts)."""
    return page.evaluate("""s => { const b = [...document.querySelectorAll(s + ' button')]
        .filter(e => e.getBoundingClientRect().width > 0 && /^\\s*Save/.test(e.textContent));
        return b.length ? b[0].textContent.replace(/\\s+/g, ' ').trim() : ''; }""", FOOTER)


def save_btn(page):
    """The dock's Save, whatever it reads: ``ph.save_btn`` matches the bare word only."""
    return page.locator(f"{FOOTER} button:visible", has_text=re.compile(r"^\s*Save( for \d+ alerts)?\s*$")).first


def answer_true_alert(page):
    """The verdict and the one required answer (was there a fire)."""
    ph.visible_btn(page, "True alert").click()
    page.wait_for_timeout(500)
    ph.open_section(page, "Questions")
    page.locator(f'{w.PANEL} .dat-question:has-text("was there a fire") .v-radio:has(label:text-is("no"))'
                 ).first.click()
    page.wait_for_timeout(500)


def wait_saved(page, t0, what):
    """Wait for the save's toast and fail on an error or a warning."""
    def landed():
        return any("saved" in x["msg"].lower() or x["kind"] in ("toast-error", "toast-warning")
                   for x in w.toasts_since(page, t0))

    w.wait_for(landed, 120, f"the toast of {what}")
    bad = [x for x in w.toasts_since(page, t0) if x["kind"] in ("toast-error", "toast-warning")]
    assert not bad, bad
    page.wait_for_timeout(2500)


def confirm_open(page) -> bool:
    return page.locator(w.MODAL).count() > 0


def wait_banner(page, n, main):
    text = w.en("analysis_tile.nearby.banner.other").format(n=n, main=main + 1)
    w.wait_for(lambda: text in w.panel_text(page), 60, f"the banner {text!r}")


def goto_alert(page, position):
    ph.analysis_go_to(page, position)
    w.wait_for(lambda: re.search(rf"\b0*{position + 1} / \d+", w.panel_text(page)), 30,
               f"alert #{position + 1} on screen")


def click_on_after_view(page, position, main, data: Path, rec: dict, out):
    """Click the polygon of the alert at ``position`` on the *after* view: frame it
    with the main alert on that view's own Leaflet map, then convert a point inside
    it with the same map (``latLngToContainerPoint``, ``panes_walk._XY``) and click."""
    shapes = load_store(data).geometry
    pt = shapes.iloc[position].representative_point()
    minx, miny, maxx, maxy = shapes.iloc[position].union(shapes.iloc[main]).bounds
    pad = 0.0004
    page.evaluate("""b => {
        const host = [...document.querySelectorAll('.analysis-views')].find(e => e.offsetParent);
        const mv = host._multiview || host.querySelector('.mv-root')._multiview;
        mv.views.find(x => x.id === 'after').map.fitBounds(b, {animate: false});
    }""", [[miny - pad, minx - pad], [maxy + pad, maxx + pad]])
    page.wait_for_timeout(2500)
    x, y = page.evaluate(_XY, ["after", pt.y, pt.x])
    rec["click"] = {"lat": pt.y, "lng": pt.x, "x": x, "y": y}
    w.shot(page, out, "03_before_click")
    page.mouse.click(x, y)
    page.wait_for_timeout(1500)


# ------------------------------------------------------------------- phases
def p1_enable(page, rec, *, out, data):
    w.goto_step(page, "evaluators", 2000)
    w.wait_for(lambda: page.locator(f"{RIGHT} {GROUP_OPTION}").count(), 30, "the grouping checkbox")
    page.locator(f"{RIGHT} {GROUP_OPTION} .v-input--selection-controls__ripple").first.click()
    field = page.locator(f"{RIGHT} .defo-group-radius input").first
    w.wait_for(lambda: field.count() and field.is_visible(), 30, "the distance field")
    field.fill("500")
    page.wait_for_timeout(1000)
    rec["note"] = page.locator(f"{RIGHT} .defo-group-note").first.inner_text()
    w.shot(page, out, "01_enable")
    assert rec["note"].strip() == w.en("evaluators.group_note"), rec["note"]
    assert page.locator(f"{RIGHT} .defo-group-include").count() == 1, "Include reviewed missing"

    def stored():
        g = ph.manifest(data, PROJECT).get("group_review") or {}
        return g.get("enabled") is True and g.get("radius_m") == RADIUS

    w.wait_for(stored, 30, "group_review stored in the manifest")
    rec["group_review"] = ph.manifest(data, PROJECT)["group_review"]


def p2_group3(page, rec, *, out, data):
    rec.update(pick_main(data))
    main, near = rec["main"], rec["near"]
    w.log(f"main #{main + 1}, neighbours {[p + 1 for p in near]}")
    goto_analysis(page)
    goto_alert(page, main)
    ph.open_section(page, "Nearby alerts")
    for p in near[:2]:
        tick_row(page, p)
    rec["ticked"] = [row_ticked(page, p) for p in near[:2]]
    assert all(rec["ticked"]), rec["ticked"]
    w.wait_for(lambda: save_text(page) == "Save for 3 alerts", 30, "the dock reads Save for 3 alerts")
    rec["save_text"] = save_text(page)
    answer_true_alert(page)
    w.shot(page, out, "02_group3_before_save")
    t0 = w.now_ms(page)
    save_btn(page).click()
    page.wait_for_timeout(1000)
    assert not confirm_open(page), "a first group asked for a confirmation"
    wait_saved(page, t0, "the group of 3")
    rec["save_toasts"] = [f"{x['kind']}: {x['msg']}" for x in w.toasts_since(page, t0)]
    wait_banner(page, 3, main)
    w.shot(page, out, "02_group3_saved")
    gdf = check_store(data, rec)
    rows = [main, *near[:2]]
    rec["status"] = [rg.status_of(gdf, p) for p in rows]
    rec["review_group"] = group_ids(gdf, rows)
    assert all(s == "Confirmed" for s in rec["status"]), rec["status"]
    assert is_group(gdf, main, near[:2]), rg.group_of(gdf, main)
    assert rec["n_groups"] == 1, rec["n_groups"]


def p3_click4(page, rec, *, out, data):
    main, near = rec["main"], rec["near"]
    third = near[2]
    assert not row_ticked(page, third), "the third neighbour is ticked already"
    click_on_after_view(page, third, main, data, rec, out)
    w.shot(page, out, "03_clicked")
    w.wait_for(lambda: row_ticked(page, third), 30, f"alert #{third + 1} ticked by the map click")
    w.wait_for(lambda: save_text(page) == "Save for 4 alerts", 30, "the dock reads Save for 4 alerts")
    rec["save_text"] = save_text(page)
    t0 = w.now_ms(page)
    save_btn(page).click()
    page.wait_for_timeout(1500)
    assert not confirm_open(page), f"a confirmation opened: {page.locator(w.MODAL).last.inner_text()}"
    wait_saved(page, t0, "the group of 4")
    wait_banner(page, 4, main)
    w.shot(page, out, "03_group4_saved")
    gdf = check_store(data, rec)
    assert is_group(gdf, main, near[:3]), rg.group_of(gdf, main)
    assert all(rg.status_of(gdf, p) == "Confirmed" for p in (main, *near[:3]))


def p4_untick(page, rec, *, out, data):
    main, near = rec["main"], rec["near"]
    leaving = near[1]
    w.wait_for(lambda: row_ticked(page, leaving), 30, f"alert #{leaving + 1} ticked (the group seeds the selection)")
    tick_row(page, leaving)
    assert not row_ticked(page, leaving)
    w.wait_for(lambda: save_text(page) == "Save for 3 alerts", 30, "the dock reads Save for 3 alerts")
    t0 = w.now_ms(page)
    save_btn(page).click()
    w.wait_for(lambda: confirm_open(page), 30, "the confirmation")
    page.wait_for_timeout(800)
    rec["confirmation"] = page.locator(w.MODAL).last.inner_text()
    w.shot(page, out, "04_confirm")
    lines = [ln for ln in rec["confirmation"].splitlines() if "#" in ln]
    assert len(lines) == 1 and LEAVES in lines[0] and f"#{leaving + 1}" in lines[0], rec["confirmation"]
    ph.modal_button(page, "Save").click()
    wait_saved(page, t0, "the group of 3 after the untick")
    wait_banner(page, 3, main)
    w.shot(page, out, "04_group3_again")
    gdf = check_store(data, rec)
    rec["status_leaving"] = rg.status_of(gdf, leaving)
    assert rec["status_leaving"] == rg.NOT_REVIEWED, rec["status_leaving"]
    assert is_group(gdf, main, {near[0], near[2]}), rg.group_of(gdf, main)


def p5_member(page, rec, *, out, data):
    main, near = rec["main"], rec["near"]
    member = near[2]
    goto_alert(page, member)
    ph.open_section(page, "Nearby alerts")
    wait_banner(page, 3, main)
    remove = ph.visible_btn(page, w.en("analysis_tile.nearby.remove"), w.PANEL)
    w.wait_for(lambda: remove.count(), 30, "Remove from group on a member")
    w.shot(page, out, "05_member")
    t0 = w.now_ms(page)
    remove.click()
    w.wait_for(lambda: confirm_open(page), 30, "the confirmation of the removal")
    page.wait_for_timeout(800)
    rec["confirmation"] = page.locator(w.MODAL).last.inner_text()
    w.shot(page, out, "05_confirm")
    assert LEAVES in rec["confirmation"] and f"#{member + 1}" in rec["confirmation"], rec["confirmation"]
    ph.modal_button(page, "Save").click()
    wait_saved(page, t0, "the removal")
    w.wait_for(lambda: (ph.status_chip(page) or "").startswith("Not reviewed"), 60, "the chip reads Not reviewed")
    rec["chip"] = ph.status_chip(page)
    w.shot(page, out, "05_removed")
    gdf = check_store(data, rec)
    rec["status_member"] = rg.status_of(gdf, member)
    assert rec["status_member"] == rg.NOT_REVIEWED, rec["status_member"]
    assert is_group(gdf, main, {near[0]}), rg.group_of(gdf, main)


def p6_locked(page, rec, *, out, data):
    w.goto_step(page, "evaluators", 2000)
    w.wait_for(lambda: page.locator(f"{RIGHT} {GROUP_OPTION}").count(), 30, "the grouping checkbox")
    rec["disabled"] = page.locator(f"{RIGHT} {GROUP_OPTION} input").first.is_disabled()
    rec["note"] = page.locator(f"{RIGHT} .defo-group-note").first.inner_text()
    w.shot(page, out, "06_locked")
    assert rec["disabled"], "the grouping checkbox is not disabled"
    expected = w.en("evaluators.group_locked_groups.one").format(n=1)
    assert rec["note"].strip() == expected, (rec["note"], expected)
    check_store(data, rec)


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8953)
    ap.add_argument("--out", type=Path, default=Path("/tmp/claude-1000/group-walk"))
    ap.add_argument("--data", type=Path, default=DATA,
                    help="the walk's data dir (DEFORESTATION_ALERTS_DATA_DIR); keep it under $HOME")
    args = ap.parse_args(argv)
    data, out = args.data, args.out
    if data == DATA:
        shutil.rmtree(data, ignore_errors=True)  # rebuilt: a previous run's files would hide this run's
    data.mkdir(parents=True, exist_ok=True)
    if not (data / PROJECT).exists():
        shutil.copytree(SEED, data / PROJECT)  # a copy: the original is never written
        use_work_set(data)
    out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, data, out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            picked = {}  # the alert phase 2 picks, for the phases after it

            def watched(fn):
                """The phase, recording the page errors it raised (a check of its own)."""
                def run(pg, rec):
                    before = len(w.app_page_errors(report))
                    rec.update(picked)
                    try:
                        fn(pg, rec)
                    finally:
                        picked.update({k: rec[k] for k in ("main", "near") if k in rec})
                        rec["page_errors"] = w.app_page_errors(report)[before:]
                return run

            def phase(fn):
                return watched(lambda pg, r: fn(pg, r, out=out, data=data))

            ok = ph.run_phases(page, [
                ("open", watched(lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT)))),
                ("enable", phase(p1_enable)),
                ("group3", phase(p2_group3)),
                ("click4", phase(p3_click4)),
                ("untick", phase(p4_untick)),
                ("member", phase(p5_member)),
                ("locked", phase(p6_locked)),
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"] = [f"{n}: {e}" for n, rec in report.get("phases", {}).items()
                                         for e in rec.get("page_errors", []) if e != THEME_SWAP_ERROR]
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
