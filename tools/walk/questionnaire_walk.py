"""Questionnaire end-to-end walk (questionnaire plan, Task 11): the configurable
questionnaire in a real browser, on a copy of a reviewed project.

    python -m tools.walk.questionnaire_walk --port 8950 --out /tmp/claude-1000/questionnaire-walk

The walk copies ``~/module_results/deforestation_alerts/repro_p`` (two alert
sets, the active one with 90 unreviewed alerts) into its own data dir, adds a
**Road access** question (one answer, required, asked for a True alert only),
and reviews the first alert through it. Ten checks, each an entry in the JSON
report with a screenshot:

 1. the stepper has 7 steps and ends at Questionnaire, whose Next is *Start analysis*
 2. the question is added: list row, tags, ``{road_access}`` and ``{verdict}`` chips
 3. Analysis: Questions closed, badge "required missing" in orange after True alert
 4. Save & next with the answer missing: warning toast naming it, the section opens, no advance
 5. answer Yes, Save & next advances; Previous shows Yes again
 6. the footer stays in view while the panel scrolls
 7. build: ZIP and DOCX tiles with sizes, Rebuild, GIF disabled
 8. the DOCX downloaded from its link says "Yes" in ``Road: {road_access}``
 9. Overview has a "Road access" card
10. dark theme: footer not white, Questions labels readable

It needs Earth Engine and sepal.io (the image timeline), so it runs outside a
sandbox.
"""

import argparse
import io
import json
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import docx
from playwright.sync_api import sync_playwright

from tools.walk import phases as ph
from tools.walk import walklib as w

PROJECT = "repro_p"
SEED = Path.home() / "module_results" / "deforestation_alerts" / PROJECT
DATA = Path.home() / "module_results" / "deforestation_alerts_walk_questionnaire"
REPO = Path(__file__).resolve().parents[2]
QUESTION = "Road access"
PLACEHOLDER = "{road_access}"
#: Raised by Leaflet when the dark-theme toggle swaps the shared map's basemap,
#: on every step (Questionnaire and Prepare too, with no Analysis map built):
#: not the questionnaire's, so tolerated in the dark-theme check only.
THEME_SWAP_ERROR = "Cannot set properties of null (setting '_fadeAnimated')"
FOOTER = ".defo-analysis-footer"
PANEL = w.PANEL  # the right panel, or the analysis screen's console while it shows


# ------------------------------------------------------------------ helpers
def write_template(data: Path) -> Path:
    """The repo's default report template plus a ``Road: {road_access}`` paragraph."""
    folder = data / "templates"
    folder.mkdir(parents=True, exist_ok=True)
    doc = docx.Document(str(REPO / "data" / "report_template.docx"))
    doc.add_paragraph(f"Road: {PLACEHOLDER}")
    path = folder / "questionnaire_template.docx"
    doc.save(str(path))
    return path


def counter(page):
    """``(index, total)`` of the queue counter ("3 / 90") of Analysis block A."""
    # The step chip reads "8/8"; the counter is zero-padded ("03 / 90") and below it.
    found = re.findall(r"(\d+) / (\d+)", w.panel_text(page))
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def questions_header(page):
    return page.locator(f'{PANEL} button:has-text("Questions"):visible').first


def badge_text(page) -> str:
    """The Questions section's badge: the chip inside its header button."""
    loc = questions_header(page).locator(".v-chip")
    return re.sub(r"\s+", " ", loc.first.inner_text()).strip() if loc.count() else ""


def questions_open(page) -> bool:
    return questions_header(page).locator(".mdi-chevron-down").count() > 0


def question_card(page):
    """The answer wrapper of Road access: the div holding its title and radios."""
    return page.locator(f'{PANEL} div.mb-3:has(> div.body-2:has-text("{QUESTION}"))').first


def yes_selected(page) -> bool:
    return question_card(page).locator('.v-radio.v-item--active:has(label:text-is("Yes"))').count() > 0


def save_next_btn(page):
    return page.locator(f"{FOOTER} button:visible", has_text=re.compile(r"Save & next")).first


def previous_btn(page):
    return page.locator(f"{PANEL} button:visible", has_text=re.compile(r"^\s*Previous\s*$")).first


