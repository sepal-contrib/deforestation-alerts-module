"""Parity gate walk (spec 2026-10-05, project dialog parity, §4): the app
starts on the Project dialog; Manage projects lists the projects and gates
Open; the name form nudges on an outside click, refuses a bad name live and
closes on Escape; every step table fits the Workflow panel; Enter submits the
period form from its date field; the source modal nudges.

    python -m tools.walk.parity_walk --port 8987 --out /tmp/parity_walk
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.walk import make_rasters
from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.seed_project import seed

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_parity"
USER = DATA / "user"  # the walk's "SEPAL folder" files: under $HOME, like a user's
A, B = "parity_a", "parity_b"
AMAZON = (-55.10, -10.10, -55.00, -10.00)  # GLAD-L and RADD both cover it
SOURCES = ("GLAD-L", "RADD")
PERIOD = ("2025-06-01", "2025-06-30")
PERIOD_ROW = "2025-06-01 → 2025-06-30"
NEXT_START, NEXT_END = "2025-07-01", "2025-07-31"  # the new period's prefill starts the day after
NEXT_ROW = "2025-07-01 → 2025-07-31"
MASK = "cut"
#: The viewport's bottom-left corner: the rail, under every open dialog's scrim.
SCRIM = (12, 988)
#: gui.widget.dialog_nudge.nudge_class(1), nudge_class(2)
NUDGES = ("da-dialog-nudge-a", "da-dialog-nudge-b")
PROJECT_DIALOG_PX = 560  # the Project rail item's dialog_width
NAME_FORM_PX = 420
MIN_NAME_PX = 100  # spec D4 budgets 120 px; 20 px of slack for padding and rounding

_RAIL_WIDTH_JS = """() => { const c = [...document.querySelectorAll('.dialog-card')]
    .find(e => e.getBoundingClientRect().width > 0);
    return c ? Math.round(c.getBoundingClientRect().width) : null; }"""


def _modal_class(page) -> str:
    loc = page.locator(w.MODAL).last
    return (loc.get_attribute("class") or "") if loc.count() else ""


def outside_clicks_nudge(page, rec, key):
    """Two clicks on the scrim: the form stays open, its class alternates (the
    animation restarts), and the rail dialog behind it, if any, stays open."""
    rail = w.dialog_visible(page)
    for expected in NUDGES:
        page.mouse.click(*SCRIM)
        w.wait_for(lambda: expected in _modal_class(page), 10, f"{key}: {expected} after an outside click")
        assert ph.dialog_open(page), f"{key}: an outside click closed the form"
        assert w.dialog_visible(page) == rail, f"{key}: an outside click closed the rail dialog"
    rec[f"{key}_nudged"] = list(NUDGES)


def startup(page, rec, *, out, port):
    """The app opens on the Project dialog: 560 px, nothing open, Manage
    projects counting the two seeded projects (spec A4, D5)."""
    page.goto(w.app_url(port), wait_until="load", timeout=180000)
    manage_btn = page.locator(f"{w.RAIL_DIALOG} {w.MANAGE_BUTTON}:visible").first
    w.wait_for(lambda: w.dialog_visible(page) and manage_btn.count(), 300, "the Project dialog at startup")
    page.wait_for_timeout(2500)  # the dialog's fade-in, for the screenshot
    rec["dialog"] = w.dialog_text(page)[:600]
    rec["dialog_px"] = page.evaluate(_RAIL_WIDTH_JS)
    rec["manage_button"] = manage_btn.inner_text()
    w.shot(page, out, "startup_project")
    assert w.en("steps.project.empty_heading").lower() in rec["dialog"].lower(), rec["dialog"]
    assert abs((rec["dialog_px"] or 0) - PROJECT_DIALOG_PX) <= 2, rec["dialog_px"]
    assert manage_btn.is_enabled() and "(2)" in rec["manage_button"], rec["manage_button"]


def manage(page, rec, *, out):
    """Manage projects lists both projects; Open, its one Enter default, is
    disabled until a row is selected; Cancel leaves the Project dialog open."""
    w.open_manage(page)
    open_ = page.locator(f"{w.MODAL} {w.ENTER_BUTTON}:visible")
    for name in (A, B):
        w.wait_for(lambda: w.manage_item(page, name).count(), 15, f"{name} listed")
    rec["manage"] = ph.modal_text(page)[:600]
    rec["enter_defaults"] = open_.count()
    assert rec["enter_defaults"] == 1, rec["enter_defaults"]
    assert open_.first.is_disabled(), "Open is enabled with no project selected"
    w.manage_item(page, B).locator(".v-list-item__title").first.click()
    w.wait_for(lambda: open_.first.is_enabled(), 15, "Open enabled once a project is selected")
    w.shot(page, out, "manage_projects")
    ph.modal_button(page, w.en("common.cancel")).click()
    w.wait_for(lambda: not ph.dialog_open(page), 15, "Manage projects closed")
    assert w.dialog_visible(page), "Cancel in Manage projects closed the Project dialog"


def new_form(page, rec, *, out):
    """New project's name form (420 px): outside clicks nudge it, a bad name is
    refused live with Create disabled, a taken name offers to open that
    project, and Escape closes the form but not the Project dialog."""
    ph.visible_btn(page, w.en("steps.project.new"), w.RAIL_DIALOG).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the name form")
    field = ph.modal_field(page, w.en("steps.project.name_label"))
    create = page.locator(f"{w.MODAL} {w.ENTER_BUTTON}")
    w.wait_for(lambda: field.count() and field.is_visible(), 15, "the name field")
    rec["form_px"] = page.locator(w.MODAL).last.evaluate("e => Math.round(e.getBoundingClientRect().width)")
    rec["enter_defaults"] = create.count()
    assert abs(rec["form_px"] - NAME_FORM_PX) <= 2, rec["form_px"]
    assert rec["enter_defaults"] == 1, rec["enter_defaults"]
    outside_clicks_nudge(page, rec, "name_form")
    w.shot(page, out, "name_form_nudged")
    field.fill("bad/name")
    chars = w.en("steps.project.name_chars")
    w.wait_for(lambda: chars in ph.modal_text(page), 15, "the live error for a bad name")
    assert create.first.is_disabled(), "Create is enabled for a bad name"
    w.shot(page, out, "name_form_invalid")
    field.fill(B)
    instead = ph.modal(page).locator("button:visible", has_text=B)
    w.wait_for(lambda: instead.count(), 15, f"the offer to open {B} instead")
    rec["taken"] = ph.modal_text(page)[:400]
    assert create.first.is_disabled(), "Create is enabled for a taken name"
    field.press("Escape")
    w.wait_for(lambda: not ph.dialog_open(page), 15, "Escape closed the name form")
    assert w.dialog_visible(page), "Escape closed the Project dialog too"


def _fits(page, rec, key, names, *, out):
    """Each named row inside the panel: every button in it, a name cell of
    MIN_NAME_PX or more, no horizontal overflow (spec D4)."""
    for name in names:
        fit = w.wait_for(lambda: ph.table_fit(page, name), 60, f"{key}: the {name} row")
        rec[f"{key}: {name}"] = fit
        left, right = fit["panel"]
        assert fit["buttons"], f"{key}: {name} has no action button: {fit}"
        assert all(left - 1 <= a and b <= right + 1 for a, b in fit["buttons"]), \
            f"{key}: a {name} button is outside the panel: {fit}"
        assert fit["name_px"] >= MIN_NAME_PX, f"{key}: {name}'s name cell is {fit['name_px']} px: {fit}"
        assert fit["overflow_px"] <= 1, f"{key}: the {name} row overflows: {fit}"
    w.shot(page, out, f"table_{key}")


def tables(page, rec, *, out):
    """Sources, periods, Download & harmonize and masks rows at the panel width."""
    w.goto_step(page, "alerts", 2000)
    _fits(page, rec, "sources", SOURCES, out=out)
    w.goto_step(page, "period", 2000)
    _fits(page, rec, "periods", (PERIOD_ROW,), out=out)
    ph.save_grid(page)
    _fits(page, rec, "prepare", SOURCES, out=out)
    ph.add_mask(page, rec, name=MASK, origin="file", ref=USER / "probability.tif", band="probability",
                mode="Keep", out=out)
    _fits(page, rec, "masks", (MASK,), out=out)
    rec["mask_mode"] = ph.row_mode_value(page, MASK)
    assert rec["mask_mode"] == "Keep", rec["mask_mode"]


def period_enter(page, rec, *, out):
    """Enter in the period form's End field presses its one default button."""
    w.goto_step(page, "period", 2000)
    ph.visible_btn(page, w.en("steps.period.new"), ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the period modal")
    start = ph.modal_field(page, w.en("steps.period.start"))
    end = ph.modal_field(page, w.en("steps.period.end"))
    rec["prefill"] = [start.input_value(), end.input_value()]
    assert rec["prefill"][0] == NEXT_START, rec["prefill"]
    end.fill(NEXT_END)
    page.wait_for_timeout(800)  # the date reaches the kernel and the check re-runs
    rec["enter_defaults"] = ph.modal(page).locator(f"{w.ENTER_BUTTON}:visible").count()
    assert rec["enter_defaults"] == 1, rec["enter_defaults"]
    t0 = w.now_ms(page)
    end.press("Enter")
    ph.wait_toast(page, t0, w.en("steps.period.created"))
    w.wait_for(lambda: not ph.dialog_open(page), 15, "Enter submitted and closed the period modal")
    w.wait_for(lambda: ph.row_text(page, NEXT_ROW), 30, "the new period listed")
    rec["row"] = ph.row_text(page, NEXT_ROW)
    w.shot(page, out, "period_enter")


def source_nudge(page, rec, *, out):
    """The source modal on the frame: the Source type select, one Enter
    default, nudged by outside clicks, closed by Cancel."""
    w.goto_step(page, "alerts", 2000)
    ph.visible_btn(page, w.en("steps.alerts.add"), ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the source modal")
    page.wait_for_timeout(1000)
    rec["source_types"] = [i["text"] for i in ph.menu_items(page, ph.SOURCE_TYPE)]
    wanted = [w.en("steps.alerts.mode_predefined"), w.en("steps.alerts.mode_custom").split(" (")[0]]
    assert [t.split(" (")[0] for t in rec["source_types"]] == wanted, rec["source_types"]
    rec["enter_defaults"] = ph.modal(page).locator(f"{w.ENTER_BUTTON}:visible").count()
    assert rec["enter_defaults"] == 1, rec["enter_defaults"]
    outside_clicks_nudge(page, rec, "source_modal")
    w.shot(page, out, "source_modal_nudged")
    ph.modal_button(page, w.en("common.cancel")).click()
    w.wait_for(lambda: not ph.dialog_open(page), 15, "the source modal closed")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8987)
    ap.add_argument("--out", type=Path, default=Path("/tmp/parity_walk"))
    args = ap.parse_args(argv)
    # The walk's own data dir, rebuilt: startup counts the projects on disk (A and B).
    shutil.rmtree(DATA, ignore_errors=True)
    USER.mkdir(parents=True, exist_ok=True)
    make_rasters.probability_tif(USER / "probability.tif", AMAZON)
    seed(A, AMAZON, sources=SOURCES, period=PERIOD, look_back=0, root=DATA)
    seed(B, AMAZON, root=DATA)
    args.out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, DATA, args.out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            out = args.out
            ok = ph.run_phases(page, [
                ("startup", lambda pg, r: startup(pg, r, out=out, port=args.port)),
                ("manage", lambda pg, r: manage(pg, r, out=out)),
                ("new_form", lambda pg, r: new_form(pg, r, out=out)),
                ("open", lambda pg, r: w.open_project(pg, A)),
                ("tables", lambda pg, r: tables(pg, r, out=out)),
                ("period_enter", lambda pg, r: period_enter(pg, r, out=out)),
                ("source_nudge", lambda pg, r: source_nudge(pg, r, out=out)),
            ], report, args.out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["page_errors_count"] = len(report.get("page_errors", []))
            browser.close()
        with open(args.out / "report.json", "w") as f:
            json.dump(report, f, indent=1, ensure_ascii=False, default=str)
        ok = ok and not report["error_toasts"] and not report["bad_responses"]
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", args.out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
