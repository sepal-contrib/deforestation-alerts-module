"""Slice 4 gate walk (spec §14 row 4): (a) mask switches make the period's rows
and set out of date and Run all re-harmonizes with no source download, (b) an
Earth Engine and a float GeoTIFF mask remove alerts, down to "all removed by
masks", (c) a keep mask covering none of the AOI warns, (d) the legends, (e)
visualization layers on the shared map and in Analysis. (f) is Step 1's tests.

    python -m tools.walk.slice4_walk --port 8984 --out /tmp/slice4_walk
"""

import argparse
import glob
import os
import re
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.walk import make_rasters
from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.seed_project import seed

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_slice4"
USER = DATA / "user"
PROJECT = "s4"
AMAZON = (-55.10, -10.10, -55.00, -10.00)
HANSEN = "UMD/hansen/global_forest_change_2023_v1_11"
WINDOW = "2025-06-01 → 2026-08-31"
SOURCES_STALE = "This period's sources are out of date"
SET_STALE = "built from inputs that have changed since"
STATUSES = ("Not reviewed", "Reviewed: confirmed", "Reviewed: false positive", "Reviewed: needs revision")


def s2_image(bbox) -> str:
    import ee
    from pysepal.scripts.utils import init_ee

    init_ee()
    box = ee.Geometry.Rectangle(list(bbox))
    # Task 21's choice (slice3_walk.s2_image): an image whose footprint holds the
    # whole box, the least cloudy, not the first tile edge filterBounds returns
    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(box)
            .filterDate("2025-06-01", "2025-09-30").filter(ee.Filter.contains(leftField=".geo", rightValue=box))
            .sort("CLOUDY_PIXEL_PERCENTAGE").first().get("system:id").getInfo())


def raw_files() -> dict:
    return {f: os.stat(f).st_mtime_ns for f in glob.glob(str(DATA / PROJECT / "periods" / "*" / "raw" / "*"))}


def harm_stats(source="GLAD-L") -> dict:
    m = ph.manifest(DATA, PROJECT)
    art = next(a for k, a in m["artifacts"].items() if k.startswith("harm:") and k.endswith(f":{source}"))
    return art.get("stats") or {}


def setup(page, rec, *, out):
    ph.save_grid(page)
    ph.add_mask(page, rec, name="hansen", origin="gee", ref=HANSEN, band="treecover2000", mode="Keep", out=out)
    ph.run_all(page, rec, out=out, expect_ready=2)  # the mask, then GLAD-L: one Ready cell each
    ph.integrate_and_land(page, rec, out=out)
    w.goto_step(page, "integrate", 2000)
    assert SET_STALE not in w.panel_text(page), w.panel_text(page)[:600]


def a_switch(page, rec, *, out):
    before = raw_files()
    rec["raw_files"] = sorted(before)
    assert before, "no raw source file recorded"
    # One Mode select per mask row: Exclude, then Off, then on again (Exclude).
    for label, act in (("exclude", lambda: ph.row_mode(page, "hansen", "Exclude")),
                       ("off", lambda: ph.row_mode(page, "hansen", ph.MODE_OFF)),
                       ("on", lambda: ph.row_mode(page, "hansen", "Exclude"))):
        w.goto_step(page, "prepare", 2000)
        t0 = w.now_ms(page)
        act()
        ph.wait_toast(page, t0, SOURCES_STALE)
        # out of date = a Harmonize pill (its raw file is current: no download)
        w.wait_for(lambda: ph.row_pill(page, "GLAD-L", "harmonize").count(), 30, f"{label}: GLAD-L out of date")
        rec[f"{label}_row"] = ph.row_text(page, "GLAD-L")
        # With its only source out of date, Integrate is locked (spec §6.1: it
        # needs a source whose files are current), so its set line cannot be
        # read here; the Period step's row shows the same set_status per period.
        w.goto_step(page, "integrate", 2000)
        rec[f"{label}_integrate_locked"] = "Integrate & vectorize" not in w.panel_text(page)[:200]
        w.goto_step(page, "period", 2000)
        rec[f"{label}_period"] = ph.row_text(page, "2025-06-01")
        assert SET_STALE in rec[f"{label}_period"], f"{label}: the set does not read out of date before a Run"
        ph.run_all(page, rec, out=out, expect_ready=2, shot_name=f"switch_{label}")
        assert not ph.row_pill(page, "GLAD-L", "harmonize").count(), ph.row_text(page, "GLAD-L")
    assert raw_files() == before, "a source was downloaded again"