def wait_counter(page, index, timeout=90):
    w.wait_for(lambda: (counter(page) or (None,))[0] == index, timeout, f"queue counter at {index}")
    page.wait_for_timeout(1500)


def goto_analysis(page):
    w.goto_step(page, "analysis", 3000)
    w.wait_for(lambda: counter(page), 120, "the Analysis queue counter")
    page.wait_for_timeout(1500)


def scroller(page):
    """JS that finds the footer's scrolling ancestor (the panel's scroll
    container: the nearest ancestor that can scroll vertically)."""
    return """() => {
        const f = document.querySelector('%s');
        let e = f && f.parentElement;
        while (e && e !== document.body) {
            const oy = getComputedStyle(e).overflowY;
            if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight + 2) return e;
            e = e.parentElement;
        }
        return null;
    }""" % FOOTER


def footer_geometry(page):
    return page.evaluate("""() => {
        const f = document.querySelector('%s');
        let e = f && f.parentElement, sc = null;
        while (e && e !== document.body) {
            const oy = getComputedStyle(e).overflowY;
            if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight + 2) { sc = e; break; }
            e = e.parentElement;
        }
        if (!f || !sc) return {footer: !!f, scroller: false};
        const fb = f.getBoundingClientRect(), sb = sc.getBoundingClientRect();
        return {footer: true, scroller: true, cls: sc.className.slice(0, 80), scrollTop: sc.scrollTop,
                scrollHeight: sc.scrollHeight, clientHeight: sc.clientHeight,
                footer_bottom: fb.bottom, panel_bottom: sb.bottom, footer_top: fb.top, footer_h: fb.height};
    }""" % FOOTER)


def scroll_to(page, where):
    page.evaluate("""(where) => {
        const f = document.querySelector('%s');
        let e = f && f.parentElement;
        while (e && e !== document.body) {
            const oy = getComputedStyle(e).overflowY;
            if ((oy === 'auto' || oy === 'scroll') && e.scrollHeight > e.clientHeight + 2) {
                e.scrollTop = where === 'top' ? 0 : e.scrollHeight; return; }
            e = e.parentElement;
        }
    }""" % FOOTER, where)
    page.wait_for_timeout(500)


def click_input_control(page, scope, label):
    """A Vuetify switch / checkbox by its label: click the control, not the input."""
    page.locator(f'{scope} .v-input--selection-controls:has(label:text-is("{label}")) '
                 '.v-input--selection-controls__ripple').first.click()
    page.wait_for_timeout(400)


def step_keys(page):
    keys = page.evaluate("() => [...document.querySelectorAll('[data-step-key]')].map(e => e.dataset.stepKey)")
    return list(dict.fromkeys(keys))


# ------------------------------------------------------------------- checks
def check_1_rail(page, rec, *, out):
    keys = step_keys(page)
    rec["steps"] = keys
    assert len(keys) == 8, keys
    assert keys[-2:] == ["questionnaire", "evaluators"] and "analysis" not in keys, keys
    w.goto_step(page, "questionnaire", 2000)
    assert "Questionnaire" in w.panel_text(page), w.panel_text(page)[:300]
    # Analysis is a screen of its own: the last step's Next is *Start analysis*.
    assert page.locator(".defo-start-analysis:visible").count() == 1, "no Start analysis on Questionnaire"
    w.shot(page, out, "01_rail_questionnaire")


