"""Build the user guide's demo project, ``madre_de_dios``, through the app.

    python -m tools.docs.demo_project --data-dir DIR [--port N] [--headed]
        [--ee-project NAME] [--ee-max-requests N]

Starts the app (``./run_ui.sh``, see ``tools.walk.walklib``) on ``DIR`` as its
data directory and drives the real UI with Playwright, the way a user builds
a project: New project, the AOI (Admin level 1: Peru / Madre de Dios), three
predefined sources, one Sentinel-2 visualization layer shown in Analysis, one
period, the grid, one forest mask, Download & harmonize, Integrate &
vectorize (minimum size and maximum count), a few reviews, a split between
two evaluators, their review packages, and one evaluator's reviewed copy.

Every stage is skipped when its output already exists (read from the
project's ``project.json``, its alert store or the fixture files), so a run
that failed or was stopped is simply run again. Downloads take a while: the
log says what is going on (``DIR/_build/server.log`` is the app's own log).

What ends up in ``DIR`` (never commit it):

- ``madre_de_dios/``: the demo project;
- ``_fixtures/``: the review package of the first evaluator, the reviewed copy
  that evaluator sends back (never merged into the demo), and the evaluator's
  review copy project folder it was made in;
- ``_build/``: the run's report, screenshots of each stage and the server log.

The contents and the numbers of the last build are in ``tools/docs/README.md``.
Earth Engine runs with the account of whoever runs it; ``--ee-project`` picks
another Cloud project for the build's server only (see ``server_hooks/``).
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from deforestation_alerts.project.review_status import reviewed_mask
from tools.walk import phases as ph
from tools.walk import walklib as w

PROJECT = "madre_de_dios"
COUNTRY, ADMIN1 = "Peru", "Madre de Dios"
SOURCES = ("GLAD-L", "RADD", "GLAD-S2")
#: The period and its look-back: the latest whole month every source covers.
PERIOD = ("2026-09-01", "2026-09-30")
LOOK_BACK = 90
#: The visualization layer: the account has no Planet NICFI access, so a
#: Sentinel-2 image of the mining corridor east of Puerto Maldonado (tile
#: 19LDF, 27 September 2026, 1.5 % cloud), in true colour.
LAYER_IMAGE = "COPERNICUS/S2_SR_HARMONIZED/20260927T144729_20260927T145044_T19LDF"
LAYER_NAME = "Sentinel-2 2026-09-27"
LAYER_RANGE = ("0", "3000")
#: The mask: Hansen's 2000 tree cover, kept where it is not zero.
MASK_ASSET = "UMD/hansen/global_forest_change_2025_v1_13"
MASK_BAND = "treecover2000"
MASK_NAME = "forest_2000"
GRID_RESOLUTION = "10"
MIN_SIZE_HA = "1"
MAX_ALERTS = "48"
EVALUATORS = ("Ana", "Luis")
#: ``(store position, verdict, loss driver)``; the positions are 0-based in
#: the set's order (bigger alerts first). The first two are reviewed before
#: the split, so they stay unassigned; the other three after it.
REVIEWS_BEFORE_SPLIT = ((0, "true", "loss_driver6"), (1, "false", None))
REVIEWS_AFTER_SPLIT = ((2, "true", "loss_driver1"), (3, "revision", "loss_driver3"), (4, "true", "loss_driver6"))
#: The first evaluator's reviews in their review copy, in their queue order.
COPY_REVIEWS = (("true", "loss_driver6"), ("true", "loss_driver1"), ("false", None))

RIGHT = ".right-panel"
STAGE_TIMEOUTS = {"prepare": 6 * 3600, "integrate": 2 * 3600}


# ------------------------------------------------------------------ state on disk
def manifest(data: Path, name: str = PROJECT):
    path = data / name / "project.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def active_set(data: Path, name: str = PROJECT):
    m = manifest(data, name) or {}
    sid = m.get("active_set")
    return (m.get("alert_sets") or {}).get(sid) if sid else None


def set_folder(data: Path, name: str = PROJECT) -> Path:
    s = active_set(data, name)
    return (data / name / s["store_rel_path"]).parent


def store(data: Path, name: str = PROJECT) -> pd.DataFrame:
    s = active_set(data, name)
    return pd.read_parquet(data / name / s["store_rel_path"])


def reviewed_count(data: Path, name: str = PROJECT) -> int:
    if not active_set(data, name):
        return 0
    return int(reviewed_mask(store(data, name)).sum())


def review_copies(data: Path) -> list:
    return [p.parent.name for p in sorted(data.glob("*/project.json"))
            if json.loads(p.read_text(encoding="utf-8")).get("review_copy")]


def fixtures(data: Path) -> Path:
    return data / "_fixtures"


def package_fixture(data: Path):
    return next(iter(sorted(fixtures(data).glob("*__review.zip"))), None)


def return_fixture(data: Path):
    return next(iter(sorted(fixtures(data).glob("*__reviewed.zip"))), None)


# ------------------------------------------------------------------ ui helpers
def choose_long(page, label, option, scope=RIGHT, timeout=120):
    """Pick the item reading exactly ``option`` in a long select (Vuetify 2
    renders a long list in steps as it scrolls): scroll its menu until the
    item is there. Waits for the select to be enabled and its items loaded."""
    slot = page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last
    sel = page.locator(f'{scope} .v-select:has(label:text-is("{label}"))').last
    w.wait_for(lambda: sel.count() and "v-input--is-disabled" not in (sel.get_attribute("class") or "")
               and "v-input--is-loading" not in (sel.get_attribute("class") or ""), timeout, f"{label} enabled")
    menu = page.locator(".menuable__content__active").last
    item = menu.locator(".v-list-item", has=page.locator(".v-list-item__title", has_text=re.compile(
        rf"^\s*{re.escape(option)}\s*$", re.IGNORECASE))).first
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not menu.count():
            slot.click()
            page.wait_for_timeout(800)
            continue
        if item.count():
            item.scroll_into_view_if_needed()
            item.click()
            page.wait_for_timeout(1000)
            return
        menu.evaluate("e => { e.scrollTop = e.scrollTop + e.clientHeight / 2; }")
        page.wait_for_timeout(300)
    seen = menu.evaluate("e => e.innerText") if menu.count() else ""
    raise AssertionError(f"{option!r} not found in {label}: {seen[-600:]!r}")


def switch_on(page, label, scope=w.MODAL):
    box = page.locator(f'{scope} .v-input:has(label:text-is("{label}"))').last
    if not box.locator("input").first.is_checked():
        box.locator(".v-input--selection-controls__input").first.click()
        page.wait_for_timeout(1200)


def open_project_dialog(page):
    w.close_dialog(page)
    page.locator('[data-step-id="2"]').first.click()
    w.wait_for(lambda: w.dialog_visible(page), 30, "the Project dialog")
    page.wait_for_timeout(800)


def review_current(page, verdict, driver, rec):
    """Answer the alert on screen: its verdict, the loss driver when one is
    given (the question is asked for True alert and Needs review), then
    **Save** and wait for its toast."""
    page.wait_for_timeout(2500)
    ph.visible_btn(page, w.en(f"analysis_tile.verdict.{verdict}"), ".defo-analysis-right").click()
    page.wait_for_timeout(700)
    if driver:
        ph.open_section(page, w.en("analysis_tile.sections.questions"))
        label = w.en("analysis_tile.questionarie.loss_driver_label")
        box = page.locator(f'.defo-analysis-right .dat-question:has-text("{label}") .v-select__slot:visible').last
        box.click()
        page.wait_for_timeout(700)
        option = w.en(f"analysis_tile.questionarie.{driver}")
        page.locator(f'.menuable__content__active .v-list-item:has-text("{option}")').first.click()
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
    t_ms = w.now_ms(page)
    ph.save_btn(page).click()
    w.wait_for(lambda: any("saved" in x["msg"].lower() or x["kind"] in ("toast-error", "toast-warning")
                           for x in w.toasts_since(page, t_ms)), 120, "the save toast")
    toasts = [f"{x['kind']}: {x['msg']}" for x in w.toasts_since(page, t_ms)]
    rec.setdefault("reviews", []).append({"facts": ph.alert_facts(page), "verdict": verdict, "driver": driver,
                                          "toasts": toasts})
    assert not ph.errors(w.toasts_since(page, t_ms)), toasts
    page.wait_for_timeout(1500)


def counter(page):
    found = re.findall(r"(\d+) / (\d+)", w.panel_text(page))
    return (int(found[-1][0]), int(found[-1][1])) if found else None


# ------------------------------------------------------------------ stages
# Each stage is ``(name, done(data) -> bool, run(page, rec, ctx))``.
def st_project(page, rec, ctx):
    open_project_dialog(page)
    ph.visible_btn(page, w.en("steps.project.new"), w.RAIL_DIALOG).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the New project form")
    ph.modal_field(page, w.en("steps.project.name_label")).fill(PROJECT)
    page.wait_for_timeout(800)
    ph.primary_button(page).click()
    w.wait_for(lambda: w.title(page).endswith(f"· {PROJECT}"), 60, f"title {PROJECT}")
    page.wait_for_timeout(1500)
    w.close_dialog(page)


def st_aoi(page, rec, ctx):
    w.goto_step(page, "aoi", 2000)
    ph.choose(page, "AOI selection method", "Admin level 1", scope=RIGHT)
    choose_long(page, "Country/Province", COUNTRY)
    choose_long(page, "Admin level 1", ADMIN1)
    t0 = w.now_ms(page)
    ph.visible_btn(page, "Select AOI", RIGHT).click()
    w.wait_for(lambda: (manifest(ctx["data"]) or {}).get("aoi"), 600, "the AOI saved")
    page.wait_for_timeout(3000)
    rec["toasts"] = [x["msg"] for x in w.toasts_since(page, t0)]
    rec["aoi"] = {k: manifest(ctx["data"])["aoi"].get(k) for k in ("name", "method", "bounds")}
    w.shot(page, ctx["shots"], "aoi")


def st_sources(page, rec, ctx):
    have = (manifest(ctx["data"]) or {}).get("sources") or {}
    for key in SOURCES:
        if key in have:
            continue
        w.goto_step(page, "alerts", 2000)
        ph.visible_btn(page, w.en("steps.alerts.add"), RIGHT).click()
        w.wait_for(lambda: ph.dialog_open(page), 15, "the source modal")
        ph.choose(page, w.en("steps.alerts.predefined_label"), key)
        button = ph.modal_button(page, w.en("steps.alerts.add"))
        w.wait_for(lambda: button.count() and button.is_enabled(), 180, f"{key}'s coverage checked")
        button.click()
        w.wait_for(lambda: not ph.dialog_open(page), 120, f"{key} added")
        page.wait_for_timeout(1500)
    w.wait_for(lambda: set(SOURCES) <= set((manifest(ctx["data"]) or {}).get("sources") or {}), 30, "the sources saved")
    w.wait_for(lambda: set(SOURCES) <= set((manifest(ctx["data"]) or {}).get("coverage") or {}), 300,
               "the coverage checked")
    rec["coverage"] = {k: v.get("fraction") for k, v in manifest(ctx["data"])["coverage"].items()}
    w.shot(page, ctx["shots"], "sources")


def st_layer(page, rec, ctx):
    w.goto_step(page, "layers", 2000)
    ph.visible_btn(page, w.en("steps.layers.add"), RIGHT).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the layer modal")
    ph.modal_field(page, w.en("steps.layers.field_name")).fill(LAYER_NAME)
    ph.choose_source(page, "gee")
    ph.type_asset(page, LAYER_IMAGE)
    # three bands or more ask Bands: RGB composite (suggested for Sentinel-2's band names), true colour
    if ph.wait_bands_read(page, LAYER_IMAGE, select_label=w.en("steps.layers.field_bands"),
                          one_band_line=w.en("steps.layers.bands_one_hint").split("{")[0], timeout=180):
        ph.choose(page, w.en("steps.layers.field_bands"), w.en("steps.layers.bands_all"))
    page.wait_for_timeout(1500)
    ph.modal_field(page, w.en("steps.layers.field_min")).fill(LAYER_RANGE[0])
    ph.modal_field(page, w.en("steps.layers.field_max")).fill(LAYER_RANGE[1])
    switch_on(page, w.en("steps.layers.field_show_in_analysis"))
    page.wait_for_timeout(800)
    w.shot(page, ctx["shots"], "layer_modal")
    ph.modal_button(page, w.en("steps.layers.add")).click()
    w.wait_for(lambda: not ph.dialog_open(page), 180, f"the layer validated (modal: {ph.modal_text(page)[:300]})")
    w.wait_for(lambda: (manifest(ctx["data"]) or {}).get("vis_layers"), 30, "the layer saved")
    rec["vis_layers"] = manifest(ctx["data"])["vis_layers"]
    w.shot(page, ctx["shots"], "layer")


def st_period(page, rec, ctx):
    w.goto_step(page, "period", 2000)
    ph.visible_btn(page, w.en("steps.period.new"), RIGHT).click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the period modal")
    for label, value in ((w.en("steps.period.start"), PERIOD[0]), (w.en("steps.period.end"), PERIOD[1]),
                         (w.en("steps.period.look_back"), str(LOOK_BACK))):
        ph.modal_field(page, label).fill(value)
        page.wait_for_timeout(500)
    page.wait_for_timeout(800)
    t0 = w.now_ms(page)
    ph.primary_button(page).click()
    ph.wait_toast(page, t0, w.en("steps.period.created"))
    w.wait_for(lambda: not ph.dialog_open(page), 15, "the period modal closed")
    rec["periods"] = manifest(ctx["data"])["periods"]
    w.shot(page, ctx["shots"], "period")


def st_grid(page, rec, ctx):
    ph.save_grid(page, resolution=GRID_RESOLUTION)
    w.wait_for(lambda: (manifest(ctx["data"]) or {}).get("grid"), 30, "the grid saved")
    rec["grid"] = manifest(ctx["data"])["grid"]
    w.shot(page, ctx["shots"], "grid")


def st_mask(page, rec, ctx):
    ph.add_mask(page, rec, name=MASK_NAME, origin="gee", ref=MASK_ASSET, band=MASK_BAND, mode="Keep",
                out=ctx["shots"])
    rec["masks"] = manifest(ctx["data"])["masks"]


def prepared(data: Path) -> bool:
    m = manifest(data) or {}
    pid = m.get("active_period")
    arts = m.get("artifacts") or {}
    return bool(pid) and all(f"harm:{pid}:{s}" in arts for s in SOURCES)


def st_prepare(page, rec, ctx):
    ph.run_all(page, rec, out=ctx["shots"], expect_ready=len(SOURCES) + 1, timeout=STAGE_TIMEOUTS["prepare"])
    m = manifest(ctx["data"])
    rec["harmonized"] = {k: {"alert_pixels": a.get("stats", {}).get("alert_pixels"), "mb": round(
        (a.get("size_bytes") or 0) / 1e6, 1)} for k, a in m["artifacts"].items() if k.startswith("harm:")}


def st_integrate(page, rec, ctx):
    w.goto_step(page, "integrate", 2000)
    page.locator(f'{RIGHT} .v-text-field:has(label:text-is("{w.en("steps.integrate.min_size")}")) input').first.fill(
        MIN_SIZE_HA)
    limit = f'{RIGHT} .v-input--checkbox:has(label:text-is("{w.en("steps.integrate.limit_alerts")}"))'
    if not page.locator(f"{limit} input").first.is_checked():
        page.locator(f"{limit} .v-input--selection-controls__ripple").first.click()
        page.wait_for_timeout(800)
    page.locator(f'{RIGHT} .v-text-field:has(label:text-is("{w.en("steps.integrate.max_alerts")}")) input').first.fill(
        MAX_ALERTS)
    page.wait_for_timeout(1500)
    w.shot(page, ctx["shots"], "integrate_form")
    t0 = w.now_ms(page)
    ph.visible_btn(page, w.en("steps.integrate.run"), RIGHT).click()
    w.wait_for(lambda: active_set(ctx["data"]) or ph.errors(w.toasts_since(page, t0)),
               STAGE_TIMEOUTS["integrate"], "the alert set", poll=5)
    page.wait_for_timeout(5000)
    rec["toasts"] = [f"{x['kind']}: {x['msg']}" for x in w.toasts_since(page, t0)]
    assert active_set(ctx["data"]), rec["toasts"]
    s = active_set(ctx["data"])
    rec["set"] = {k: s.get(k) for k in ("id", "period_id", "sources", "params", "n_alerts", "stats")}
    rec["alerts"] = len(store(ctx["data"]))
    w.shot(page, ctx["shots"], "integrated")


def open_analysis(page):
    """``walklib.goto_step(page, "analysis")``, tried twice: right after another
    step's change the panel may still be switching steps, and the Start analysis
    button it found is hidden before the click lands."""
    try:
        w.goto_step(page, "analysis", 4000)
    except (PlaywrightTimeoutError, AssertionError):
        page.wait_for_timeout(3000)
        w.goto_step(page, "analysis", 4000)


def _review_positions(page, rec, ctx, plan):
    open_analysis(page)
    done = reviewed_mask(store(ctx["data"])).tolist()
    for position, verdict, driver in plan:
        if done[position]:
            continue
        ph.analysis_go_to(page, position)
        w.wait_for(lambda: (counter(page) or (0,))[0] == position + 1, 90, f"alert number {position + 1}")
        review_current(page, verdict, driver, rec)
    w.back_to_workflow(page)


def st_reviews_before(page, rec, ctx):
    _review_positions(page, rec, ctx, REVIEWS_BEFORE_SPLIT)


def st_split(page, rec, ctx):
    w.goto_step(page, "evaluators", 2000)
    multi = f"{RIGHT} .defo-mode-multi"
    if not page.locator(f"{multi} input").first.is_checked():  # a new project has no review mode yet
        page.locator(f"{multi} .v-input--selection-controls__ripple").first.click()
        page.wait_for_timeout(600)
    page.locator(f"{RIGHT} button.defo-split-open:visible").first.click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the split modal")
    page.locator(f'{w.MODAL} .v-radio:has(label:text-is("{w.en("evaluators.mode_names")}"))').first.click()
    page.wait_for_timeout(600)
    rows = page.locator(f"{w.MODAL} .defo-name-row")
    for i, name in enumerate(EVALUATORS):
        rows.nth(i).locator("input").fill(name)
        page.wait_for_timeout(400)
    w.shot(page, ctx["shots"], "split_modal")
    t0 = w.now_ms(page)
    ph.modal_button(page, w.en("evaluators.split")).click()
    ph.wait_toast(page, t0, f"split between {len(EVALUATORS)}", kind="success")
    page.wait_for_timeout(1500)
    rec["split"] = active_set(ctx["data"]).get("split")
    rec["assigned"] = store(ctx["data"])["evaluator"].fillna("").value_counts().to_dict()
    w.shot(page, ctx["shots"], "split")


def st_reviews_after(page, rec, ctx):
    _review_positions(page, rec, ctx, REVIEWS_AFTER_SPLIT)


def st_packages(page, rec, ctx):
    data = ctx["data"]
    zips = sorted(set_folder(data).glob("*__review.zip"))
    if len(zips) < len(EVALUATORS):
        w.goto_step(page, "aoi", 3000)  # an Admin AOI's geometry: the packages carry it
        w.goto_step(page, "evaluators", 2000)
        btn = page.locator(f"{RIGHT} button.defo-generate:visible").first
        w.wait_for(lambda: btn.count() and btn.is_enabled(), 300, "Generate packages enabled")
        t0 = w.now_ms(page)
        btn.click()
        ph.wait_toast(page, t0, f"{len(EVALUATORS)} packages ready", kind="success", timeout=900)
        page.wait_for_timeout(1500)
        zips = sorted(set_folder(data).glob("*__review.zip"))
    rec["packages"] = [z.name for z in zips]
    first = next(z for z in zips if "__e01-" in z.name)
    fixtures(data).mkdir(parents=True, exist_ok=True)
    shutil.copy2(first, fixtures(data) / first.name)
    rec["fixture"] = first.name
    w.shot(page, ctx["shots"], "packages")


def new_copy_reviews(data: Path, copy: str) -> int:
    """Reviews made in the review copy: its reviewed alerts beyond the ones
    the demo had reviewed already (the package carries those)."""
    mine, demo = store(data, copy), store(data)
    base = int(reviewed_mask(demo[demo["alert_id"].isin(set(mine["alert_id"]))]).sum())
    return reviewed_count(data, copy) - base


def st_return(page, rec, ctx):
    data = ctx["data"]
    copies = review_copies(data)
    if copies:
        copy = copies[0]
        w.open_project(page, copy)
        open_analysis(page)
    else:
        open_project_dialog(page)
        page.locator(f"{w.RAIL_DIALOG} button:has(.mdi-import):visible").first.click()
        w.wait_for(lambda: ph.dialog_open(page), 15, "the import form")
        ph.pick_file(page, w.en("steps.project.import_pick"), package_fixture(data), scope=w.MODAL)
        w.wait_for(lambda: review_copies(data), 300, "the review copy on disk")
        (copy,) = review_copies(data)
        w.wait_for(lambda: w.title(page).endswith(f"· {copy}"), 120, f"title {copy}")
        w.wait_for(lambda: page.locator(w.SCREEN_ON).count(), 120, "the analysis screen")
    rec["copy"] = copy
    w.wait_for(lambda: counter(page), 180, "the queue counter")
    nxt = page.locator(f"{w.PANEL} button:has(.mdi-chevron-right):visible", has_text=w.en("analysis_tile.nav.next")).first
    for i, (verdict, driver) in enumerate(COPY_REVIEWS):
        if i:
            before = counter(page)
            nxt.click()
            w.wait_for(lambda: counter(page) and counter(page)[0] == before[0] + 1, 90, "the next alert")
        if new_copy_reviews(data, copy) >= len(COPY_REVIEWS):
            break
        review_current(page, verdict, driver, rec)
    rec["copy_reviewed"] = reviewed_count(data, copy)
    rec["copy_new_reviews"] = new_copy_reviews(data, copy)
    w.goto_step(page, "evaluators", 2000)
    page.locator(f"{RIGHT} button.defo-generate-return:visible").first.click()
    link = page.locator(f"{RIGHT} a.defo-download-action:visible").first
    w.wait_for(lambda: link.count(), 300, "the reviewed copy's link")
    w.shot(page, ctx["shots"], "return")
    (returned,) = sorted(set_folder(data, copy).glob("*__reviewed.zip"))
    shutil.copy2(returned, fixtures(data) / returned.name)
    rec["fixture"] = returned.name
    w.open_project(page, PROJECT)


STAGES = (
    ("project", lambda d: manifest(d) is not None, st_project),
    ("aoi", lambda d: bool((manifest(d) or {}).get("aoi")), st_aoi),
    ("sources", lambda d: set(SOURCES) <= set((manifest(d) or {}).get("sources") or {}), st_sources),
    ("layer", lambda d: bool((manifest(d) or {}).get("vis_layers")), st_layer),
    ("period", lambda d: bool((manifest(d) or {}).get("periods")), st_period),
    ("grid", lambda d: bool((manifest(d) or {}).get("grid")), st_grid),
    ("mask", lambda d: bool((manifest(d) or {}).get("masks")), st_mask),
    ("prepare", prepared, st_prepare),
    ("integrate", lambda d: active_set(d) is not None, st_integrate),
    ("reviews_before_split", lambda d: bool((active_set(d) or {}).get("split"))
     or reviewed_count(d) >= len(REVIEWS_BEFORE_SPLIT), st_reviews_before),
    ("split", lambda d: bool((active_set(d) or {}).get("split")), st_split),
    ("reviews_after_split", lambda d: reviewed_count(d) >= len(REVIEWS_BEFORE_SPLIT) + len(REVIEWS_AFTER_SPLIT),
     st_reviews_after),
    ("packages", lambda d: package_fixture(d) is not None, st_packages),
    ("return", lambda d: return_fixture(d) is not None, st_return),
)


def park_review_copies(data: Path) -> list:
    """Move the evaluator's review copy out of the data dir into the fixtures:
    the demo's Manage projects lists the demo alone."""
    moved = []
    for name in review_copies(data):
        dest = fixtures(data) / "review_copy" / name
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(data / name), str(dest))
        moved.append(str(dest))
    return moved


