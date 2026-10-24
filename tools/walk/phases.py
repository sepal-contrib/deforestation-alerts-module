"""Reusable phases of the gate walks. Each takes the page and a record dict,
asserts what the spec requires and stores what it saw in the record."""

import glob
import json
import os
import re
import time
import traceback
from pathlib import Path

from tools.walk.walklib import (ENTER_BUTTON, MODAL, close_dialog, dialog_text, dialog_visible, en, goto_step,
                                log, now_ms, panel_text, shot, toasts_since, wait_for)

#: Form labels as the app shows them (the English catalogue).
SOURCE_TYPE = en("steps.alerts.mode_label")
MODE_OFF = en("steps.prepare.mask_mode_off")
#: The Source select above a picker (the mask, attribute and visualization-layer forms)
FIELD_SOURCE = en("steps.raster_input.field_source")
SOURCE = {"gee": en("steps.raster_input.source_gee"), "file": en("steps.raster_input.source_file")}
#: What shows under that picker: the band list being read, or why it could not be
BANDS_LOADING = en("steps.raster_input.bands_loading")
BANDS_FAILED = en("steps.raster_input.bands_failed").split("{")[0]
#: The mask form's Band select (two bands or more), or its one-band line
MASK_BAND = en("steps.prepare.mask_band")
MASK_ONE_BAND = en("steps.prepare.mask_band_one").split("{")[0]
#: The alert-source form's selects (Alert format and the two band selects with two
#: bands or more, Date encoding once the date values are read), its one-band line
#: and the line shown while the date values are read
ALERT_FORMAT = en("steps.alerts.format_label")
ALERT_BAND = en("steps.alerts.band_alert")
DATE_BAND = en("steps.alerts.band_date")
DATE_ENCODING = en("steps.alerts.encoding_label")
ALERT_ONE_BAND = en("steps.alerts.band_one").split("{")[0]
DATES_LOADING = en("steps.alerts.dates_loading")


def manifest(data_dir, project):
    return json.load(open(f"{data_dir}/{project}/project.json"))


def set_dir(data_dir, project):
    hits = sorted(glob.glob(f"{data_dir}/{project}/periods/*/sets/*"), key=os.path.getmtime)
    return hits[-1] if hits else None


def errors(toasts):
    return [x for x in toasts if x["kind"] == "toast-error"]


def visible_btn(page, text, scope=""):
    return page.locator(f'{scope} button:has-text("{text}"):visible'.strip()).first


# The analysis screen's right column is the review console (spec 2026-10-03): block A
# (queue counter, navigation, facts grid + status chip), block B (the verdict)
# and three sections -- Questions (the loss driver and every other project
# question, spec D4), Refine polygon, and Export this alert, which sits in the
# sticky footer above Save & next / Save (spec D6) -- whose bodies are
# COLLAPSED (hidden with ``display``), so nothing inside them is ``:visible``
# until the header is clicked. The retired "Alert info" card's facts live in block A.
# Block A is a flat ``panel_block`` (a ``div.py-3``, no longer a ``v-card``).
_BLOCK_A_JS = """
    const card = [...document.querySelectorAll('.defo-analysis-right .py-3, .defo-analysis-right .pb-3')]
        .filter(e => e.getBoundingClientRect().width > 0)
        .filter(e => ['Source', 'Detected', 'Status'].every(w => e.textContent.includes(w)))
        .sort((a, b) => a.textContent.length - b.textContent.length)[0];
"""


def alert_facts(page):
    """Block A as one line: the queue counter, the navigation and the facts
    grid's Source / Detected / Status rows with their values.

    ``textContent``, not ``innerText``: the grid's labels are drawn uppercase
    by ``text-uppercase`` and ``innerText`` applies that transform.
    """
    return page.evaluate("() => {%s return card ? card.textContent"
                         ".replace(/\\s+/g, ' ').trim().slice(0, 300) : null; }" % _BLOCK_A_JS)


def status_chip(page):
    """The review-status chip of block A's facts grid -- the only chip in that
    card -- in the legend's wording ("Not reviewed", "Reviewed: confirmed").
    Its colour is a swatch span, so the words are what there is to read."""
    return page.evaluate("() => {%s const chip = card && card.querySelector('.v-chip');"
                         " return chip ? chip.textContent.replace(/\\s+/g, ' ').trim()"
                         " : null; }" % _BLOCK_A_JS)


def open_section(page, title):
    """Open a collapsible Analysis section ("Questions" / "Refine polygon" /
    "Export this alert"): its body is hidden with ``display`` until the 50px header is
    clicked, and the header's chevron says which way it is."""
    header = visible_btn(page, title)
    wait_for(lambda: header.count(), 30, f'the "{title}" section header')
    if not header.locator(".mdi-chevron-down").count():
        header.click()
        page.wait_for_timeout(800)