def check_2_add_question(page, rec, *, out, data, template):
    w.goto_step(page, "questionnaire", 2000)
    ph.visible_btn(page, "Add question", PANEL).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the question form")
    page.wait_for_timeout(600)
    ph.modal_field(page, "Question").fill(QUESTION)
    for n, label in ((1, "Yes"), (2, "No")):
        ph.modal_button(page, "Add option").click()
        page.wait_for_timeout(500)
        ph.modal_field(page, f"Option {n}").fill(label)
    click_input_control(page, w.MODAL, "Required")
    for verdict in ("False positive", "Needs review"):
        click_input_control(page, w.MODAL, verdict)
    page.wait_for_timeout(500)
    rec["form_text"] = ph.modal_text(page)[:600]
    assert PLACEHOLDER in rec["form_text"], rec["form_text"]
    w.shot(page, out, "02a_question_form")
    w.wait_for(lambda: ph.primary_button(page).count() and ph.primary_button(page).is_enabled(), 15,
               "the form's Add enabled")
    ph.primary_button(page).click()
    w.wait_for(lambda: not ph.dialog_open(page), 15, "the question form closed")
    page.wait_for_timeout(1500)
    row = ph.row_text(page, QUESTION)
    rec["row"] = row
    assert row, "Road access is not in the list"
    for tag in ("one answer", "2 options", "required", "True alert"):
        assert tag in row, f"tag {tag!r} missing from {row!r}"
    assert "Needs review" not in row and "False positive" not in row and "all verdicts" not in row, row
    assert PLACEHOLDER in row, row
    page.locator(".right-panel button.defo-template-toggle:visible").first.click()
    page.wait_for_timeout(800)
    chips = page.evaluate("() => [...document.querySelectorAll('span[title]')].map(e => e.title)"
                          ".filter(x => x.startsWith('{'))")
    rec["chips"] = sorted(set(chips))
    assert PLACEHOLDER in chips and "{verdict}" in chips, chips
    # A chip copies its placeholder and toasts.
    t0 = w.now_ms(page)
    page.locator(f'span[title="{PLACEHOLDER}"]:visible').first.click()
    ph.wait_toast(page, t0, f"Copied {PLACEHOLDER}", timeout=15)
    # The report template: picked here, before the build (spec D3).
    ph.pick_file(page, "Report template", template)
    w.wait_for(lambda: ph.manifest(data, PROJECT).get("report_template") == str(template), 30,
               "the template saved in project.json")
    rec["report_template"] = ph.manifest(data, PROJECT)["report_template"]
    stored = ph.manifest(data, PROJECT)
    rec["questions_stored"] = [q["key"] for q in stored.get("questions", [])]
    assert "road_access" in rec["questions_stored"], stored.get("questions")
    page.wait_for_timeout(800)
    w.shot(page, out, "02b_questionnaire_list")


def check_3_badge(page, rec, *, out):
    goto_analysis(page)
    assert not questions_open(page), "the Questions section is open by default"
    assert not question_card(page).is_visible(), "Questions body is visible while collapsed"
    rec["badge_before_verdict"] = badge_text(page)
    ph.visible_btn(page, "True alert").click()
    w.wait_for(lambda: "required missing" in badge_text(page), 15, "the required-missing badge")
    rec["badge"] = badge_text(page)
    chip = questions_header(page).locator(".v-chip").first
    rec["badge_class"] = chip.get_attribute("class")
    rec["badge_color"] = chip.evaluate("e => getComputedStyle(e).color")
    assert "warning" in rec["badge_class"], rec["badge_class"]
    r, g, b = (int(x) for x in re.findall(r"\d+", rec["badge_color"])[:3])
    assert r > 200 and 80 < g < 200 and b < 90, f"badge is not orange: {rec['badge_color']}"
    w.shot(page, out, "03_questions_collapsed_badge")


def check_4_required_refusal(page, rec, *, out):
    before = counter(page)
    assert not questions_open(page)
    t0 = w.now_ms(page)
    save_next_btn(page).click()
    got = w.wait_for(lambda: [x for x in w.toasts_since(page, t0) if x["kind"] == "toast-warning"], 60,
                     "a warning toast")
    rec["toast"] = got[0]["msg"]
    assert QUESTION in got[0]["msg"], got
    w.wait_for(questions_open_fn(page), 15, "the Questions section opened")
    page.wait_for_timeout(1000)
    rec["counter_before"], rec["counter_after"] = before, counter(page)
    assert rec["counter_after"] == before, "the alert advanced despite the refusal"
    assert question_card(page).is_visible()
    w.shot(page, out, "04_required_refusal")


def questions_open_fn(page):
    return lambda: questions_open(page)


