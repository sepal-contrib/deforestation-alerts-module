"""Reusable phases of the gate walks. Each takes the page and a record dict,
asserts what the spec requires and stores what it saw in the record."""

import glob
import json
import os
import re
import time
import traceback
from pathlib import Path

from tools.walk.walklib import (close_dialog, dialog_text, dialog_visible, goto_step, log, now_ms,
                                panel_text, shot, toasts_since, wait_for)


def manifest(data_dir, project):
    return json.load(open(f"{data_dir}/{project}/project.json"))


def set_dir(data_dir, project):
    hits = sorted(glob.glob(f"{data_dir}/{project}/periods/*/sets/*"), key=os.path.getmtime)
    return hits[-1] if hits else None


def errors(toasts):
    return [x for x in toasts if x["kind"] == "toast-error"]


def visible_btn(page, text, scope=""):
    return page.locator(f'{scope} button:has-text("{text}"):visible'.strip()).first


def alert_info(page):
    return page.evaluate("""() => { const c = [...document.querySelectorAll('.v-card')]
        .find(e => e.getBoundingClientRect().width > 0 && e.innerText.includes('Alert info'));
        return c ? c.innerText.replace(/\\s+/g, ' ').slice(0, 300) : null; }""")


def download_btn(page):
    return page.locator('.v-card:has-text("Save and Export") button:has-text("Download"):visible').first


def prepare_state(page):
    return {
        "ready": page.locator('.right-panel :text-is("ready")').count(),
        "running": page.locator('.right-panel :text-is("running")').count(),
    }


def save_grid(page, *, resolution="30"):
    goto_step(page, "prepare", 2000)
    page.locator(".mdi-crosshairs-gps:visible").first.click()
    page.wait_for_timeout(500)
    page.locator('.right-panel .v-text-field:has(label:has-text("Resolution")) input').first.fill(resolution)
    page.wait_for_timeout(800)
    visible_btn(page, "Save grid").click()
    page.wait_for_timeout(1500)


def run_all(page, rec, *, out, expect_ready=4, timeout=900, shot_name="prepare_done"):
    """Click "Run N pending" and wait for the job: it must start (a row reads
    running, or nothing is pending any more) and end with ``expect_ready``
    "ready" status cells (two per source row, two per Earth Engine mask row,
    one aligned cell per GeoTIFF mask row) and no error toast."""
    goto_step(page, "prepare", 2000)
    run = page.locator('.right-panel button:has-text("pending"):visible').first
    t_run = now_ms(page)
    run.click()
    t0 = time.time()
    wait_for(lambda: prepare_state(page)["running"] or "run 0 pending" in panel_text(page).lower(), 30,
             "the job to start", poll=0.3)
    while time.time() - t0 < timeout:
        st = prepare_state(page)
        if st["ready"] >= expect_ready and not st["running"]:
            break
        if errors(toasts_since(page, t_run)) and not st["running"]:
            break
        page.wait_for_timeout(3000)
    rec["prepare_seconds"] = round(time.time() - t0)
    rec["prepare_panel"] = panel_text(page)[:1500].replace("\n", " | ")
    rec["prepare_toasts"] = [f"{x['kind']}: {x['msg']}" for x in toasts_since(page, t_run)]
    shot(page, out, shot_name)
    assert prepare_state(page)["ready"] >= expect_ready, rec["prepare_panel"]
    assert not errors(toasts_since(page, t_run)), rec["prepare_toasts"]


def prepare_grid_and_run_all(page, rec, *, out, resolution="30", expect_ready=4, timeout=900):
    save_grid(page, resolution=resolution)
    run_all(page, rec, out=out, expect_ready=expect_ready, timeout=timeout)


def integrate_and_land(page, rec, *, out, timeout=600, replace=False):
    """Integrate & vectorize until Analysis shows the set. ``replace``: the set
    exists, so confirm the Replace dialog first."""
    goto_step(page, "integrate", 2000)
    t_int = now_ms(page)
    visible_btn(page, "Integrate & vectorize").click()
    if replace:
        wait_for(lambda: dialog_open(page), 30, "the Replace dialog")
        modal_button(page, "Replace").click()
    t0, landed = time.time(), False
    while time.time() - t0 < timeout:
        loc = page.locator('text="Alert info"')
        if loc.count() and loc.first.is_visible():
            landed = True
            break
        if errors(toasts_since(page, t_int)):
            break
        page.wait_for_timeout(2000)
    page.wait_for_timeout(5000)
    rec["integrate_toasts"] = [f"{x['kind']}: {x['msg']}" for x in toasts_since(page, t_int)]
    shot(page, out, "analysis")
    assert landed, rec["integrate_toasts"]
    assert not errors(toasts_since(page, t_int)), rec["integrate_toasts"]


