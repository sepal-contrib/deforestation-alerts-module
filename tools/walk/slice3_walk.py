"""Slice 3 gate walk (spec §14 row 3): (a) custom Earth Engine and GeoTIFF
sources beside GLAD-L in one set, (b) a date-only Earth Engine source in yymm,
(c) coverage that blocks and explains, (d) a window without data, (f) the
attribute raster from Earth Engine and from a GeoTIFF. (e) is Step 1's tests.

    python -m tools.walk.slice3_walk --port 8983 --out /tmp/slice3_walk
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import docx
import geopandas as gpd
import pandas as pd
import rasterio
from playwright.sync_api import sync_playwright

from tools.walk import make_rasters
from tools.walk import phases as ph
from tools.walk import walklib as w
from tools.walk.seed_project import seed

DATA = Path.home() / "module_results" / "deforestation_alerts_walk_slice3"
USER = DATA / "user"  # the walk's "SEPAL folder" files: under $HOME, like a user's
AMAZON = (-55.10, -10.10, -55.00, -10.00)
CANINDEYU = (-55.5, -24.5, -55.4, -24.4)
MONTH_STARTS = {date(2026, m, 1).timetuple().tm_yday for m in range(1, 13)}
OPEN_PANEL = ".right-panel .v-expansion-panel--active"


def _ee():
    import ee
    from pysepal.scripts.utils import init_ee

    init_ee()
    return ee


def latest_radd_sa() -> str:
    ee = _ee()
    col = (ee.ImageCollection("projects/radar-wur/raddalert/v1").filter(ee.Filter.eq("geography", "sa"))
           .filterMetadata("layer", "contains", "alert").sort("system:time_end", False))
    return ee.Image(col.first()).get("system:id").getInfo()


def s2_image(bbox) -> str:
    ee = _ee()
    box = ee.Geometry.Rectangle(list(bbox))
    # an image whose footprint holds the whole box, the least cloudy: the
    # first image of filterBounds can be a tile edge with no data over the alerts
    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(box)
            .filterDate("2025-06-01", "2025-09-30").filter(ee.Filter.contains(leftField=".geo", rightValue=box))
            .sort("CLOUDY_PIXEL_PERCENTAGE").first().get("system:id").getInfo())


def store(project):
    m = ph.manifest(DATA, project)
    s = m["alert_sets"][m["active_set"]]
    return gpd.read_parquet(DATA / project / s["store_rel_path"]), s


def harmonized(project, source):
    m = ph.manifest(DATA, project)
    art = next(a for k, a in m["artifacts"].items() if k.startswith("harm:") and k.endswith(f":{source}"))
    with rasterio.open(DATA / project / art["rel_path"]) as r:
        return r.read(1), r.read(2)


def day_of_year(value) -> int:
    """``alert_date_min`` is ``yyyy.ddd`` (2026.182 is 1 July)."""
    return int(round((float(value) - int(float(value))) * 1000))


def add_custom(page, rec, *, name, origin, ref, preset, encoding=None, out):
    """Alerts → Add source → Custom: one source through the modal, validated and listed (spec §6.2)."""
    w.goto_step(page, "alerts", 2000)
    ph.visible_btn(page, "Add source", ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the source modal")
    ph.modal_button(page, "Custom").click()
    ph.modal_field(page, "Name").fill(name)
    if origin == "gee":
        ph.radio(page, "Earth Engine asset")
        ph.type_asset(page, ref)
    else:
        ph.radio(page, "GeoTIFF in my SEPAL folder")
        ph.pick_file(page, "GeoTIFF", ref, scope=".v-dialog--active")
    w.wait_for(lambda: ph.radio_checked(page, preset), 180, f"{ref}: bands listed and the {preset} preset suggested")
    if encoding:
        ph.choose(page, "Date encoding", encoding)
    rec[f"modal_{name}"] = ph.modal_text(page)[:800]
    w.shot(page, out, f"modal_{name.replace(' ', '_')}")
    ph.modal_button(page, "Add source").click()
    w.wait_for(lambda: not ph.dialog_open(page), 180, f"{name} validated and added")
    w.wait_for(lambda: ph.row_text(page, name), 30, f"the {name} row")
    rec[f"row_{name}"] = ph.row_text(page, name)


def a_mixed(page, rec, *, out, folder):
    add_custom(page, rec, name="radd sa", origin="gee", ref=latest_radd_sa(), preset="RADD", out=out)
    assert "custom · RADD" in rec["row_radd sa"] and "covers" in rec["row_radd sa"], rec["row_radd sa"]
    add_custom(page, rec, name="ccdc tif", origin="file", ref=USER / "ccdc.tif", preset="SEPAL CCDC alerts", out=out)
    assert "custom · SEPAL CCDC alerts" in rec["row_ccdc tif"], rec["row_ccdc tif"]
    ph.prepare_grid_and_run_all(page, rec, out=out, expect_ready=6)  # three rows, download + harmonize each
    ph.integrate_and_land(page, rec, out=out)
    alerts, s = store("s3a")
    rec["digit_map"] = s["digit_map"]
    assert {int(k): v for k, v in s["digit_map"].items()} == {0: "GLAD-L", 3: "radd sa", 4: "ccdc tif"}, s["digit_map"]
    named = [str(v) for v in alerts["alert_sources"]]
    for custom in ("radd sa", "ccdc tif"):  # the Alert info card names a custom source
        i = next((k for k, v in enumerate(named) if custom in v), None)
        assert i is not None, f"no alert from {custom}: {sorted(set(named))[:10]}"
        ph.analysis_go_to(page, i)
        rec[f"alert_info_{custom}"] = ph.alert_info(page) or ""
        assert custom in rec[f"alert_info_{custom}"], rec[f"alert_info_{custom}"]
    # the alert on screen is a "ccdc tif" one: its report names its sources
    ph.review_and_save(page, rec, out=out)
    ph.pick_images(page, rec, out=out)
    ph.download_with_images(page, rec, out=out, folder=folder())
    report = sorted(Path(folder()).glob("alert_*.docx"), key=lambda p: p.stat().st_mtime)[-1]
    text = "\n".join(p.text for p in docx.Document(str(report)).paragraphs)
    rec["report"] = report.name
    assert "ccdc tif" in text, f"{report.name} does not name the alert's sources"
    # Both open the Overview dialog, where the export card is, and close it again.
    ph.overview_reviewed(page, rec, out=out, n=1)
    ph.overview_export_csv(page, rec, out=out, folder=folder())
    csv = next(f for f in rec["export_new_files"] if f.endswith(".csv"))
    joined = " | ".join(map(str, pd.read_csv(Path(folder()) / csv)["alert_sources"]))
    assert "radd sa" in joined and "ccdc tif" in joined, joined[:300]


def _attribute_header(page):
    return page.locator('.right-panel .v-expansion-panel-header:has-text("Attribute raster")').first


def _load_attribute(page, *, origin, ref):
    header = _attribute_header(page)
    if not page.locator(OPEN_PANEL).count():
        header.click()
        page.wait_for_timeout(800)
    if origin == "gee":
        ph.radio(page, "Earth Engine asset", scope=OPEN_PANEL)
        ph.type_asset(page, ref, scope=OPEN_PANEL)
    else:
        ph.radio(page, "GeoTIFF in my SEPAL folder", scope=OPEN_PANEL)
        ph.pick_file(page, "GeoTIFF", ref, scope=OPEN_PANEL)
    ph.visible_btn(page, "Load bands", OPEN_PANEL).click()
    # the rows of a raster loaded before stay until the new bands are in: wait for this one's
    w.wait_for(lambda: f"Bands of {ref}" in page.locator(OPEN_PANEL).inner_text()
               and page.locator(f"{OPEN_PANEL} .v-input--checkbox").count(), 120, f"the band list of {ref}")


def _include_only(page, band):
    boxes = page.locator(f"{OPEN_PANEL} .v-input--checkbox")
    for i in range(boxes.count()):
        box = boxes.nth(i)
        checked = "v-input--is-label-active" in (box.get_attribute("class") or "")
        if (box.inner_text().strip() == band) != checked:
            box.locator(".v-input--selection-controls__input").click()
            page.wait_for_timeout(150)


def f_attribute(page, rec, *, out):
    w.goto_step(page, "integrate", 2000)
    _load_attribute(page, origin="gee", ref=s2_image(AMAZON))
    _include_only(page, "B11")
    _attribute_header(page).click()  # collapsed, the header names the raster
    page.wait_for_timeout(800)
    rec["header"] = _attribute_header(page).inner_text()
    assert rec["header"].startswith("Attribute raster:"), rec["header"]
    ph.integrate_and_land(page, rec, out=out, replace=True)
    alerts, _ = store("s3a")
    assert "b11" in alerts.columns and alerts["b11"].notna().any(), list(alerts.columns)
    rec["b11_alerts"] = f'{int(alerts["b11"].notna().sum())} of {len(alerts)}'
    w.goto_step(page, "integrate", 2000)
    _load_attribute(page, origin="file", ref=USER / "slope.tif")
    _include_only(page, "slope")
    ph.integrate_and_land(page, rec, out=out)  # the set just rebuilt has no review: no Replace dialog
    alerts, _ = store("s3a")
    rec["columns"] = list(alerts.columns)
    assert "slope" in alerts.columns and alerts["slope"].notna().any(), rec["columns"]
    rec["slope_alerts"] = f'{int(alerts["slope"].notna().sum())} of {len(alerts)}'


def b_date_only(page, rec, *, out, asset):
    w.open_project(page, "s3b")
    add_custom(page, rec, name="monthly", origin="gee", ref=asset, preset="Date only", encoding="yymm", out=out)
    ph.prepare_grid_and_run_all(page, rec, out=out, expect_ready=2)
    ph.integrate_and_land(page, rec, out=out)
    alert, dates = harmonized("s3b", "monthly")
    rec["codes"] = sorted(set(alert[alert > 0].tolist()))
    rec["days"] = sorted({int(d) % 1000 for d in dates[dates > 0].tolist()})
    assert rec["codes"] == [1], rec["codes"]  # every dated pixel is a confirmed alert
    assert rec["days"] and set(rec["days"]) <= MONTH_STARTS, rec["days"]  # yymm: the month's first day
    alerts, _ = store("s3b")
    rec["store_days"] = sorted({day_of_year(v) for v in alerts["alert_date_min"]})
    assert len(alerts) and set(rec["store_days"]) <= MONTH_STARTS, rec["store_days"]


def c_not_covered(page, rec, *, out):
    w.open_project(page, "s3c1")
    w.goto_step(page, "alerts", 2000)
    ph.visible_btn(page, "Add source", ".right-panel").click()
    w.wait_for(lambda: ph.dialog_open(page), 15, "the source modal")
    w.wait_for(lambda: "checking coverage" not in " ".join(i["text"] for i in ph.menu_items(page, "Predefined source")),
               120, "the coverage check", poll=3)
    items = {i["text"]: i["disabled"] for i in ph.menu_items(page, "Predefined source")}
    rec["predefined_items"] = items
    s2 = next(k for k in items if k.startswith("GLAD-S2"))
    assert items[s2] and "GLAD-S2 does not cover this AOI: its area is 90°W–30°W, 20°S–20°N" in s2, items
    ph.modal_button(page, "Cancel").click()
    w.wait_for(lambda: not ph.dialog_open(page), 15, "the modal closed")

    w.open_project(page, "s3c2")
    w.goto_step(page, "alerts", 2000)
    w.wait_for(lambda: "does not cover the AOI" in ph.row_text(page, "GLAD-S2"), 120, "GLAD-S2's coverage chip")
    rec["alerts_row"] = ph.row_text(page, "GLAD-S2")
    assert ph.row_button(page, "GLAD-S2", "mdi-map-plus").is_disabled()
    ph.save_grid(page)
    rec["prepare_row"] = ph.row_text(page, "GLAD-S2")
    assert "Not covered: GLAD-S2 does not cover this AOI" in rec["prepare_row"], rec["prepare_row"]
    assert ph.row_button(page, "GLAD-S2", "mdi-cloud-download-outline").is_disabled()
    assert ph.row_button(page, "GLAD-S2", "mdi-map-plus").is_disabled()
    # innerText is upper case (Vuetify buttons): compare case-insensitively
    assert "run 1 pending" in w.panel_text(page).lower(), w.panel_text(page)[-600:]
    ph.run_all(page, rec, out=out, expect_ready=2)
    w.goto_step(page, "integrate", 2000)
    choice = page.locator('.right-panel .v-input--checkbox:has(label:has-text("GLAD-S2"))').first
    rec["integrate_choice"] = choice.inner_text()
    assert "v-input--is-disabled" in (choice.get_attribute("class") or ""), rec["integrate_choice"]
    assert "does not cover" in rec["integrate_choice"], rec["integrate_choice"]
    w.shot(page, out, "not_covered")


def d_no_data(page, rec, *, out):
    w.open_project(page, "s3d")
    ph.save_grid(page)
    w.wait_for(lambda: "No data for this period" in ph.row_text(page, "RADD"), 120, "the RADD row's reason")
    rec["row"] = ph.row_text(page, "RADD")
    assert "No data for this period: RADD starts 2020-01-01 here" in rec["row"], rec["row"]
    assert ph.row_button(page, "RADD", "mdi-cloud-download-outline").is_disabled()
    w.shot(page, out, "no_data")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8983)
    ap.add_argument("--out", type=Path, default=Path("/tmp/slice3_walk"))
    args = ap.parse_args(argv)
    USER.mkdir(parents=True, exist_ok=True)
    make_rasters.ccdc_tif(USER / "ccdc.tif", AMAZON, when=date(2026, 3, 14))
    make_rasters.slope_tif(USER / "slope.tif", AMAZON)
    asset = make_rasters.ensure_yymm_asset(AMAZON, months=(2603, 2604))
    # 2025-06 on: RADD has only 6 pixels (10 m) in the box in 2026, below the minimum alert size
    seed("s3a", AMAZON, sources=("GLAD-L",), period=("2025-06-01", "2026-08-31"), look_back=0, root=DATA)
    seed("s3b", AMAZON, period=("2026-03-01", "2026-04-30"), look_back=0, root=DATA)
    seed("s3c1", CANINDEYU, sources=("GLAD-L",), root=DATA)
    seed("s3c2", CANINDEYU, sources=("GLAD-L", "GLAD-S2"), period=("2026-06-01", "2026-08-31"), look_back=0,
         root=DATA)
    seed("s3d", AMAZON, sources=("RADD",), period=("2019-01-01", "2019-12-31"), look_back=0, root=DATA)
    args.out.mkdir(parents=True, exist_ok=True)
    proc = w.start_server(args.port, DATA, args.out / "server.log")
    report, ok = {"yymm_asset": asset}, False
    try:
        with sync_playwright() as pw:
            browser, page = w.new_page(pw, report)
            out = args.out
            folder = lambda: ph.set_dir(DATA, "s3a")  # noqa: E731
            ok = ph.run_phases(page, [
                ("open", lambda pg, r: (w.open_app(pg, args.port), w.open_project(pg, "s3a"))),
                ("a_mixed", lambda pg, r: a_mixed(pg, r, out=out, folder=folder)),
                ("f_attribute", lambda pg, r: f_attribute(pg, r, out=out)),
                ("b_date_only", lambda pg, r: b_date_only(pg, r, out=out, asset=asset)),
                ("c_not_covered", lambda pg, r: c_not_covered(pg, r, out=out)),
                ("d_no_data", lambda pg, r: d_no_data(pg, r, out=out)),
            ], report, args.out / "report.json")
            report["page_errors_count"] = len(report.get("page_errors", []))
            browser.close()
    finally:
        w.stop_server(proc)
    print("WALK", "PASS" if ok else "FAIL", args.out / "report.json")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