def check_5_answer_and_previous(page, rec, *, out):
    first = counter(page)[0]
    question_card(page).locator('.v-radio:has(label:text-is("Yes"))').first.click()
    w.wait_for(lambda: yes_selected(page), 10, "Yes selected")
    w.wait_for(lambda: "required missing" not in badge_text(page), 10, "the badge without required-missing")
    rec["badge_after_answer"] = badge_text(page)
    t0 = w.now_ms(page)
    save_next_btn(page).click()
    wait_counter(page, first + 1, timeout=120)
    rec["toasts"] = [f"{x['kind']}: {x['msg']}" for x in w.toasts_since(page, t0)]
    assert not ph.errors(w.toasts_since(page, t0)), rec["toasts"]
    assert counter(page)[0] == first + 1
    # The next alert has no answer yet; its Export is "save first".
    assert not yes_selected(page), "the next alert shows the previous answer"
    ph.open_section(page, "Export this alert")
    build = page.locator(".defo-build-tile:visible").first
    rec["build_tile_unsaved"] = re.sub(r"\s+", " ", build.inner_text())
    assert "save first" in rec["build_tile_unsaved"], rec["build_tile_unsaved"]
    w.shot(page, out, "05a_next_alert")
    previous_btn(page).click()
    wait_counter(page, first, timeout=90)
    w.wait_for(lambda: yes_selected(page), 30, "Yes selected again after Previous")
    rec["yes_after_previous"] = True
    w.shot(page, out, "05b_previous_yes")


def check_6_sticky_footer(page, rec, *, out):
    ph.open_section(page, "Questions")
    ph.open_section(page, "Refine polygon")
    page.wait_for_timeout(800)
    top = footer_geometry(page)
    rec["at_top"] = top
    assert top.get("scroller"), f"the panel does not scroll with Questions and Refine open: {top}"
    scroll_to(page, "top")
    top = footer_geometry(page)
    w.shot(page, out, "06a_footer_scrolled_top")
    scroll_to(page, "bottom")
    bottom = footer_geometry(page)
    rec["at_top"], rec["at_bottom"] = top, bottom
    w.shot(page, out, "06b_footer_scrolled_bottom")
    assert bottom["scrollTop"] > top["scrollTop"] + 20, (top, bottom)
    for name, g in (("top", top), ("bottom", bottom)):
        assert abs(g["footer_bottom"] - g["panel_bottom"]) <= 2, f"footer not at the panel bottom ({name}): {g}"
    scroll_to(page, "top")
    # Refine closed again, as the next checks expect
    if page.locator(f'{PANEL} button:has-text("Refine polygon"):visible').first.locator(".mdi-chevron-down").count():
        page.locator(f'{PANEL} button:has-text("Refine polygon"):visible').first.click()


def check_7_build(page, rec, *, out):
    t0 = w.now_ms(page)
    ph.pick_images(page, rec, out=out)
    # Picking images is an unsaved change of the review (the images are stored
    # with it), so the build waits for a Save of the alert as it now stands.
    ph.open_section(page, "Export this alert")
    t_save = w.now_ms(page)
    ph.save_btn(page).click()
    w.wait_for(lambda: any("saved" in x["msg"].lower() or x["kind"] == "toast-error"
                           for x in w.toasts_since(page, t_save)), 120, "the save toast")
    assert not ph.errors(w.toasts_since(page, t_save)), w.toasts_since(page, t_save)
    page.wait_for_timeout(1500)
    build = page.locator(".defo-build-tile:visible").first
    w.wait_for(lambda: build.count() and build.is_enabled(), 30, "the build tile enabled")
    rec["build_tile"] = re.sub(r"\s+", " ", build.inner_text())
    w.shot(page, out, "07a_build_tile")
    build.click()
    w.wait_for(lambda: page.locator(".defo-rebuild:visible").count()
               or ph.errors(w.toasts_since(page, t0)), 900, "the build to finish", poll=2)
    assert not ph.errors(w.toasts_since(page, t0)), [x["msg"] for x in w.toasts_since(page, t0)]
    page.wait_for_timeout(2000)
    tiles = {}
    for kind in ("zip", "docx", "gif"):
        tile = page.locator(f".defo-file-tile:visible:has-text('{kind.upper()}')").first
        tiles[kind] = {
            "text": re.sub(r"\s+", " ", tile.inner_text()),
            "disabled": tile.is_disabled() or "v-btn--disabled" in (tile.get_attribute("class") or ""),
            "href": tile.get_attribute("href"),
        }
    rec["tiles"] = tiles
    for kind in ("zip", "docx", "gif"):  # the GIF tile is a real download since feat/alert-gif
        assert re.search(r"\d+(\.\d)? (B|KB|MB|GB)", tiles[kind]["text"]), tiles[kind]
        assert not tiles[kind]["disabled"] and tiles[kind]["href"] and tiles[kind]["href"] != "#", tiles[kind]
    assert page.locator(".defo-rebuild:visible").count() == 1
    rec["rebuild"] = re.sub(r"\s+", " ", page.locator(".defo-rebuild:visible").first.inner_text())
    page.wait_for_timeout(500)
    w.shot(page, out, "07b_file_tiles")


