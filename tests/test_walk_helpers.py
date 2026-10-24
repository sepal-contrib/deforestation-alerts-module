"""The browser walks read their labels from the English catalogue and the
app's own class names. A renamed key or class breaks this test, instead of a
walk timing out twenty minutes in."""

import re
from pathlib import Path

import pytest

from gui.widget.enter_key import ENTER_DEFAULT
from tools.walk import phases, walklib


def test_the_walks_press_the_apps_enter_default():
    assert walklib.ENTER_BUTTON == f"button.{ENTER_DEFAULT}"


def test_the_modal_scope_leaves_the_rail_dialog_out():
    # MapApp's rail dialog is a v-dialog too (content-class "dialog-container").
    assert walklib.MODAL == ".v-dialog--active:not(.dialog-container)"
    assert phases.MODAL is walklib.MODAL


def test_en_reads_the_english_catalogues():
    assert walklib.en("common.cancel") == "Cancel"
    assert walklib.en("steps.alerts.add") == "Add source"
    with pytest.raises(KeyError):
        walklib.en("steps.alerts.no_such_key")
    with pytest.raises(KeyError):
        walklib.en("steps.project.count_sources")  # a plural node, not a text


def test_the_labels_the_walks_pick_exist():
    assert phases.SOURCE_TYPE == "Source type"
    assert phases.MODE_OFF == walklib.en("steps.prepare.mask_mode_off")
    # the mask, attribute and visualization-layer forms: a Source select above the picker
    assert phases.FIELD_SOURCE == walklib.en("steps.raster_input.field_source") == "Source"
    assert phases.SOURCE == {"gee": "Earth Engine", "file": "GeoTIFF"}
    assert phases.MASK_BAND == "Band" and phases.MASK_ONE_BAND == "One band ("
    # the alert-source form: Alert format and the band selects, Date encoding, the one-band line
    assert (phases.ALERT_FORMAT, phases.ALERT_BAND, phases.DATE_BAND) == ("Alert format", "Alert band", "Date band")
    assert phases.DATE_ENCODING == "Date encoding" and phases.ALERT_ONE_BAND == "One band ("
    assert phases.DATES_LOADING == "Reading the date values…"
    assert phases.BANDS_LOADING == walklib.en("steps.raster_input.bands_loading")
    assert phases.BANDS_FAILED == "Could not read the bands: "
    for key in ("steps.project.empty_heading", "steps.project.new", "steps.project.search",
                "steps.project.no_match", "steps.project.name_label", "steps.project.name_chars",
                "steps.integrate.attr_add", "steps.raster_input.field_source", "steps.raster_input.bands_loading",
                "steps.period.new", "steps.period.created"):
        assert walklib.en(key), key


#: The f-string key families the walks build, and every value they fill in.
KEY_FAMILIES = {
    "analysis.timeline.lane_{lane}": ("before", "after"),
    "widgets.product_table.status_{state}": ("ready", "running"),
}


def test_every_label_key_a_walk_names_exists():
    """Every ``en("…")`` literal in tools/walk resolves to an English text,
    including the keys a walk reads lazily inside a phase; an f-string key is
    expanded over its family's values."""
    walk_dir = Path(walklib.__file__).parent
    found = set()
    for path in sorted(walk_dir.glob("*.py")):
        found |= set(re.findall(r'(?<![\w])en\((f?"[^"]+")\)', path.read_text(encoding="utf-8")))
    keys = set()
    for literal in found:
        key = literal.lstrip("f").strip('"')
        if literal.startswith("f") and "{" in key:
            assert key in KEY_FAMILIES, f"add {key!r} and its values to KEY_FAMILIES"
            name = re.search(r"\{(\w+)\}", key).group(1)
            keys |= {key.replace("{" + name + "}", value) for value in KEY_FAMILIES[key]}
        else:
            keys.add(key)
    assert len(keys) >= 25, sorted(keys)  # the scan sees the walks
    missing = []
    for key in sorted(keys):
        try:
            walklib.en(key)
        except KeyError:
            missing.append(key)
    assert not missing, missing