#: The panel's sticky footer (``gui/tile/analysis/footer.py``), by its class.
_FOOTER = ".defo-analysis-footer"


def save_btn(page):
    """The footer's *Save* -- exact text, since *Save & next* also reads
    "Save", and SCOPED to the footer, since three other catalogue strings
    render exactly "Save" too (the alert-source, period and visualization
    modals' own Save buttons). All three live inside modals today, so a
    document-wide match is unique only by luck: a modal a preceding phase left
    open would be clicked instead, with no diagnostic. The footer's buttons
    sit outside the collapsed Export body, so no section has to be opened."""
    return page.locator(f"{_FOOTER} button:visible",
                        has_text=re.compile(r"^\s*Save\s*$")).first


def download_btn(page):
    """The dashed *Build alert files* tile (``.defo-build-tile``), in the
    Export section's body (open it first). Only before a build: it hides once
    a build left files, and *Rebuild files* is another button
    (``.defo-rebuild``). Found by its class, not its label: "(save first)"
    shows in the label until the alert is saved."""
    return page.locator('.defo-analysis-right .defo-build-tile:visible').first


#: The Prepare step's labels in English (``run_all`` takes others, see
#: ``prepare_labels``): the plural forms of the "Run {n} pending" button and
#: the status cells' texts.
EN_PREPARE = None


def prepare_labels(forms, ready, running):
    """The labels ``run_all`` reads, in one language: ``forms`` the plural
    forms of ``steps.prepare.run_all`` (each with ``{n}``), ``ready`` /
    ``running`` the ``widgets.product_table.status_*`` texts."""
    alts = "|".join(re.escape(f).replace(re.escape("{n}"), r"\d+") for f in sorted(set(forms)))
    return {"run": re.compile(rf"^\s*(?:{alts})\s*$"), "ready": ready, "running": running}


def en_prepare():
    global EN_PREPARE
    if EN_PREPARE is None:
        from tools.walk.walklib import MESSAGES_EN

        node = json.loads((MESSAGES_EN / "steps.json").read_text(encoding="utf-8"))["steps"]["prepare"]["run_all"]
        EN_PREPARE = prepare_labels(node.values(), en("widgets.product_table.status_ready"),
                                    en("widgets.product_table.status_running"))
    return EN_PREPARE


def run_button(page, labels=None):
    """The "Run {n} pending" button of the Prepare panel, found by its
    catalogue text (any plural form) in the labels' language."""
    labels = labels or en_prepare()
    return page.locator(".right-panel button:visible").filter(has_text=labels["run"]).first


def pending_count(page, labels=None) -> int:
    """The {n} of the "Run {n} pending" button (0 when it is not shown)."""
    btn = run_button(page, labels)
    if not btn.count():
        return 0
    m = re.search(r"\d+", btn.inner_text())
    return int(m.group()) if m else 0


def prepare_state(page, labels=None):
    """The "ready" / "running" status cells on the panel, by their label (a
    Masks row's status is a label too, since Task 7)."""
    labels = labels or en_prepare()
    return {state: page.locator(f'.right-panel :text-is("{labels[state]}")').count() for state in ("ready", "running")}


def save_grid(page, *, resolution="30"):
    goto_step(page, "prepare", 2000)
    page.locator(".mdi-crosshairs-gps:visible").first.click()
    page.wait_for_timeout(500)
    page.locator('.right-panel .v-text-field:has(label:has-text("Resolution")) input').first.fill(resolution)
    page.wait_for_timeout(800)
    visible_btn(page, "Save grid").click()
    page.wait_for_timeout(1500)