def summary(data: Path) -> dict:
    m = manifest(data) or {}
    out = {"aoi": (m.get("aoi") or {}).get("name"), "sources": list(m.get("sources") or {}),
           "periods": list(m.get("periods") or {}), "look_back_days": LOOK_BACK,
           "vis_layers": list(m.get("vis_layers") or {}), "masks": list(m.get("masks") or {}), "grid": m.get("grid")}
    s = active_set(data)
    if s:
        frame = store(data)
        out["set"] = s.get("id")
        out["alerts"] = len(frame)
        out["reviewed"] = int(reviewed_mask(frame).sum())
        if "evaluator" in frame:
            # an unassigned alert holds "" (None in older stores)
            out["assigned"] = frame["evaluator"].fillna("").replace("", "(unassigned)").value_counts().to_dict()
    out["fixtures"] = sorted(str(p.relative_to(data)) for p in fixtures(data).glob("*.zip")) \
        if fixtures(data).is_dir() else []
    return out


def server_env(args) -> None:
    """Earth Engine settings for the server this builder starts: they go in
    this process's environment, which ``walklib.start_server`` copies, and
    reach the server's processes through ``server_hooks/sitecustomize.py``."""
    raw_cache = getattr(args, "raw_cache", None)
    if not (args.ee_project or args.ee_max_requests or raw_cache):
        return
    hooks = Path(__file__).resolve().parent / "server_hooks"
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(hooks), os.environ.get("PYTHONPATH")]))
    if args.ee_project:
        os.environ["DEFO_DOCS_EE_PROJECT"] = args.ee_project
        # eeclient's sessions read EARTHENGINE_TOKEN before the credentials file: the same
        # credentials, naming the chosen project (the file itself is left as it is)
        creds = Path.home() / ".config" / "earthengine" / "credentials"
        if creds.is_file():
            token = json.loads(creds.read_text(encoding="utf-8"))
            token["project"] = args.ee_project
            os.environ["EARTHENGINE_TOKEN"] = json.dumps(token)
        w.log(f"Earth Engine project: {args.ee_project}")
    if args.ee_max_requests:
        os.environ["DEFO_DOCS_EE_MAX_REQUESTS"] = str(args.ee_max_requests)
        w.log(f"Earth Engine tile requests capped at {args.ee_max_requests}")
    if raw_cache:
        os.environ["DEFO_DOCS_RAW_CACHE"] = str(Path(raw_cache).expanduser().resolve())
        w.log(f"raw downloads cached in {os.environ['DEFO_DOCS_RAW_CACHE']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, required=True,
                    help="the app's data directory (DEFORESTATION_ALERTS_DATA_DIR); keep it under $HOME")
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--ee-max-requests", type=int, metavar="N",
                    help="cap the app's concurrent Earth Engine tile requests at N and retry 'Too Many Requests' "
                         "(for an Earth Engine project in Restricted Mode; see tools/docs/server_hooks)")
    ap.add_argument("--ee-project", metavar="NAME",
                    help="the Earth Engine (Cloud) project for the app server this builder starts, instead of the "
                         "one in ~/.config/earthengine/credentials; that file is not changed")
    args = ap.parse_args(argv)
    data = args.data_dir.expanduser().resolve()
    build = data / "_build"
    shots = build / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "stages": {}}
    report_path = build / "report.json"

    def save_report():
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")

    todo = [(name, run) for name, done, run in STAGES if not done(data)]
    for name, done, _ in STAGES:
        if not done(data):
            break
        report["stages"][name] = {"skipped": True}
    ok = True
    if todo:
        w.log(f"stages to run: {', '.join(n for n, _ in todo)}")
        server_env(args)
        proc = w.start_server(args.port, data, build / "server.log")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=not args.headed)
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                page.add_init_script(w.TOAST_SPY)
                ctx = {"data": data, "shots": shots}
                w.open_app(page, args.port)
                if manifest(data) is not None:
                    w.open_project(page, PROJECT)
                for name, done, run in STAGES:
                    if done(data):
                        report["stages"].setdefault(name, {"skipped": True})
                        continue
                    w.log(f"=== {name} ===")
                    rec, t0 = {}, time.time()
                    try:
                        run(page, rec, ctx)
                        rec["ok"] = done(data)
                        if not rec["ok"]:
                            rec["error"] = "the stage ran but its output is not on disk"
                    except Exception as e:  # noqa: BLE001
                        rec.update(ok=False, error=f"{e.__class__.__name__}: {e}", trace=traceback.format_exc()[-2000:])
                    rec["seconds"] = round(time.time() - t0)
                    report["stages"][name] = rec
                    save_report()
                    w.log(f"{name}: {'ok' if rec['ok'] else 'FAILED: ' + rec['error']} ({rec['seconds']} s)")
                    if not rec["ok"]:
                        w.shot(page, shots, f"failed_{name}")
                        ok = False
                        break
                browser.close()
        except Exception:  # noqa: BLE001
            report["error"] = traceback.format_exc()[-2000:]
            traceback.print_exc()
            ok = False
        finally:
            w.stop_server(proc)
    if ok:
        report["parked_review_copies"] = park_review_copies(data)
    report["summary"] = summary(data)
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_report()
    w.log(json.dumps(report["summary"], indent=1, default=str))
    w.log(f"demo {'READY' if ok else 'INCOMPLETE (run again to resume)'}; report: {report_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
