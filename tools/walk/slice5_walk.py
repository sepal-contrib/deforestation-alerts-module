"""Slice 5 gate walk (spec §14 row 5): two monthly periods whose mask choices
differ, no pixel reported twice, the period rules, the set selector, earlier
sets with Go, the Replace list and deleting the latest period.

    python -m tools.walk.slice5_walk --port 8985 --out /tmp/slice5_walk
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from playwright.sync_api import sync_playwright

from tools.walk import make_rasters
from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.seed_project import seed

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_slice5"
USER = DATA / "user"  # the walk's "SEPAL folder" files: under $HOME, like a user's
PROJECT = "s5"
AMAZON = (-55.10, -10.10, -55.00, -10.00)
# The brief's months (June / July 2026) and mask (Hansen treecover2000) cannot
# hold here: GLAD-L has 20 alert pixels in the box from 2026-05-02 to
# 2026-06-30, none in a cluster of 0.5 ha (June's set is empty), and every
# alert of the box lies in tree cover 2000 (and in Hansen's lossyear, and in
# JRC GFC2020), so Exclude empties the second month's set. July / August 2025
# have the most clustered alerts, and a mask of the box's south half (a
# GeoTIFF, lat < -10.05) splits them: kept, July has 15 clustered pixels
# (checked on Earth Engine, 2026-10-02). August switches the mask off, so its
# window (from 2 July, look-back 30 days) holds July's alerts again: the
# footprint of July's set is what keeps them out of August's set.
CUT = -10.05
JUNE, JULY = "2025-07-01_2025-07-31", "2025-08-01_2025-08-31"  # the first and the second month
JUNE_ROW, JULY_ROW = "2025-07-01 → 2025-07-31", "2025-08-01 → 2025-08-31"
JUNE_START, JULY_START = "2025-07-01", "2025-08-01"
WINDOW = "Search window: 2025-06-01 → 2025-07-31"
JULY_FIRST_DAY = 2025.213  # 1 August 2025 as ``yyyy.ddd``
SET_STALE = "built from inputs that have changed since"
SEEN = {}  # what a phase found that a later phase needs (the set ids)


def south_mask_tif(path, bbox, cut=CUT) -> Path:
    """One uint8 band ``south``: 1 south of latitude ``cut``, 0 north of it."""
    profile, (h, _) = make_rasters._profile(bbox, 1, "uint8")
    band = np.zeros((h, profile["width"]), "uint8")
    band[int(round((bbox[3] - cut) / make_rasters.RES)):, :] = 1
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)
        dst.descriptions = ("south",)
    return Path(path)


def sets_of(period_id) -> list:
    return [s for s in ph.manifest(DATA, PROJECT)["alert_sets"].values() if s["period_id"] == period_id]


def legend_chips(page, value) -> list:
    """Show the legend ``value`` of the dropdown and read its colour chips as (r, g, b)."""
    page.locator(".sepal-legend select:visible").first.select_option(value)
    page.wait_for_timeout(800)
    colours = page.evaluate("""() => { const l = [...document.querySelectorAll('.sepal-legend')]
        .find(e => e.getBoundingClientRect().width > 0);
        return l ? [...l.querySelectorAll('.sepal-legend__chip:not(.sepal-legend__chip--empty)')]
            .map(e => getComputedStyle(e).backgroundColor) : []; }""")
    return [tuple(int(v) for v in re.findall(r"\d+", c)[:3]) for c in colours]


def a_first_month(page, rec, *, out):
    ph.save_grid(page)
    ph.add_mask(page, rec, name="south", origin="file", ref=USER / "south.tif", band="south", mode="Keep", out=out)
    ph.run_all(page, rec, out=out, expect_ready=3)  # the mask (aligned), then GLAD-L
    ph.integrate_and_land(page, rec, out=out)
    (june,) = sets_of(JUNE)
    rec["june_set"], rec["june_alerts"], rec["june_footprint"] = june["id"], june["n_alerts"], june["footprint_rel_path"]
    SEEN["june_set"] = june["id"]
    assert june["n_alerts"] > 0, "no alert in the first month here: pick another month for the walk"
    assert [(m["name"], m["mode"]) for m in june["masks_used"]] == [("south", "keep")], june["masks_used"]


def b_second_period(page, rec, *, out):
    w.goto_step(page, "period", 2000)
    rec["june_row"] = ph.row_text(page, JUNE_ROW)
    for text in (WINDOW, "Its dates are fixed: it has alert sets"):
        assert text in rec["june_row"], rec["june_row"]
    assert re.search(rf"{JUNE_ROW}\s+1\b", rec["june_row"]), rec["june_row"]  # the Sets cell reads 1
    ph.visible_btn(page, "New period", ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the period modal")
    start, end = ph.modal_field(page, "Start"), ph.modal_field(page, "End")
    rec["prefill"] = [start.input_value(), end.input_value()]
    assert rec["prefill"] == [JULY_START, date.today().isoformat()], rec["prefill"]
    end.fill(JULY.split("_")[1])
    page.wait_for_timeout(800)
    t0 = w.now_ms(page)
    ph.modal_button(page, "Save").click()
    ph.wait_toast(page, t0, "Period created")
    w.wait_for(lambda: not ph.dialog_open(page), 15, "the period modal closed")
    panel = w.panel_text(page)
    assert panel.index(JULY_ROW) < panel.index(JUNE_ROW), "July is not listed first"
    assert ph.row_button(page, JULY_ROW, "mdi-radiobox-marked").count(), "July is not the selected period"
    p = ph.manifest(DATA, PROJECT)  # the new period copied the latest period's mask choices
    rec["july_masks_at_creation"] = p["periods"][JULY]["masks"]
    assert p["periods"][JULY]["masks"] == {"south": {"on": True, "mode": "keep"}}, p["periods"][JULY]["masks"]
    w.goto_step(page, "prepare", 2000)
    mask_row = ph.row(page, "south")
    rec["july_mask_row"] = ph.row_text(page, "south")
    assert mask_row.locator(".v-input--switch.v-input--is-label-active").count() and "Keep" in rec["july_mask_row"], \
        rec["july_mask_row"]
    t1 = w.now_ms(page)
    ph.row_switch(page, "south")  # off in August only
    ph.wait_toast(page, t1, "This period's sources are out of date")
    w.wait_for(lambda: not ph.manifest(DATA, PROJECT)["periods"][JULY]["masks"]["south"]["on"], 30,
               "the switch saved")
    p = ph.manifest(DATA, PROJECT)
    assert p["periods"][JUNE]["masks"] == {"south": {"on": True, "mode": "keep"}}, p["periods"][JUNE]["masks"]
    # the switch in August leaves July's set current: no stale banner on it, no
    # out-of-date badge on July's row
    ph.open_overview(page)  # June's set is still the active one
    rec["overview_after_switch"] = w.dialog_text(page)[:600]
    assert SET_STALE not in rec["overview_after_switch"], rec["overview_after_switch"]
    w.close_dialog(page)
    w.goto_step(page, "period", 2000)
    rec["june_row_after_switch"] = ph.row_text(page, JUNE_ROW)
    assert "out of date" not in rec["june_row_after_switch"], rec["june_row_after_switch"]
    ph.run_all(page, rec, out=out, expect_ready=2, shot_name="july_ready")  # GLAD-L's two cells
    ph.integrate_and_land(page, rec, out=out)
    p = ph.manifest(DATA, PROJECT)
    assert p["periods"][JULY]["masks"] == {"south": {"on": False, "mode": "keep"}}, p["periods"][JULY]["masks"]
    assert p["periods"][JUNE]["masks"] == {"south": {"on": True, "mode": "keep"}}, p["periods"][JUNE]["masks"]
    (july,) = sets_of(JULY)
    assert july["masks_used"] == [], july["masks_used"]
    rec["july_set"], rec["july_alerts"], rec["july_footprint_from"] = july["id"], july["n_alerts"], july["footprint_from"]
    SEEN["july_set"] = july["id"]
    assert SEEN["june_set"] in july["footprint_from"], july["footprint_from"]


def c_no_repeat(page, rec, *, out):
    (june,), (july,) = sets_of(JUNE), sets_of(JULY)
    with rasterio.open(DATA / PROJECT / june["footprint_rel_path"]) as a, \
            rasterio.open(DATA / PROJECT / july["footprint_rel_path"]) as b:
        fa, fb = a.read(1).astype(bool), b.read(1).astype(bool)
    rec["june_footprint_px"], rec["july_footprint_px"] = int(fa.sum()), int(fb.sum())
    # Precondition: August's harmonized GLAD-L (mask off, window from 2 July)
    # has alert pixels inside July's footprint, so a zero overlap below is the
    # footprint at work, not two disjoint inputs.
    p = ph.manifest(DATA, PROJECT)
    art = next(a for k, a in p["artifacts"].items() if k.startswith(f"harm:{JULY}:") and k.endswith(":GLAD-L"))
    with rasterio.open(DATA / PROJECT / art["rel_path"]) as r:
        harm = r.read(1) > 0
    assert harm.shape == fa.shape, (harm.shape, fa.shape)
    rec["august_input_in_july_footprint_px"] = int((harm & fa).sum())
    assert rec["august_input_in_july_footprint_px"] > 0, "August's input has no alert in July's footprint"
    rec["overlap"] = int((fa & fb).sum())
    assert rec["overlap"] == 0, f"{rec['overlap']} pixels reported by both months"
    # Informative only: August's alerts whose first date falls before 1 August
    # (in the look-back window). They are pixels July's set did not report
    # (the overlap is 0): mostly north of the cut, which July's mask kept out.
    alerts = gpd.read_parquet(DATA / PROJECT / july["store_rel_path"])
    early = alerts[alerts["alert_date_min"].astype(float) < JULY_FIRST_DAY]
    rec["august_alerts_dated_before_august"] = len(early)
    rec["of_which_north_of_cut"] = int((early.to_crs("EPSG:4326").geometry.representative_point().y > CUT).sum())


def d_rules(page, rec, *, out):
    folder = DATA / PROJECT / "periods" / JUNE
    before = sorted(str(f) for f in folder.rglob("*"))
    w.goto_step(page, "period", 2000)
    t0 = w.now_ms(page)
    ph.row_button(page, JUNE_ROW, "mdi-pencil-outline").click()
    ph.wait_toast(page, t0, "This period has alert sets; its dates can't change.", kind="warning")
    page.wait_for_timeout(1000)
    assert not ph.dialog_open(page), "the period modal opened"
    t1 = w.now_ms(page)
    ph.row_button(page, JUNE_ROW, "mdi-delete-outline").click()
    ph.wait_toast(page, t1, "This period has alert sets and a later period exists", kind="warning")
    page.wait_for_timeout(1000)
    assert not ph.dialog_open(page) and ph.row_text(page, JUNE_ROW)
    assert sorted(str(f) for f in folder.rglob("*")) == before, "June's folder changed"
    rec["june_files"] = len(before)


def e_sets(page, rec, *, out):
    ph.open_overview(page)
    items = [i["text"] for i in ph.menu_items(page, "Alert set", scope=".dialog-card")]
    rec["selector"] = items
    assert len(items) == 2 and items[0].startswith(JULY_START) and items[1].startswith(JUNE_START), items
    assert all(re.search(r"· [\d,]+ alerts · \d+ reviewed$", i) for i in items), items
    ph.choose(page, "Alert set", JULY_START, scope=".dialog-card")  # July's set active
    w.wait_for(lambda: ph.manifest(DATA, PROJECT)["active_set"] == SEEN["july_set"], 30, "July's set active")
    page.wait_for_timeout(3000)
    w.close_dialog(page)
    # Both alert layers are switched on in Integrate & vectorize now; they stay
    # on while the walk moves to Analysis and back (MapLayers ruling #10).
    ph.show_alerts(page)
    page.locator('.right-panel .v-input--switch:has(label:has-text("Show earlier sets")) '
                 '.v-input--selection-controls__input').first.click()
    w.wait_for(lambda: any(o["text"].startswith("Earlier set") and "GLAD-L" in o["text"]
                           for o in ph.legend_options(page)), 180, "the earlier set's legend")
    rec["legend_options"] = ph.legend_options(page)
    earlier = next(o for o in rec["legend_options"] if o["text"].startswith("Earlier set"))
    assert earlier["text"] == f"Earlier set · {JUNE} · GLAD-L", earlier
    active = next(o for o in rec["legend_options"] if o["text"].startswith("Alerts"))
    rec["active_chips"] = legend_chips(page, active["value"])
    rec["earlier_chips"] = legend_chips(page, earlier["value"])
    rec["earlier_legend"] = ph.legend_text(page)
    w.shot(page, out, "earlier_legend")
    assert len(rec["earlier_chips"]) == 4 == len(rec["active_chips"]), (rec["earlier_chips"], rec["active_chips"])
    assert all(sum(e) > sum(a) for e, a in zip(rec["earlier_chips"], rec["active_chips"])), "chips not lighter"
    (june,) = sets_of(JUNE)
    alerts = gpd.read_parquet(DATA / PROJECT / june["store_rel_path"])
    # The largest alert past position 0 (easy to hit; and June's saved review
    # position is 0, so landing on it proves the Go selected the alert).
    pos = int(alerts["area_ha"].iloc[1:].astype(float).values.argmax()) + 1 if len(alerts) > 1 else 0
    rec["go_position"], rec["go_alert_id"] = pos, int(alerts["alert_id"].iloc[pos])
    point = alerts.to_crs("EPSG:4326").geometry.iloc[pos].representative_point()
    _, x, y = w.fly_to(page, point.x, point.y, 16)
    w.wait_for(lambda: ph.legend_options(page), 60, "the legends after the zoom")
    page.wait_for_timeout(1500)
    _, x, y = w.lonlat_to_screen(page, point.x, point.y)
    page.mouse.click(x, y)
    w.wait_for(lambda: "Alert " in (page.locator(".leaflet-popup-content:visible").first.inner_text()
                                    if page.locator(".leaflet-popup-content:visible").count() else ""),
               30, "the alert popup's content")
    page.wait_for_timeout(500)
    rec["popup"] = page.locator(".leaflet-popup-content:visible").first.inner_text()
    w.shot(page, out, "earlier_popup")
    assert f"Earlier set · {JUNE} · GLAD-L" in rec["popup"], rec["popup"]
    assert f"Alert {rec['go_alert_id']} ·" in rec["popup"], rec["popup"]
    page.locator('.leaflet-popup-content button:has-text("Review")').first.click()
    w.wait_for(lambda: ph.alert_info(page), 120, "Analysis on June's alert")
    selected = page.locator('.v-select:has(label:text-is("Alert set")) .v-select__selection:visible').first
    w.wait_for(lambda: selected.inner_text().startswith(JUNE_START), 60, "Analysis's selector on June's set")
    rec["analysis_set"] = selected.inner_text()
    go = page.locator("button:visible", has_text=re.compile(r"^\s*Go\s*$")).first
    field = go.locator("xpath=..").locator("input").first
    # The navigator's id field holds the alert's position in the set's store
    # (``actual_alert_id``), not its ``alert_id``.
    w.wait_for(lambda: field.input_value() == str(pos), 60, f"the id field at position {pos}")
    rec["id_field"] = field.input_value()
    w.shot(page, out, "go_analysis")
    ph.open_overview(page)
    ph.choose(page, "Alert set", JULY_START, scope=".dialog-card")
    w.wait_for(lambda: ph.manifest(DATA, PROJECT)["active_set"] == SEEN["july_set"], 30, "July's set active")
    w.close_dialog(page)


def f_replace_list(page, rec, *, out):
    w.goto_step(page, "analysis", 4000)
    w.wait_for(lambda: page.locator('.v-select:has(label:text-is("Alert set")) .v-select__selection:visible')
               .first.inner_text().startswith(JULY_START), 60, "Analysis on July's set")
    ph.pick_images(page, rec, out=out)
    # "Is this a true alert?" is a select whose default is "Yes" (Confirmed)
    ph.review_and_save(page, rec, out=out)
    (july,) = sets_of(JULY)
    created = july["created_at"]
    assert july["n_reviewed"] == 1, july["n_reviewed"]
    w.goto_step(page, "integrate", 2000)
    t0 = w.now_ms(page)
    ph.visible_btn(page, "Integrate & vectorize").click()
    w.wait_for(lambda: ph.dialog_open(page), 30, "the Replace dialog")
    page.wait_for_timeout(1000)
    rec["replace_dialog"] = ph.modal_text(page)
    w.shot(page, out, "replace_dialog")
    assert "1 reviewed alert(s) in this set will be discarded." in rec["replace_dialog"], rec["replace_dialog"]
    # each listed alert on a line of its own (ConfirmDialog's details are blocks)
    assert re.search(r"^#\d+ · Reviewed: confirmed · [\d.]+ ha$", rec["replace_dialog"], re.M), rec["replace_dialog"]
    ph.modal_button(page, "Cancel").click()
    page.wait_for_timeout(3000)
    assert not ph.dialog_open(page)
    (july,) = sets_of(JULY)
    rec["after_cancel_toasts"] = [x["msg"] for x in w.toasts_since(page, t0)]
    assert july["created_at"] == created and july["n_reviewed"] == 1, "the Replace ran"
    assert "running" not in w.panel_text(page).lower() and not rec["after_cancel_toasts"], "a job started"


def g_delete_latest(page, rec, *, out):
    w.goto_step(page, "period", 2000)
    ph.row_button(page, JULY_ROW, "mdi-delete-outline").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the delete confirmation")
    page.wait_for_timeout(1000)
    rec["delete_dialog"] = ph.modal_text(page)
    w.shot(page, out, "delete_dialog")
    assert ("Its alert set (1 reviewed alerts), its downloads and every other file of the period are deleted."
            in rec["delete_dialog"]), rec["delete_dialog"]
    assert re.search(r"^GLAD-L · [\d,]+ alerts · 1 reviewed$", rec["delete_dialog"], re.M), rec["delete_dialog"]
    t0 = w.now_ms(page)
    ph.modal_button(page, "Delete").click()
    ph.wait_toast(page, t0, "Period deleted")
    page.wait_for_timeout(1500)
    assert not (DATA / PROJECT / "periods" / JULY).exists()
    p = ph.manifest(DATA, PROJECT)
    assert JULY not in json.dumps(p), "a key, set or artifact still names July"
    (june,) = sets_of(JUNE)
    rec["active"] = (p["active_period"], p["active_set"])
    assert p["active_period"] == JUNE and p["active_set"] == june["id"], rec["active"]
    ph.open_overview(page)
    items = [i["text"] for i in ph.menu_items(page, "Alert set", scope=".dialog-card")]
    rec["selector"] = items
    assert len(items) == 1 and items[0].startswith(JUNE_START), items
    w.close_dialog(page)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8985)
    ap.add_argument("--out", type=Path, default=Path("/tmp/slice5_walk"))
    args = ap.parse_args(argv)
    USER.mkdir(parents=True, exist_ok=True)
    south_mask_tif(USER / "south.tif", AMAZON)
    seed(PROJECT, AMAZON, sources=("GLAD-L",), period=tuple(JUNE.split("_")), look_back=30, root=DATA)
    args.out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, DATA, args.out / "server.log")
    report, ok = {}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            out = args.out
            def counted(fn):  # each phase records the page errors seen so far
                def run(pg, r):
                    try:
                        fn(pg, r)
                    finally:
                        r["page_errors_so_far"] = len(report["page_errors"])
                return run

            ok = ph.run_phases(page, [(name, counted(fn)) for name, fn in [
                ("open", lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, PROJECT))),
                ("a_first_month", lambda pg, r: a_first_month(pg, r, out=out)),
                ("b_second_period", lambda pg, r: b_second_period(pg, r, out=out)),
                ("c_no_repeat", lambda pg, r: c_no_repeat(pg, r, out=out)),
                ("d_rules", lambda pg, r: d_rules(pg, r, out=out)),
                ("e_sets", lambda pg, r: e_sets(pg, r, out=out)),
                ("f_replace_list", lambda pg, r: f_replace_list(pg, r, out=out)),
                ("g_delete_latest", lambda pg, r: g_delete_latest(pg, r, out=out)),
            ]], report, args.out / "report.json")
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