def run_all(page, rec, *, out, expect_ready=4, timeout=900, shot_name="prepare_done", labels=None):
    """Click "Run N pending" and wait for the job: it must start (a row reads
    running, or nothing is pending any more) and end with ``expect_ready``
    "ready" status cells (two per source row, two per Earth Engine mask row,
    one aligned cell per GeoTIFF mask row) and no error toast. ``labels``
    (``prepare_labels``) for a language other than English."""
    goto_step(page, "prepare", 2000)
    run = run_button(page, labels)
    t_run = now_ms(page)
    run.click()
    t0 = time.time()
    wait_for(lambda: prepare_state(page, labels)["running"] or pending_count(page, labels) == 0, 30,
             "the job to start", poll=0.3)
    while time.time() - t0 < timeout:
        st = prepare_state(page, labels)
        if st["ready"] >= expect_ready and not st["running"]:
            break
        if errors(toasts_since(page, t_run)) and not st["running"]:
            break
        page.wait_for_timeout(3000)
    rec["prepare_seconds"] = round(time.time() - t0)
    rec["prepare_panel"] = panel_text(page)[:1500].replace("\n", " | ")
    rec["prepare_toasts"] = [f"{x['kind']}: {x['msg']}" for x in toasts_since(page, t_run)]
    shot(page, out, shot_name)
    assert prepare_state(page, labels)["ready"] >= expect_ready, rec["prepare_panel"]
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
        # Block A's card is on screen. NOT "the new set's alert is on screen":
        # the Source / Detected / Status labels are written once at build and
        # the values are left alone while another set's rows are current
        # (``context.facts_of`` returns None), so this is truthy as soon as the
        # Analysis panel is visible, alert or no alert. Same semantics as the
        # retired ``text="Alert info"`` anchor; the assertions that follow the
        # 5s settle below are what read the alert itself.
        if alert_facts(page):
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
    """Block B: answer the verdict question, pick a loss driver in the
    Questions section (collapsed by default, so opened first), then **Save**
    (not *Save & next*: the assertions below read the alert that was saved).
    There is no default answer any more -- an unanswered Save is refused -- so
    the verdict button is pressed here."""
    page.wait_for_timeout(2000)
    visible_btn(page, "True alert").click()
    page.wait_for_timeout(500)
    open_section(page, "Questions")
    cb = page.locator('.defo-analysis-right .dat-question:has-text("Loss driver") .v-select__slot:visible').last
    cb.click()
    page.wait_for_timeout(700)
    page.locator('.menuable__content__active .v-list-item:has-text("Logging")').first.click()
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    t_ms = now_ms(page)
    save_btn(page).click()
    wait_for(lambda: any("saved" in x["msg"].lower() or x["kind"] in ("toast-error", "toast-warning")
                         for x in toasts_since(page, t_ms)), 120, "save outcome toast")
    page.wait_for_timeout(1500)
    rec["status_after_save"] = status_chip(page)
    rec["facts_after_save"] = alert_facts(page)
    rec["save_toasts"] = [f"{x['kind']}: {x['msg']}" for x in toasts_since(page, t_ms)]
    shot(page, out, "saved")
    assert not errors(toasts_since(page, t_ms)), rec["save_toasts"]
    # Block A re-reads the row the save wrote, so the chip is the store's
    # answer and not the button that was pressed.
    assert (rec["status_after_save"] or "").startswith("Reviewed"), rec["facts_after_save"]
    open_section(page, "Export this alert")
    assert not download_btn(page).is_disabled(), "Build alert files still disabled after the Save"


def _lane_options(page):
    """The pickable stripes of the timeline's one lane: buttons out of the tab
    order (the handles are the keyboard's way along it), disabled while the
    cloud limit hides them. Planet is left out: a NICFI pick depends on the
    account's access."""
    host = page.locator(".analysis-timeline:visible").first
    return host.locator('button[tabindex="-1"]:not([disabled])'
                        ':not([aria-label^="Planet"]):not([aria-current="true"])')


def map_titles(page) -> list:
    """The two map panes' titles, which name the image each one shows."""
    return [t.inner_text() for t in page.locator(".analysis-mapbox:visible .v-card__title").all()]


def pick_images(page, rec, *, out):
    """The shared image timeline (``gui/tile/analysis/timeline.py``): a click
    on a stripe left of the before handle moves the before handle, one right
    of the after handle moves the after handle. Picks the earliest and the
    latest stripe drawn, as the retired per-pane selectors' first and last
    buttons did."""
    opts = _lane_options(page)
    wait_for(lambda: opts.count() >= 2, 240, "image stripes in the timeline lane")
    picked = []
    for k in (0, -1):
        btn = opts.nth(k if k >= 0 else opts.count() - 1)
        picked.append(btn.get_attribute("aria-label"))
        # Stripes are a few px apart and overlap their neighbours' hit areas:
        # the click goes to this one, not to the point under its centre.
        btn.dispatch_event("click")
        page.wait_for_timeout(4000)
    rec["picked"] = picked
    unset = en("analysis.timeline.unset")
    wait_for(lambda: len(map_titles(page)) == 2 and not any(unset in t for t in map_titles(page)),
             30, "both images named in the map titles")
    shot(page, out, "images_picked")
    assert len(picked) >= 2, picked