def test_the_walks_prepare_labels_are_the_ones_the_status_cells_show():
    """``prepare_state`` counts the Status cells by their text: ``en_prepare``
    must hold the label of a ready source and mask cell, and its running
    pattern must match every in-flight cell (Task 7)."""
    from deforestation_alerts.engine.fingerprint import Status
    from deforestation_alerts.engine.plan import MaskRow, PrepareRow
    from gui.scripts.prepare_view import mask_state, source_state

    labels = phases.en_prepare()
    source = dict(file_source=False, failed=False, locked=False, on_run=None)
    mask = dict(failed=False, locked=False, on_run=None)
    ready_source = source_state(PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT), stage=None, queued=False,
                                pct=None, **source)
    ready_mask = mask_state(MaskRow("forest", "gee", Status.CURRENT, Status.CURRENT, True, "keep"), stage=None,
                            queued=False, pct=None, **mask)
    assert labels["ready"] == ready_source["label"] == ready_mask["label"]
    row = PrepareRow("GLAD-L", Status.MISSING, Status.MISSING)
    pending = MaskRow("forest", "gee", Status.MISSING, Status.MISSING, True, "keep")
    running = [source_state(row, stage="download", queued=False, pct=12, **source),
               source_state(row, stage="download", queued=False, pct=None, **source),
               source_state(row, stage="harmonize", queued=False, pct=None, **source),
               source_state(row, stage=None, queued=True, pct=None, **source),
               mask_state(pending, stage="download", queued=False, pct=100, **mask),
               mask_state(pending, stage="align", queued=False, pct=None, **mask),
               mask_state(pending, stage=None, queued=True, pct=None, **mask)]
    assert all(labels["running"].fullmatch(cell["label"]) for cell in running), [c["label"] for c in running]
    assert not labels["running"].fullmatch(ready_source["label"])


# ----------------------------------------------------- the band pickers
#: A stand-in for a form's Vuetify multi-select (the attribute form's Bands):
#: the slot toggles the menu, an item toggles its ``aria-selected``.
_BANDS = """<!doctype html><html><body>
<div class="v-dialog--active"><div class="v-select"><label>Bands</label>
  <div class="v-select__slot" onclick="menu()">B1</div></div></div>
<div id="menu">
  <div class="v-list-item" aria-selected="true" onclick="pick(this)"><div class="v-list-item__title">B1</div></div>
  <div class="v-list-item" aria-selected="false" onclick="pick(this)"><div class="v-list-item__title">B11</div></div>
  <div class="v-list-item" aria-selected="true" onclick="pick(this)"><div class="v-list-item__title">B12</div></div>
</div>
<script>
window.picked = [];
function menu() { document.getElementById('menu').classList.toggle('menuable__content__active'); }
function pick(e) {
  e.setAttribute('aria-selected', String(e.getAttribute('aria-selected') !== 'true'));
  window.picked.push([...document.querySelectorAll('[aria-selected="true"]')].length);
}
</script></body></html>"""


@pytest.fixture
def bands_page(_walk_browser):
    page = _walk_browser.new_page()
    page.set_content(_BANDS)
    yield page
    page.close()


def test_select_only_keeps_one_band_of_a_multi_select(bands_page):
    phases.select_only(bands_page, "Bands", "B11")
    selected = bands_page.evaluate("""() => [...document.querySelectorAll('.v-list-item')]
        .filter(e => e.getAttribute('aria-selected') === 'true').map(e => e.innerText.trim())""")
    assert selected == ["B11"]  # "B1" and "B12" are other bands, not prefixes of it
    assert 0 not in bands_page.evaluate("() => window.picked")  # never empty on the way
    assert bands_page.locator(".menuable__content__active").count() == 0  # the menu is closed again


def test_wait_bands_read_tells_a_select_from_a_one_band_line(bands_page):
    assert phases.wait_bands_read(bands_page, "x", select_label="Bands", one_band_line="One band (", timeout=2)
    assert phases.has_select(bands_page, "Bands") and not phases.has_select(bands_page, "Band")
    bands_page.set_content('<div class="v-dialog--active"><p>One band (slope): its statistic is added.</p></div>')
    assert not phases.wait_bands_read(bands_page, "x", select_label="Bands", one_band_line="One band (", timeout=2)
    bands_page.set_content(f'<div class="v-dialog--active"><p>{phases.BANDS_FAILED}boom</p></div>')
    with pytest.raises(AssertionError, match="boom"):
        phases.wait_bands_read(bands_page, "x", select_label="Bands", one_band_line="One band (", timeout=2)


def _source_form(body):
    return f'<div class="v-dialog--active">{body}</div>'


def test_wait_source_ready_waits_for_a_state_that_shows(bands_page):
    encoding = f'<div class="v-select"><label>{phases.DATE_ENCODING}</label></div>'
    # date only: nothing read yet is not ready, though no "Reading…" line shows
    bands_page.set_content(_source_form("<p>One band (d).</p>"))
    with pytest.raises(AssertionError, match="ready to send"):
        phases.wait_source_ready(bands_page, "x", date_only=True, timeout=1)
    bands_page.set_content(_source_form(f"<p>{phases.DATES_LOADING}</p>"))
    with pytest.raises(AssertionError, match="ready to send"):
        phases.wait_source_ready(bands_page, "x", date_only=True, timeout=1)
    bands_page.set_content(_source_form(encoding))
    phases.wait_source_ready(bands_page, "x", date_only=True, timeout=2)
    # any other format: the Add button enabled
    bands_page.set_content(_source_form("<button disabled>Add source</button>"))
    with pytest.raises(AssertionError, match="ready to send"):
        phases.wait_source_ready(bands_page, "x", date_only=False, timeout=1)
    bands_page.set_content(_source_form("<button>Add source</button>"))
    phases.wait_source_ready(bands_page, "x", date_only=False, timeout=2)