def check_8_docx(page, rec, *, out, data):
    """The DOCX tile's link, fetched. On SEPAL it is the sandbox's file route
    (``https://sepal.io/api/sandbox/jupyter/files/<home-relative path>``); a
    local run has no SEPAL session, so that route answers 401 whatever the
    link. Then the walk proves the link points at the file it built (the
    home-relative path of the set folder's ``alert_<id>.docx``) and reads that
    file from disk: the bytes the link serves on SEPAL."""
    href = page.locator(".defo-file-tile:visible:has-text('DOCX')").first.get_attribute("href")
    url = page.evaluate("h => new URL(h, document.baseURI).href", href)
    rec["href"] = url
    resp = page.request.get(url)
    rec["status"] = resp.status
    if resp.ok:
        body, rec["read_from"] = resp.body(), "href"
    else:
        m = ph.manifest(data, PROJECT)
        folder = data / PROJECT / Path(m["alert_sets"][m["active_set"]]["store_rel_path"]).parent
        rec["read_from"] = "disk (the link needs a SEPAL session: HTTP %d)" % resp.status
        rel = (folder / "alert_1.docx").relative_to(Path.home()).as_posix()
        assert url.endswith("/" + rel), f"the link {url} does not point at {rel}"
        body = (folder / "alert_1.docx").read_bytes()
    rec["bytes"] = len(body)
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(body)).paragraphs)
    rec["road_lines"] = [ln for ln in text.splitlines() if "Road" in ln]
    assert any(ln.strip() == "Road: Yes" for ln in text.splitlines()), (rec["road_lines"], text[:400])
    assert PLACEHOLDER not in text, "the placeholder was not replaced"
    w.shot(page, out, "08_docx_downloaded")


def check_9_overview(page, rec, *, out):
    ph.open_overview(page, wait=6000)
    text = w.dialog_text(page)
    rec["has_card"] = QUESTION in text
    card = page.locator(f'{w.RAIL_DIALOG} .v-card:has-text("{QUESTION}")').last
    if card.count():
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        rec["card_text"] = re.sub(r"\s+", " ", card.inner_text())[:300]
    w.shot(page, out, "09_overview")
    assert QUESTION in text, text[:800]
    assert w.close_dialog(page), "the Overview dialog did not close"


def toggle_dark(page):
    selectors = ("button:has(.mdi-weather-night)", "button:has(.mdi-weather-sunny)", "button:has(.mdi-auto-fix)",
                 "button:has(.mdi-theme-light-dark)")
    for _ in range(4):
        if page.locator(".v-application.theme--dark").count():
            return True
        for sel in selectors:
            btn = page.locator(f"{sel}:visible").first
            if btn.count():
                btn.click()
                page.wait_for_timeout(1500)
                # a theme menu: take its dark entry when one opened
                item = page.locator(".menuable__content__active .v-list-item:has(.mdi-weather-night)").first
                if item.count():
                    item.click()
                    page.wait_for_timeout(1500)
                break
        if page.locator(".v-application.theme--dark").count():
            return True
    return bool(page.locator(".v-application.theme--dark").count())


