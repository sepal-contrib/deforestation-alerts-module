"""Evaluator split and review-sharing walk (evaluator-split plan, Task 14).

    python -m tools.walk.evaluators_walk --port 8960 --out /tmp/claude-1000/evaluators-walk

The walk copies ``~/module_results/deforestation_alerts/repro_p`` (its active
set: 90 unreviewed GLAD-L alerts; an Earth Engine ADMIN1 AOI without a
sidecar) into its own data dir and plays both sides of spec 2026-10-10 §10:

 1. creator: Evaluators step, Count 2 → Split: 45 / 45, toast
 2. creator: Download package for Evaluator 1 (after the AOI step's replay): link, zip members, packaged
 3. evaluator: Project → Import the package → lands on Analysis with the evaluator chip, 45 alerts
    (the chip text is built from the catalogue: ``analysis_tile.filter.chip``)
 4. evaluator: review and Save, Next, review and Save: two saved rows, both Evaluator 1's
 5. evaluator: Evaluators step shows Return reviews (2 / 45) → Download reviewed copy
 6. creator: reopen repro_p → Evaluators → Import the reviewed copy → preview → Merge → table and store

It needs Earth Engine and sepal.io (the AOI replay, the image timeline), so
it runs outside a sandbox.
"""

import argparse
import json
import re
import shutil
import sys
import traceback
import zipfile
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

from tools.walk import phases as ph
from tools.walk import walklib as w

PROJECT = "repro_p"
SEED = Path.home() / "module_results" / "deforestation_alerts" / PROJECT
DATA = Path.home() / "module_results" / "deforestation_alerts_walk_evaluators"
EVALUATOR = "Evaluator 1"
NOT_REVIEWED = ("", "Not reviewed")
RIGHT = ".right-panel"


# ------------------------------------------------------------------ helpers
def set_folder(data, project) -> Path:
    m = ph.manifest(data, project)
    return (Path(data) / project / m["alert_sets"][m["active_set"]]["store_rel_path"]).parent


def store(data, project) -> pd.DataFrame:
    return pd.read_parquet(set_folder(data, project) / "alert_db.parquet", columns=["alert_id", "status", "evaluator"])


def reviewed(frame) -> pd.DataFrame:
    return frame[frame["status"].notna() & ~frame["status"].isin(NOT_REVIEWED)]


def split_record(data, project):
    m = ph.manifest(data, project)
    return m["alert_sets"][m["active_set"]].get("split")


def review_copies(data) -> list:
    return [p.parent.name for p in sorted(Path(data).glob("*/project.json"))
            if json.loads(p.read_text(encoding="utf-8")).get("review_copy")]


def counter(page):
    """``(index, matches)`` of the Analysis queue counter."""
    found = re.findall(r"(\d+) / (\d+)", w.panel_text(page))
    return (int(found[-1][0]), int(found[-1][1])) if found else None


def open_project_dialog(page):
    w.close_dialog(page)
    page.locator('[data-step-id="2"]').first.click()
    btn = page.locator(f"{w.RAIL_DIALOG} button:has(.mdi-import):visible").first
    w.wait_for(lambda: btn.count(), 30, "the Import button")
    return btn


# ------------------------------------------------------------------- phases
def p1_split(page, rec, *, out, data):
    w.goto_step(page, "evaluators", 2000)
    keys = list(dict.fromkeys(page.evaluate(
        "() => [...document.querySelectorAll('[data-step-key]')].map(e => e.dataset.stepKey)")))
    rec["steps"] = keys
    assert keys[-2:] == ["questionnaire", "evaluators"], keys
    field = page.locator(f'{RIGHT} .v-text-field:has(label:text-is("{w.en("evaluators.count_label")}")) input').first
    field.fill("2")
    t0 = w.now_ms(page)
    ph.visible_btn(page, w.en("evaluators.split"), RIGHT).click()
    ph.wait_toast(page, t0, "split between 2", kind="success")
    page.wait_for_timeout(1500)
    rec["rows"] = {n: ph.row_text(page, n) for n in (EVALUATOR, "Evaluator 2")}
    w.shot(page, out, "01_split")
    assert all(re.search(r"\b45\b", text) for text in rec["rows"].values()), rec["rows"]
    counts = store(data, PROJECT)["evaluator"].value_counts().to_dict()
    rec["store_counts"] = counts
    assert counts == {EVALUATOR: 45, "Evaluator 2": 45}, counts