def b_masks_remove_alerts(page, rec, *, out):
    rec["hansen_only"] = harm_stats()  # hansen on, exclude (the end of a_switch)
    # the Earth Engine mask removes alerts by itself (over this box it leaves
    # one of ~300, east of the middle, so the probability mask removes that one)
    assert rec["hansen_only"]["alert_pixels"] < rec["hansen_only"]["alert_pixels_unmasked"], rec["hansen_only"]
    ph.add_mask(page, rec, name="probability", origin="file", ref=USER / "probability.tif", band="probability",
                mode="Keep", out=out)
    ph.run_all(page, rec, out=out, expect_ready=3, shot_name="two_masks")  # hansen, probability, then GLAD-L
    rec["both"] = harm_stats()
    assert rec["both"]["alert_pixels"] < rec["hansen_only"]["alert_pixels"], (rec["hansen_only"], rec["both"])
    make_rasters.probability_tif(USER / "probability.tif", AMAZON, everywhere=True)  # the file changes
    ph.row_mode(page, "probability", "Exclude")
    ph.run_all(page, rec, out=out, expect_ready=3, shot_name="all_masked")  # probability realigned, then GLAD-L
    rec["all_removed"] = ph.row_text(page, "GLAD-L")
    assert re.search(r"ready · all [\d,]+ alert pixels removed by masks", rec["all_removed"]), rec["all_removed"]


def c_covers_none(page, rec, *, out):
    ph.add_mask(page, rec, name="far", origin="file", ref=USER / "far.tif", band="far", mode="Keep", out=out)
    t0 = w.now_ms(page)
    ph.row_pill(page, "far", "align").click()  # a GeoTIFF mask is only aligned
    ph.wait_toast(page, t0, "covers none of the AOI", timeout=300, kind="warning")
    w.wait_for(lambda: "covers none of the AOI: every alert would be removed" in ph.row_text(page, "far"), 60,
               "the far row's warning line")
    rec["far_row"] = ph.row_text(page, "far")
    for name in ("far", "probability"):  # give the next phases their alerts back
        ph.row_menu_pick(page, name, "steps.prepare.mask_delete_action")  # the row's ⋮ menu
        w.wait_for(lambda: ph.dialog_open(page), 15, f"the {name} delete confirmation")
        ph.modal_button(page, "Delete").click()
        w.wait_for(lambda: not ph.row_text(page, name), 30, f"{name} deleted")
    ph.run_all(page, rec, out=out, expect_ready=2, shot_name="hansen_only")