def review_and_save(page, rec, *, out):
    page.wait_for_timeout(2000)
    cb = page.locator('.v-card:has-text("Loss driver") .v-select__slot:visible').last
    cb.click()
    page.wait_for_timeout(700)
    page.locator('.menuable__content__active .v-list-item:has-text("Logging")').first.click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    t_ms = now_ms(page)
    page.locator('.v-card:has-text("Save and Export") button:has-text("Save"):visible').first.click()
    wait_for(lambda: any("saved" in x["msg"].lower() or x["kind"] in ("toast-error", "toast-warning")
                         for x in toasts_since(page, t_ms)), 120, "save outcome toast")
    page.wait_for_timeout(1500)
    rec["status_after_save"] = alert_info(page)
    rec["save_toasts"] = [f"{x['kind']}: {x['msg']}" for x in toasts_since(page, t_ms)]
    shot(page, out, "saved")
    assert not errors(toasts_since(page, t_ms)), rec["save_toasts"]
    assert rec["status_after_save"] and "Status: Reviewed" in rec["status_after_save"], rec["status_after_save"]
    assert not download_btn(page).is_disabled(), "Download still disabled after the Save"


def pick_images(page, rec, *, out):
    tabs = page.locator('.v-tab:has-text("Sentinel-2"):visible')
    for i in range(tabs.count()):
        tabs.nth(i).click()
        page.wait_for_timeout(3000)
    wait_for(lambda: page.locator(".v-slide-group button:visible").count() >= 2, 240, "image buttons")
    groups = page.locator(".v-slide-group:visible")
    picked = []
    for g in range(groups.count()):
        b = groups.nth(g).locator("button")
        if b.count():
            k = b.count() - 1 if g else 0
            picked.append(b.nth(k).inner_text())
            b.nth(k).click()
            page.wait_for_timeout(4000)
    rec["picked"] = picked
    shot(page, out, "images_picked")
    assert len(picked) >= 2, picked


def download_with_images(page, rec, *, out, folder):
    before = set(os.listdir(folder))
    t_ms = now_ms(page)
    download_btn(page).click()
    wait_for(lambda: any(x["kind"] in ("toast-error", "toast-warning") or "download" in x["msg"].lower()
                         for x in toasts_since(page, t_ms)), 900, "download outcome", poll=2)
    page.wait_for_timeout(3000)
    rec["download_new_files"] = sorted(set(os.listdir(folder)) - before)
    shot(page, out, "downloaded")
    exts = {os.path.splitext(f)[1] for f in rec["download_new_files"]}
    assert {".zip", ".docx"} <= exts, rec["download_new_files"]
    assert not glob.glob(f"{folder}/*.part"), "a .part file was left in the set folder"


def open_overview(page, wait=5000):
    """Open the Overview dashboard: a MapApp rail dialog, not a right-panel
    view, so its content is read with ``walklib.dialog_text`` and it is left
    with ``walklib.close_dialog``."""
    page.locator('[data-step-id="3"]').first.click()
    wait_for(lambda: dialog_visible(page), 30, "the Overview dialog")
    page.wait_for_timeout(wait)


def show_alerts(page, on=True, label="Show these alerts on the map"):
    """Flip Integrate & vectorize's alert switch -- what draws the active set
    on the shared map now that the Overview dashboard draws nothing. The layer
    stays on while the user moves to another step (MapLayers ruling #10)."""
    goto_step(page, "integrate", 2000)
    row = page.locator(f'.right-panel .v-input--switch:has(label:text-is("{label}"))').first
    wait_for(lambda: row.count(), 30, f'the "{label}" switch')
    if row.locator("input").first.is_checked() != on:
        row.locator(".v-input--selection-controls__input").first.click()
        page.wait_for_timeout(2000)


def overview_reviewed(page, rec, *, out, n=1):
    open_overview(page)
    txt = dialog_text(page)
    line = re.search(r"(\d[\d,]*) alerts · (\d+) reviewed", txt)
    rec["overview_line"] = line.group(0) if line else None
    shot(page, out, "overview")
    assert line and int(line.group(2)) == n, txt[:800]
    close_dialog(page)


