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
    assert phases.RASTER_SOURCE == walklib.en("steps.raster_input.origin_label")
    assert phases.ORIGIN == {"gee": "Earth Engine asset", "file": "GeoTIFF in my SEPAL folder"}
    assert phases.MODE_OFF == walklib.en("steps.prepare.mask_mode_off")
    for key in ("steps.project.empty_heading", "steps.project.new", "steps.project.search",
                "steps.project.no_match", "steps.project.name_label", "steps.project.name_chars",
                "steps.integrate.attr_add", "steps.integrate.attr_load", "steps.integrate.attr_loaded",
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


def test_prepare_state_counts_the_stages_of_a_compact_mask_status():
    """A Masks row shows one compact status icon (Task 8): its stages are in
    its tooltip, so the walks' "ready" count reads them there."""
    from types import SimpleNamespace

    from deforestation_alerts.engine.fingerprint import Status
    from gui.tile.masks_card import mask_status

    gee = mask_status(SimpleNamespace(raw=Status.CURRENT, aligned=Status.CURRENT), busy=False)["title"]
    tif = mask_status(SimpleNamespace(raw=None, aligned=Status.CURRENT), busy=False)["title"]
    stale = mask_status(SimpleNamespace(raw=Status.CURRENT, aligned=Status.STALE), busy=False)["title"]
    busy = mask_status(SimpleNamespace(raw=Status.CURRENT, aligned=Status.MISSING), busy=True)["title"]
    ready, running = walklib.en("widgets.product_table.status_ready"), walklib.en("widgets.product_table.status_running")
    assert phases.compact_stages([gee, tif, stale, "Prepare: ready to go"], ready) == 4
    assert phases.compact_stages([busy], running) == 2
    assert phases.compact_stages([gee], running) == 0


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
    <p id="showing">workflow on integrate</p>
    <button class="defo-start-analysis" onclick="openScreen()">Start analysis</button>
  </div>
</div></div>
<div class="defo-analysis-screen" id="screen" style="display: none;">
  <button class="defo-back-to-workflow" onclick="closeScreen()">Workflow</button>
  <div class="defo-analysis-right"><p>the review console</p></div>
</div>
<script>
function step(key) { document.getElementById('showing').textContent = 'workflow on ' + key; }
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
    assert screens.locator("#showing").text_content() == "workflow on questionnaire"
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