def add_predefined(page, key):
    w.goto_step(page, "alerts", 2000)
    ph.visible_btn(page, "Add source", ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the source modal")
    ph.choose(page, "Predefined source", key)
    button = ph.modal_button(page, "Add source")
    w.wait_for(lambda: button.count() and button.is_enabled(), 120, f"{key}'s coverage checked")
    button.click()
    w.wait_for(lambda: not ph.dialog_open(page), 30, f"{key} added")


def d_legends(page, rec, *, out):
    ph.row_button(page, "GLAD-L", "mdi-map-plus").click()
    w.wait_for(lambda: "GLAD-L" in ph.legend_text(page) and WINDOW in ph.legend_text(page), 120, "GLAD-L's legend")
    add_predefined(page, "RADD")
    w.goto_step(page, "alerts", 2000)
    ph.row_button(page, "RADD", "mdi-map-plus").click()
    w.wait_for(lambda: "RADD · preview" in ph.legend_text(page), 180, "RADD's preview legend")
    rec["preview_legend"] = ph.legend_text(page)
    assert "Confirmed (high confidence)" in rec["preview_legend"] and "Probable (low confidence)" in rec["preview_legend"]
    rec["preview_options"] = ph.legend_options(page)
    assert [o["text"] for o in rec["preview_options"] if o["selected"]] == ["RADD · preview"], rec["preview_options"]
    w.goto_step(page, "prepare", 2000)
    ph.row_pill(page, "RADD", "download").click()
    w.wait_for(lambda: "RADD" in ph.legend_text(page) and WINDOW in ph.legend_text(page)
               and "preview" not in ph.legend_text(page), 1200, "RADD's harmonized legend", poll=3)
    assert not any("preview" in o["text"] for o in ph.legend_options(page)), ph.legend_options(page)
    ph.row_button(page, "hansen", "mdi-map-plus").click()
    w.wait_for(lambda: any(o["selected"] and o["text"] == "Mask hansen" for o in ph.legend_options(page)), 120,
               "the mask's legend selected")
    for mode, text in (("Keep", "Kept area"), ("Exclude", "Excluded area")):
        ph.row_mode(page, "hansen", mode)
        w.wait_for(lambda: text in ph.legend_text(page), 30, f"the mask legend reads {text}")
    ph.row_mode(page, "hansen", ph.MODE_OFF)  # off in this period
    w.wait_for(lambda: "Not used in this period" in ph.legend_text(page), 30, "the mask legend reads off")
    ph.row_button(page, "hansen", "mdi-map-minus").click()
    w.wait_for(lambda: "Mask hansen" not in [o["text"] for o in ph.legend_options(page)]
               and "Mask hansen" not in ph.legend_text(page), 30, "the mask's legend gone with its layer")
    rec["after_hide"] = ph.legend_options(page) or ph.legend_text(page)
    w.goto_step(page, "analysis", 4000)
    assert ph.legend_text(page) == "", ph.legend_text(page)  # hidden while Analysis shows
    w.goto_step(page, "prepare", 2000)
    w.wait_for(lambda: ph.legend_text(page), 15, "the legend back")
    # The set's own layer keeps its legend across a step change. With hansen
    # off, both sources are out of date and Integrate is locked, so
    # show_alerts uses the Overview's copy of the switch.
    ph.show_alerts(page)
    w.wait_for(lambda: all(s in ph.legend_text(page) for s in STATUSES), 120, "the set's four status chips")
    rec["set_legend"] = ph.legend_text(page)
    w.goto_step(page, "prepare", 2000)
    assert all(s in ph.legend_text(page) for s in STATUSES), ph.legend_text(page)
    w.shot(page, out, "legends")


def _modal_check(page, label):
    ph.modal(page).locator(f'.v-input:has(label:text-is("{label}")) .v-input--selection-controls__input').first.click()
    page.wait_for_timeout(400)


def add_layer(page, rec, *, kind, name, fill, out) -> bool:
    """Optional layers → Add layer; False when the modal refuses it."""
    w.goto_step(page, "layers", 2000)
    ph.visible_btn(page, "Add layer", ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the layer modal")
    ph.choose(page, "Kind", kind)
    ph.modal_field(page, "Name").fill(name)
    fill()
    _modal_check(page, "Show in Analysis")
    w.shot(page, out, f"layer_modal_{name}")
    ph.primary_button(page).click()  # "Add layer": the form's Enter default
    try:
        w.wait_for(lambda: not ph.dialog_open(page), 180, f"{name} saved")
    except AssertionError:  # refused: the reason stays in the open form
        rec[f"{name}_refused"] = ph.modal_text(page)[:300]
        ph.modal_button(page, "Cancel").click()
        return False
    return True


def analysis_layer_controls(page) -> list:
    """Each Analysis view's layer menu (``leaflet-multiview``'s ``layerMenu``,
    one per view), as ``{layer name: ticked}``. The menu's list is in the DOM
    while it is collapsed and its ticks follow the map, so nothing is hovered;
    ``textContent`` because the collapsed list has no ``innerText``."""
    return page.evaluate("""() => [...document.querySelectorAll('.analysis-views .mv-view')]
        .filter(v => v.offsetParent)
        .map(v => Object.fromEntries([...v.querySelectorAll(
            '.mv-layer-menu .leaflet-control-layers-overlays label')]
            .map(l => [l.querySelector('input + span').textContent.trim(), l.querySelector('input').checked])))""")


def e_vis_layers(page, rec, *, out, s2):
    def gee():
        ph.type_asset(page, s2)
        w.wait_for(lambda: "Reading the bands" not in ph.modal_text(page), 120, "the image's bands")
        ph.modal(page).locator('.v-input--switch:has(label:has-text("Three bands (RGB)")) '
                               '.v-input--selection-controls__input').first.click()
        for label, band in (("Red band", "B4"), ("Green band", "B3"), ("Blue band", "B2")):
            ph.choose(page, label, band)
        ph.modal_field(page, "Min").fill("0")
        ph.modal_field(page, "Max").fill("3000")

    def tif():
        ph.pick_file(page, "GeoTIFF", USER / "slope.tif", scope=ph.MODAL)
        w.wait_for(lambda: "Reading the bands" not in ph.modal_text(page), 60, "the file's bands")
        ph.choose(page, "Band", "slope")
        ph.choose(page, "Colormap", "viridis")
        ph.modal_field(page, "Min").fill("0")
        ph.modal_field(page, "Max").fill("45")

    assert add_layer(page, rec, kind="Earth Engine", name="s2", fill=gee, out=out)
    ph.row_button(page, "s2", "mdi-map-plus").click()
    w.wait_for(lambda: "RGB composite of B4, B3, B2, 0–3000" in ph.legend_text(page), 180, "the s2 legend")
    assert add_layer(page, rec, kind="GeoTIFF", name="slope", fill=tif, out=out)
    ph.row_button(page, "slope", "mdi-map-plus").click()
    w.wait_for(lambda: "slope" in ph.legend_text(page) and "45" in ph.legend_text(page), 180, "the slope legend")
    w.goto_step(page, "analysis", 6000)
    wanted = ["slope"]

    def listed():
        lists = analysis_layer_controls(page)
        return lists if len(lists) == 2 and all(all(n in m for n in wanted) for m in lists) else None

    lists = w.wait_for(listed, 180, "the layer menu of both Analysis views listing the layers", poll=3)
    rec["analysis_layer_controls"] = lists
    assert all(not m[n] for m in lists for n in wanted), lists  # added switched off
    assert ph.legend_text(page) == "", ph.legend_text(page)  # no legend on the Analysis maps (D20)
    w.shot(page, out, "analysis_vis_layers")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8984)
    ap.add_argument("--out", type=Path, default=Path("/tmp/slice4_walk"))
    args = ap.parse_args(argv)
    # The walk's own data dir, rebuilt: files a previous run left (a set's
    # downloads) would hide what this run writes.
    shutil.rmtree(DATA, ignore_errors=True)
    USER.mkdir(parents=True, exist_ok=True)
    make_rasters.probability_tif(USER / "probability.tif", AMAZON)
    make_rasters.far_mask_tif(USER / "far.tif")
    make_rasters.slope_tif(USER / "slope.tif", AMAZON)
    s2 = s2_image(AMAZON)
    seed(PROJECT, AMAZON, sources=("GLAD-L",), period=("2025-06-01", "2026-08-31"), look_back=0, root=DATA)
    args.out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, DATA, args.out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            out = args.out
            ok = ph.run_phases(page, [
                ("open", lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT))),
                ("setup", lambda pg, r: setup(pg, r, out=out)),
                ("a_switch", lambda pg, r: a_switch(pg, r, out=out)),
                ("b_masks_remove_alerts", lambda pg, r: b_masks_remove_alerts(pg, r, out=out)),
                ("c_covers_none", lambda pg, r: c_covers_none(pg, r, out=out)),
                ("d_legends", lambda pg, r: d_legends(pg, r, out=out)),
                ("e_vis_layers", lambda pg, r: e_vis_layers(pg, r, out=out, s2=s2)),
            ], report, args.out / "report.json")
            report["page_errors_count"] = len(report.get("page_errors", []))
            browser.close()
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", args.out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