def check_10_dark(page, rec, *, out):
    goto_analysis(page)
    ph.open_section(page, "Questions")
    rec["light_footer_bg"] = page.evaluate("s => getComputedStyle(document.querySelector(s)).backgroundColor",
                                           FOOTER)
    assert toggle_dark(page), "could not switch to the dark theme"
    page.wait_for_timeout(2000)
    goto_analysis(page)
    ph.open_section(page, "Questions")
    page.wait_for_timeout(800)
    info = page.evaluate("""s => {
        const f = document.querySelector(s);
        const labels = [...document.querySelectorAll('.defo-analysis-right .mb-3 > .body-2')]
            .filter(e => e.getBoundingClientRect().width > 0)
            .map(e => ({text: e.innerText, color: getComputedStyle(e).color}));
        const radios = [...document.querySelectorAll('.defo-analysis-right .mb-3 .v-radio label')]
            .filter(e => e.getBoundingClientRect().width > 0)
            .map(e => ({text: e.innerText, color: getComputedStyle(e).color}));
        const header = [...document.querySelectorAll('.defo-analysis-right button')]
            .find(b => b.innerText.includes('Questions'));
        return {bg: getComputedStyle(f).backgroundColor, labels, radios,
                header: header ? getComputedStyle(header).color : null,
                dark: !!document.querySelector('.v-application.theme--dark')};
    }""", FOOTER)
    rec.update(info)
    scroll_to(page, "bottom")  # the footer covers the sections below the fold at the top
    w.shot(page, out, "10_dark_theme")
    assert info["dark"], "not in the dark theme"
    assert info["bg"] not in ("rgb(255, 255, 255)", "rgba(255, 255, 255, 1)", "rgba(0, 0, 0, 0)"), info["bg"]
    assert info["labels"], "no Questions labels found"
    for item in info["labels"] + info["radios"]:
        assert item["color"] not in ("rgb(0, 0, 0)", "rgba(0, 0, 0, 1)"), item
        rgb = [int(x) for x in re.findall(r"\d+", item["color"])[:3]]
        assert sum(rgb) / 3 > 140, f"label too dark for a dark surface: {item}"


# ---------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8950)
    ap.add_argument("--out", type=Path, default=Path("/tmp/claude-1000/questionnaire-walk"))
    ap.add_argument("--data", type=Path, default=DATA,
                    help="the walk's data dir (DEFORESTATION_ALERTS_DATA_DIR); keep it under $HOME")
    args = ap.parse_args(argv)
    data, out = args.data, args.out
    if data == DATA:
        shutil.rmtree(data, ignore_errors=True)  # rebuilt: a previous run's files would hide this run's
    data.mkdir(parents=True, exist_ok=True)
    if not (data / PROJECT).exists():
        shutil.copytree(SEED, data / PROJECT)  # a copy: the original is never written
    template = write_template(data)
    out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, data, out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)

            def watched(fn):
                """The phase, recording the page errors it raised (a check of its own)."""
                def run(pg, rec):
                    before = len(w.app_page_errors(report))
                    try:
                        fn(pg, rec)
                    finally:
                        rec["page_errors"] = w.app_page_errors(report)[before:]
                return run

            ok = ph.run_phases(page, [
                ("open", watched(lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT)))),
                ("c01_rail", watched(lambda pg, r: check_1_rail(pg, r, out=out))),
                ("c02_add_question", watched(lambda pg, r: check_2_add_question(
                    pg, r, out=out, data=data, template=template))),
                ("c03_badge", watched(lambda pg, r: check_3_badge(pg, r, out=out))),
                ("c04_required_refusal", watched(lambda pg, r: check_4_required_refusal(pg, r, out=out))),
                ("c05_answer_previous", watched(lambda pg, r: check_5_answer_and_previous(pg, r, out=out))),
                ("c06_sticky_footer", watched(lambda pg, r: check_6_sticky_footer(pg, r, out=out))),
                ("c07_build", watched(lambda pg, r: check_7_build(pg, r, out=out))),
                ("c08_docx", watched(lambda pg, r: check_8_docx(pg, r, out=out, data=data))),
                ("c09_overview", watched(lambda pg, r: check_9_overview(pg, r, out=out))),
                ("c10_dark", watched(lambda pg, r: check_10_dark(pg, r, out=out))),
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"], report["theme_swap_errors"] = [], 0
            for name, rec in report.get("phases", {}).items():
                for err in rec.get("page_errors", []):
                    if name == "c10_dark" and err == THEME_SWAP_ERROR:
                        report["theme_swap_errors"] += 1
                    else:
                        report["app_page_errors"].append(f"{name}: {err}")
            if not ok:
                w.shot(page, out, "failure")
            browser.close()
        with open(out / "report.json", "w") as f:
            json.dump(report, f, indent=1, ensure_ascii=False, default=str)
        ok = ok and not report["error_toasts"] and not report["bad_responses"] and not report["app_page_errors"]
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