def download_with_images(page, rec, *, out, folder):
    open_section(page, "Export this alert")
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
    """Flip the active set's alert switch, which draws the set on the shared
    map. The layer stays on while the user moves to another step (MapLayers
    ruling #10). Integrate & vectorize's switch is used when that step is open.
    With every source out of date, the step is locked and shows no form, so
    the walk uses the Overview dialog's copy of the switch instead: the same
    boolean, and turning it ON closes the dialog."""
    goto_step(page, "integrate", 2000)
    row = page.locator(f'.right-panel .v-input--switch:has(label:text-is("{label}"))').first
    locked = page.locator(f'.right-panel :text-is("{en("workflow.lock_integrate")}")')
    wait_for(lambda: row.count() or locked.count(), 30, f'the "{label}" switch or the Integrate lock')
    if not row.count():
        open_overview(page, wait=2000)
        row = page.locator(f'.dialog-card .v-input--switch:has(label:text-is("{label}"))').first
        wait_for(lambda: row.count(), 60, f'the Overview\'s "{label}" switch')
    if row.locator("input").first.is_checked() != on:
        row.locator(".v-input--selection-controls__input").first.click()
        page.wait_for_timeout(2000)
    close_dialog(page)


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
# A step's modal is a Vuetify dialog (`MODAL`) on the form frame: an outside
# click nudges it, ESC cancels it, Enter presses its one `ENTER_BUTTON`
# (`primary_button`); its raster origin is a select labelled FIELD_SOURCE
# above the picker (`choose_source`). A ProductTable row is a CSS-grid div whose first cell
# holds the row's name, its under-row notes inside it; a Masks card row has
# one Mode select (Off / Keep / Exclude) for its old switch and mode.
# pysepal's AssetSelectComponent is a combobox labelled "Select an asset", its
# FileInputComponent a `.pysepal-file-input-btn` opening a file menu rooted at
# $HOME that closes itself when a file is picked; the floating map legend is
# LegendComponent's `div.sepal-legend` (z-index 150), its dropdown a native
# <select> shown with two legends or more. Never press Escape inside a step
# modal unless the walk means to cancel it.

def dialog_open(page) -> bool:
    return page.locator(MODAL).count() > 0


def modal(page):
    return page.locator(MODAL).last


def modal_text(page) -> str:
    return modal(page).inner_text() if dialog_open(page) else ""


def modal_button(page, text):
    return modal(page).locator(f'button:has-text("{text}"):visible').last


def primary_button(page):
    """The open form's primary button ("Add …" or "Save", whatever the
    label): its one Enter default. Absent while the form's check runs."""
    return modal(page).locator(f"{ENTER_BUTTON}:visible").last


def modal_field(page, label):
    return modal(page).locator(f'.v-input:has(label:text-is("{label}")) input').first


def radio(page, label, scope=MODAL):
    page.locator(f'{scope} .v-radio:has(label:has-text("{label}"))').last.click()
    page.wait_for_timeout(500)


def _open_select(page, label, scope):
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()
    page.wait_for_timeout(600)


def menu_items(page, label, scope=MODAL) -> list:
    """Open the select labelled ``label`` and read its items (text, disabled); the menu is closed again."""
    _open_select(page, label, scope)
    items = page.evaluate("""() => [...document.querySelectorAll('.menuable__content__active .v-list-item')]
        .map(e => ({text: e.innerText.replace(/\\s+/g, ' ').trim(),
                    disabled: e.classList.contains('v-list-item--disabled')}))""")
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()  # close it
    page.wait_for_timeout(400)
    return items


def choose(page, label, option, scope=MODAL):
    """Pick ``option`` (substring of an item's text) in the select labelled ``label``."""
    _open_select(page, label, scope)
    page.locator(f'.menuable__content__active .v-list-item:has-text("{option}")').first.click()
    page.wait_for_timeout(600)


def choose_source(page, origin, scope=MODAL):
    """The Source select above the picker (``SourceSelect``): ``"gee"`` or ``"file"``."""
    choose(page, FIELD_SOURCE, SOURCE[origin], scope)


def has_select(page, label, scope=MODAL) -> bool:
    return page.locator(f'{scope} .v-select:has(label:text-is("{label}"))').count() > 0


def wait_bands_read(page, ref, *, select_label, one_band_line, timeout=120):
    """Wait until the picked raster's bands are read: the select labelled
    ``select_label`` (two bands or more) or the ``one_band_line`` shows.
    Raises with the form's error when the bands could not be read."""
    def settled():
        text = modal_text(page)
        if BANDS_LOADING in text:
            return False
        return BANDS_FAILED in text or one_band_line in text or has_select(page, select_label)

    wait_for(settled, timeout, f"the bands of {ref}")
    text = modal_text(page)
    assert BANDS_FAILED not in text, f"the bands of {ref}: {text[:300]}"
    return has_select(page, select_label)