def p2_package(page, rec, *, out, data):
    btn = ph.row_button(page, EVALUATOR, "mdi-cloud-download-outline")
    # repro_p's ADMIN1 AOI has no sidecar: the button waits for the AOI step's replay.
    w.wait_for(lambda: btn.count() and btn.is_enabled(), 240, "Download package enabled")
    btn.click()
    link = page.locator(f"{RIGHT} .defo-package-link:visible").first
    w.wait_for(lambda: link.count(), 300, "the package link")
    rec["href"] = link.get_attribute("href")
    zips = sorted(set_folder(data, PROJECT).glob("*__review.zip"))
    rec["zips"] = [z.name for z in zips]
    assert len(zips) == 1, rec["zips"]
    with zipfile.ZipFile(zips[0]) as zf:
        rec["members"] = sorted(zf.namelist())
    assert {"project.json", "aoi.geojson", "alerts/alert_db.parquet"} <= set(rec["members"]), rec["members"]
    assert rec["href"].endswith(zips[0].name), rec["href"]
    assert split_record(data, PROJECT)["packaged"] is True
    rec["package"] = str(zips[0])
    w.shot(page, out, "02_package")


def p3_import(page, rec, *, out, data, package):
    open_project_dialog(page).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the import form")
    ph.pick_file(page, w.en("steps.project.import_pick"), package, scope=w.MODAL)
    w.wait_for(lambda: review_copies(data), 180, "the review copy on disk")
    (copy,) = review_copies(data)
    rec["copy"] = copy
    w.wait_for(lambda: w.title(page).endswith(f"· {copy}"), 120, f"title {copy}")
    w.wait_for(lambda: page.locator(w.SCREEN_ON).count(), 120, "the analysis screen")
    chip = w.en("analysis_tile.filter.chip").format(attribute=w.en("analysis_tile.filter.col_evaluator"),
                                                     values=EVALUATOR)
    w.wait_for(lambda: chip in w.panel_text(page), 120, "the evaluator chip")
    w.wait_for(lambda: (counter(page) or (0, 0))[1] == 45, 180, "45 alerts in the filter")
    rec["counter"] = counter(page)
    # The chip is the copy's only statement of whose alerts these are: it is
    # shown in full (it was cut to "Eval…" at 1400 px), with a full tooltip.
    rec["chip"] = page.evaluate(
        "() => { const c = document.querySelector('.defo-analysis-screen .defo-filter-chip');"
        " if (!c) return null; const t = c.querySelector('.v-chip__content') || c;"
        " return {title: c.getAttribute('title'), text: t.textContent.trim(),"
        " chip: [c.scrollWidth, c.clientWidth], content: [t.scrollWidth, t.clientWidth]}; }")
    assert rec["chip"] is not None, "no filter chip on the screen"
    assert rec["chip"]["title"] == chip, rec["chip"]
    for part in ("chip", "content"):
        scroll, client = rec["chip"][part]
        assert scroll <= client, f"the chip's {part} is clipped: {rec['chip']}"
    w.shot(page, out, "03_imported")


def p4_review(page, rec, *, out, data, copy):
    ph.review_and_save(page, rec.setdefault("first", {}), out=out)
    nxt = page.locator(f"{w.PANEL} button:has(.mdi-chevron-right):visible",
                       has_text=w.en("analysis_tile.nav.next")).first
    nxt.click()
    w.wait_for(lambda: (counter(page) or (0,))[0] == 2, 90, "the second of Evaluator 1's alerts")
    rec["counter_after_next"] = counter(page)
    ph.review_and_save(page, rec.setdefault("second", {}), out=out)
    saved = reviewed(store(data, copy))
    rec["saved"] = saved.to_dict("records")
    assert len(saved) == 2 and set(saved["evaluator"]) == {EVALUATOR}, rec["saved"]