def overview_export_csv(page, rec, *, out, folder):
    before = set(os.listdir(folder))
    t_ms = now_ms(page)
    open_overview(page)
    page.locator('.dialog-card .v-card:has-text("Export alerts") button:has-text("CSV"):visible').first.click()
    wait_for(lambda: (set(os.listdir(folder)) - before) or errors(toasts_since(page, t_ms)), 120,
             "export file or error")
    page.wait_for_timeout(2000)
    rec["export_new_files"] = sorted(set(os.listdir(folder)) - before)
    shot(page, out, "exported")
    assert any(f.endswith(".csv") for f in rec["export_new_files"]), rec["export_new_files"]
    assert not errors(toasts_since(page, t_ms))
    close_dialog(page)


# ------------------------------------------------- forms, tables and the legend (gates of slices 3-6)
# A step's modal is a Vuetify dialog (`.v-dialog--active`); a ProductTable row is
# a CSS-grid div whose first cell holds the row's name, its under-row notes
# inside it; pysepal's AssetSelectComponent is a combobox labelled "Select an
# asset", its FileInputComponent a `.pysepal-file-input-btn` opening a file menu
# rooted at $HOME that closes itself when a file is picked; the floating map
# legend is LegendComponent's `div.sepal-legend` (z-index 150), its dropdown a
# native <select> shown with two legends or more. Never press Escape inside a
# step modal: ESC is its cancel.

def dialog_open(page) -> bool:
    return page.locator(".v-dialog--active").count() > 0


def modal(page):
    return page.locator(".v-dialog--active").last


def modal_text(page) -> str:
    return modal(page).inner_text() if dialog_open(page) else ""


def modal_button(page, text):
    return modal(page).locator(f'button:has-text("{text}"):visible').last


def modal_field(page, label):
    return modal(page).locator(f'.v-input:has(label:text-is("{label}")) input').first


def radio(page, label, scope=".v-dialog--active"):
    page.locator(f'{scope} .v-radio:has(label:has-text("{label}"))').last.click()
    page.wait_for_timeout(500)


def radio_checked(page, label, scope=".v-dialog--active") -> bool:
    return page.locator(f'{scope} .v-radio.v-item--active:has(label:has-text("{label}"))').count() > 0


def _open_select(page, label, scope):
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()
    page.wait_for_timeout(600)


def menu_items(page, label, scope=".v-dialog--active") -> list:
    """Open the select labelled ``label`` and read its items (text, disabled); the menu is closed again."""
    _open_select(page, label, scope)
    items = page.evaluate("""() => [...document.querySelectorAll('.menuable__content__active .v-list-item')]
        .map(e => ({text: e.innerText.replace(/\\s+/g, ' ').trim(),
                    disabled: e.classList.contains('v-list-item--disabled')}))""")
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()  # close it
    page.wait_for_timeout(400)
    return items


def choose(page, label, option, scope=".v-dialog--active"):
    """Pick ``option`` (substring of an item's text) in the select labelled ``label``."""
    _open_select(page, label, scope)
    page.locator(f'.menuable__content__active .v-list-item:has-text("{option}")').first.click()
    page.wait_for_timeout(600)


def type_asset(page, asset_id, scope=".v-dialog--active"):
    """AssetSelectComponent: type an asset id, commit it (Enter, then Tab)."""
    field = page.locator(f'{scope} .v-input:has(label:has-text("Select an asset")) input:not([type="hidden"])').last
    field.click()
    field.fill(str(asset_id))
    field.press("Enter")
    field.press("Tab")
    page.wait_for_timeout(1500)


def pick_file(page, button_text, path, scope=""):
    """FileInputComponent: open its menu (the button reads ``button_text``) and
    walk from $HOME to ``path`` one entry at a time (the menu closes on the file)."""
    page.locator(f'{scope} .pysepal-file-input-btn:visible:has-text("{button_text}")'.strip()).last.click()
    page.wait_for_timeout(1500)
    for part in Path(path).relative_to(Path.home()).parts:
        item = page.locator(f'.menuable__content__active .v-list-item:has(.v-list-item__title:text-is("{part}"))').first
        wait_for(lambda: item.count() and item.is_visible(), 30, f"file menu entry {part}")
        item.click()
        page.wait_for_timeout(1200)


def row(page, name, scope=".right-panel"):
    """The visible ProductTable row whose first cell starts with ``name``."""
    first_cell = page.locator(":scope > div:first-child", has_text=re.compile(rf"^\s*{re.escape(name)}"))
    return page.locator(f'{scope} div[style*="grid-template-columns"]:visible').filter(has=first_cell).first


def row_text(page, name, scope=".right-panel") -> str:
    loc = row(page, name, scope)
    return re.sub(r"\s+", " ", loc.inner_text()) if loc.count() else ""