def wait_source_ready(page, ref, *, date_only, button="Add source", timeout=180):
    """Wait until the alert-source form can be sent, on a state that shows,
    never on a line gone (the date values' line may not show yet): a
    date-only source once its values are read (the Date encoding select and
    no "Reading the date values…" line), any other format once ``button`` is
    enabled."""
    def ready():
        if date_only:
            return has_select(page, DATE_ENCODING) and DATES_LOADING not in modal_text(page)
        found = modal(page).locator(f'button:has-text("{button}"):visible')
        return found.count() > 0 and found.last.is_enabled()

    wait_for(ready, timeout, f"{ref}: the source form ready to send")


def select_only(page, label, option, scope=MODAL):
    """In the multi-select labelled ``label``, keep only the item whose text is
    ``option`` selected: it is selected first, then the others are cleared, so
    the selection is never empty on the way. The menu is closed again."""
    _open_select(page, label, scope)
    items = page.locator(".menuable__content__active .v-list-item")
    wait_for(lambda: items.count(), 15, f"the {label} menu")
    for keep in (True, False):  # select the one wanted, then clear the rest
        for i in range(items.count()):
            item = items.nth(i)
            wanted = item.inner_text().strip() == option
            selected = item.get_attribute("aria-selected") == "true"
            if wanted == keep and selected != wanted:
                item.click()
                page.wait_for_timeout(300)
    page.locator(f'{scope} .v-select:has(label:text-is("{label}")) .v-select__slot').last.click()  # close it
    page.wait_for_timeout(400)


def type_asset(page, asset_id, scope=MODAL):
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
    mdi-cloud-download-outline (run), mdi-cloud-sync (re-download), mdi-pencil-outline,
    mdi-delete-outline, mdi-radiobox-blank / mdi-radiobox-marked (select)."""
    return row(page, name, scope).locator(f"button:has(.{icon})").first


def row_mode(page, name, mode):
    """Set a Masks card row's Mode select: ``MODE_OFF`` (off in this period),
    "Keep" or "Exclude" (on, in that mode)."""
    row(page, name).locator(".v-select .v-select__slot").first.click()
    page.wait_for_timeout(600)
    page.locator(f'.menuable__content__active .v-list-item:has-text("{mode}")').first.click()
    page.wait_for_timeout(1200)


def row_mode_value(page, name) -> str:
    """What a Masks card row's Mode select reads ("Off", "Keep" or "Exclude"); '' without the row."""
    sel = row(page, name).locator(".v-select .v-select__selection").first
    return sel.inner_text().strip() if sel.count() else ""


_FIT_JS = """r => {
    const box = e => { const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; };
    return {panel: box(r.closest('.right-panel')), row: box(r),
            name_px: Math.round(r.firstElementChild.getBoundingClientRect().width),
            overflow_px: r.scrollWidth - r.clientWidth,
            buttons: [...r.querySelectorAll('button')].map(box)};
}"""


def table_fit(page, name, scope=".right-panel"):
    """How the ProductTable row ``name`` sits in the Workflow panel (spec D4):
    ``{"panel": [left, right], "row": [...], "name_px", "overflow_px",
    "buttons": [[left, right], ...]}``; None without the row."""
    loc = row(page, name, scope)
    return loc.evaluate(_FIT_JS) if loc.count() else None


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
    choose_source(page, origin)
    if origin == "gee":
        type_asset(page, ref)
    else:
        pick_file(page, "GeoTIFF", ref, scope=MODAL)
    # one band is a line (the band is set to it), two or more a Band select
    if wait_bands_read(page, ref, select_label=MASK_BAND, one_band_line=MASK_ONE_BAND):
        choose(page, MASK_BAND, str(band))
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


def analysis_jump_field(page):
    """Block A's jump row, revealed: ``(go_button, number_field)``. The **#**
    button toggles the row, and the number in it is the 1-based one the queue
    counter shows, not the 0-based position in the store."""
    go = page.locator("button:visible", has_text=re.compile(r"^\s*Go\s*$"))
    if not go.count():
        page.locator('button[aria-label="Jump to alert number"]:visible').first.click()
        page.wait_for_timeout(600)
    go = page.locator("button:visible", has_text=re.compile(r"^\s*Go\s*$")).first
    return go, go.locator("xpath=..").locator("input").first


def analysis_go_to(page, position):
    """Jump to ``position``, the 0-based position in the set's store: block A's
    field holds the 1-based number its counter shows, so the Go takes ``+ 1``."""
    go, field = analysis_jump_field(page)
    field.fill(str(position + 1))
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