def p5_return(page, rec, *, out, data, copy):
    w.goto_step(page, "evaluators", 2000)
    text = w.panel_text(page)
    rec["panel"] = text[:400]
    assert "2 / 45" in text and w.en("evaluators.return_note") in text, text[:400]
    ph.visible_btn(page, w.en("evaluators.download_return"), RIGHT).click()
    link = page.locator(f"{RIGHT} .defo-return-link:visible").first
    w.wait_for(lambda: link.count(), 120, "the reviewed-copy link")
    zips = sorted(set_folder(data, copy).glob("*__reviewed.zip"))
    assert len(zips) == 1, [z.name for z in zips]
    rec["returned"] = str(zips[0])
    w.shot(page, out, "05_return")


def p6_merge(page, rec, *, out, data, returned):
    w.open_project(page, PROJECT)
    w.goto_step(page, "evaluators", 2000)
    ph.pick_file(page, w.en("evaluators.import_label"), returned, scope=RIGHT)
    w.wait_for(lambda: ph.dialog_open(page) and "2 new reviews" in ph.modal_text(page), 120, "the merge preview")
    rec["preview"] = ph.modal_text(page)[:400]
    w.shot(page, out, "06_preview")
    t0 = w.now_ms(page)
    ph.modal_button(page, w.en("evaluators.merge")).click()
    ph.wait_toast(page, t0, w.en("evaluators.merged").format(taken=2, evaluator=EVALUATOR), kind="success")
    page.wait_for_timeout(1500)
    merged = reviewed(store(data, PROJECT))
    rec["merged"] = merged.to_dict("records")
    assert len(merged) == 2 and set(merged["evaluator"]) == {EVALUATOR}, rec["merged"]
    assert EVALUATOR in split_record(data, PROJECT)["returns"]
    rec["row"] = ph.row_text(page, EVALUATOR)
    assert re.search(r"\b2\b", rec["row"]) and re.search(r"\d{4}-\d{2}-\d{2}", rec["row"]), rec["row"]
    w.shot(page, out, "06_merged")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8960)
    ap.add_argument("--out", type=Path, default=Path("/tmp/claude-1000/evaluators-walk"))
    ap.add_argument("--data", type=Path, default=DATA,
                    help="the walk's data dir (DEFORESTATION_ALERTS_DATA_DIR); keep it under $HOME")
    args = ap.parse_args(argv)
    data, out = args.data, args.out
    if data == DATA:
        shutil.rmtree(data, ignore_errors=True)  # rebuilt: a previous run's copies would hide this run's
    data.mkdir(parents=True, exist_ok=True)
    if not (data / PROJECT).exists():
        shutil.copytree(SEED, data / PROJECT)  # a copy: the original is never written
    out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, data, out / "server.log")
    report, ok, ctx = {}, False, {}
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

            def _p2(pg, r):
                p2_package(pg, r, out=out, data=data)
                ctx["package"] = r["package"]

            def _p3(pg, r):
                p3_import(pg, r, out=out, data=data, package=ctx["package"])
                ctx["copy"] = r["copy"]

            def _p5(pg, r):
                p5_return(pg, r, out=out, data=data, copy=ctx["copy"])
                ctx["returned"] = r["returned"]

            ok = ph.run_phases(page, [
                ("open", watched(lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT)))),
                ("p1_split", watched(lambda pg, r: p1_split(pg, r, out=out, data=data))),
                ("p2_package", watched(_p2)),
                ("p3_import", watched(_p3)),
                ("p4_review", watched(lambda pg, r: p4_review(pg, r, out=out, data=data, copy=ctx["copy"]))),
                ("p5_return", watched(_p5)),
                ("p6_merge", watched(lambda pg, r: p6_merge(pg, r, out=out, data=data, returned=ctx["returned"]))),
            ], report, out / "report.json")
            report["error_toasts"] = [x for x in w.toasts_since(page) if x["kind"] == "toast-error"]
            report["app_page_errors"] = [f"{name}: {err}" for name, rec in report.get("phases", {}).items()
                                         for err in rec.get("page_errors", [])]
            if not ok:
                w.shot(page, out, "failure")
            browser.close()
        with open(out / "report.json", "w") as f:
            json.dump(report, f, indent=1, ensure_ascii=False, default=str)
        ok = ok and not report["error_toasts"] and not report["bad_responses"] and not report["app_page_errors"]
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        ok = False
    finally:
        w.stop_server(proc)
    w.log(f"walk {'PASSED' if ok else 'FAILED'}; report: {out / 'report.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