def row_button(page, name, icon, scope=".right-panel"):
    """A row action by its icon (ProductTable): mdi-map-plus / mdi-map-minus (show / hide),
    mdi-cloud-download-outline (run), mdi-cloud-refresh (re-download), mdi-pencil-outline,
    mdi-delete-outline, mdi-radiobox-blank / mdi-radiobox-marked (select)."""
    return row(page, name, scope).locator(f"button:has(.{icon})").first


def row_switch(page, name):
    """Flip the "on in this period" switch of a Masks card row."""
    row(page, name).locator(".v-input--switch .v-input--selection-controls__input").first.click()
    page.wait_for_timeout(1200)


def row_mode(page, name, mode):
    """Set a Masks card row's keep / exclude select ("Keep" or "Exclude")."""
    row(page, name).locator(".v-select .v-select__slot").first.click()
    page.wait_for_timeout(600)
    page.locator(f'.menuable__content__active .v-list-item:has-text("{mode}")').first.click()
    page.wait_for_timeout(1200)


def wait_toast(page, t0_ms, text, timeout=60, kind=None):
    return wait_for(lambda: [x for x in toasts_since(page, t0_ms)
                             if text in x["msg"] and (kind is None or x["kind"] == f"toast-{kind}")],
                    timeout, f"toast {text!r}")


def add_mask(page, rec, *, name, origin, ref, band, mode="Keep", out):
    """Download & harmonize → Masks card → Add mask, validated and listed (spec §7.3)."""
    goto_step(page, "prepare", 2000)
    visible_btn(page, "Add mask", ".right-panel").click()
    wait_for(lambda: dialog_open(page), 15, "the mask modal")
    modal_field(page, "Name").fill(name)
    if origin == "gee":
        radio(page, "Earth Engine asset")
        type_asset(page, ref)
    else:
        radio(page, "GeoTIFF in my SEPAL folder")
        pick_file(page, "GeoTIFF", ref, scope=".v-dialog--active")
    wait_for(lambda: "Reading the bands" not in modal_text(page) and "Could not read" not in modal_text(page), 120,
             f"the bands of {ref}")
    choose(page, "Band", str(band))
    radio(page, mode)
    shot(page, out, f"mask_modal_{name}")
    modal_button(page, "Add mask").click()
    wait_for(lambda: not dialog_open(page), 120, f"the mask {name} validated (modal: {modal_text(page)[:200]})")
    wait_for(lambda: row_text(page, name), 30, f"the mask row {name}")
    rec[f"mask_row_{name}"] = row_text(page, name)


def legend_text(page) -> str:
    """The floating legend's text without its dropdown; '' while it is hidden."""
    return page.evaluate("""() => [...document.querySelectorAll('.sepal-legend')]
        .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
        .map(e => { const c = e.cloneNode(true); c.querySelectorAll('select').forEach(s => s.remove());
                    return c.textContent.replace(/\\s+/g, ' ').trim(); }).join(' | ')""")


def legend_options(page) -> list:
    """The legend dropdown's entries ``{"text", "value", "selected"}``; [] with one legend or none."""
    return page.evaluate("""() => { const s = [...document.querySelectorAll('.sepal-legend select')]
        .find(e => e.getBoundingClientRect().width > 0);
        return s ? [...s.options].map(o => ({text: o.text.trim(), value: o.value, selected: o.selected})) : []; }""")


def analysis_go_to(page, position):
    """Analysis navigator: type ``position`` in the id field next to Go and press Go."""
    go = page.locator("button:visible", has_text=re.compile(r"^\s*Go\s*$")).first
    field = go.locator("xpath=..").locator("input").first
    field.fill(str(position))
    go.click()
    page.wait_for_timeout(4000)


def run_phases(page, phases, report, report_path):
    """Run ``[(name, fn)]`` in order; stop at the first failure; write the report after each."""
    ok = True
    for name, fn in phases:
        rec = {"ok": True}
        t0 = now_ms(page)
        log(f"=== {name} ===")
        try:
            fn(page, rec)
        except Exception as e:  # noqa: BLE001
            rec.update(ok=False, error=f"{e.__class__.__name__}: {e}", trace=traceback.format_exc()[-1500:])
            ok = False
        rec["toasts"] = toasts_since(page, t0)
        report.setdefault("phases", {})[name] = rec
        with open(report_path, "w") as f:
            json.dump(report, f, indent=1, ensure_ascii=False, default=str)
        if not ok:
            log(f"FAILED {name}: {rec['error']}")
            break
    return ok