# ----------------------------------------------------- the analysis screen
#: A stand-in for the app's two screens: MapApp's wrap (the rail, the
#: stepper in the right panel, the main map) and the analysis screen beside
#: the rail, hidden and shown the way ``build_screen`` and the shell's
#: stylesheet do it: the main map and the right panel hide, the rail stays.
_SCREENS = """<!doctype html><html><head><style>
body:has(.defo-analysis-screen--on) #map-container,
body:has(.defo-analysis-screen--on) .right-panel { display: none; }
</style></head><body>
<div class="v-application"><div class="v-application--wrap">
  <nav class="left-drawer"><div data-step-id="1">About</div></nav>
  <div id="map-container"></div>
  <div class="right-panel">
    <button data-step-key="integrate" onclick="step('integrate')">integrate</button>
    <button data-step-key="questionnaire" onclick="step('questionnaire')">questionnaire</button>
    <button data-step-key="evaluators" onclick="step('evaluators')">evaluators</button>
    <span id="badge">1 / 3</span>
    <p id="showing">workflow on integrate</p>
    <button class="defo-start-analysis" onclick="openScreen()">Start analysis</button>
  </div>
</div></div>
<div class="defo-analysis-screen" id="screen" style="display: none;">
  <button class="defo-back-to-workflow" onclick="closeScreen()">Workflow</button>
  <div class="defo-analysis-right"><p>the review console</p></div>
</div>
<script>
function step(key) {
  document.getElementById('showing').textContent = 'workflow on ' + key;
  document.getElementById('badge').textContent =
    (['integrate', 'questionnaire', 'evaluators'].indexOf(key) + 1) + ' / 3';
}
function openScreen() {
  const s = document.getElementById('screen');
  s.style.display = ''; s.classList.add('defo-analysis-screen--on');
}
function closeScreen() {
  const s = document.getElementById('screen');
  s.classList.remove('defo-analysis-screen--on'); s.style.display = 'none';
}
</script></body></html>"""


@pytest.fixture(scope="module")
def _walk_browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except sync_api.Error as exc:  # no browser installed here
        pw.stop()
        pytest.skip(f"no headless Chromium: {exc}")
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def screens(_walk_browser):
    page = _walk_browser.new_page()
    page.set_content(_SCREENS)
    yield page
    page.close()


def _screen_on(page):
    return page.locator(walklib.SCREEN_ON).count() == 1


def test_the_panel_scope_follows_the_analysis_screen(screens):
    """``PANEL`` is the workflow's right panel, and the analysis screen's
    console while that screen shows: MapApp stays mounted beside the screen,
    its right panel hidden, so a bare ``.right-panel`` would read the hidden
    workflow panel."""
    assert "workflow on integrate" in walklib.panel_text(screens)
    assert screens.locator(f"{walklib.PANEL} p").count() == 1
    screens.evaluate("() => openScreen()")
    assert walklib.panel_text(screens).strip() == "the review console"
    assert screens.locator(f"{walklib.PANEL} p").first.inner_text() == "the review console"


def test_goto_step_reaches_analysis_through_start_analysis(screens):
    walklib.goto_step(screens, "analysis", 100)
    assert _screen_on(screens)
    assert screens.locator("#showing").text_content() == "workflow on evaluators"
    # A workflow step from the analysis screen: back to the workflow first
    # (the stepper is in the right panel, hidden while the screen shows), then the step.
    walklib.goto_step(screens, "integrate", 100)
    assert not _screen_on(screens)
    assert "workflow on integrate" in walklib.panel_text(screens)


def test_back_to_workflow_leaves_the_screen_and_is_a_no_op_elsewhere(screens):
    walklib.back_to_workflow(screens, wait=100)  # on the workflow: nothing to do
    assert not _screen_on(screens)
    screens.evaluate("() => openScreen()")
    walklib.back_to_workflow(screens, wait=100)
    assert not _screen_on(screens)
    assert screens.locator("#screen").evaluate("e => e.style.display") == "none"


def test_prepare_state_counts_the_status_cells_by_their_text(_walk_browser):
    """Ready and in-flight Status cells (their label in a div, as ProductTable
    renders a ``state`` cell), and nothing outside the panel or in a subtitle."""
    page = _walk_browser.new_page()
    try:
        page.set_content("""<div class="right-panel">
          <div><i></i><div>Ready</div></div><div><i></i><div>Ready</div></div>
          <div><i></i><div>Downloading 42%</div></div><div><i></i><div>Aligning</div></div>
          <div><i></i><div>Queued</div></div><div>waiting for the row above</div>
          <button>Align</button></div><div>Ready</div>""")
        assert phases.prepare_state(page) == {"ready": 2, "running": 3}
    finally:
        page.close()
