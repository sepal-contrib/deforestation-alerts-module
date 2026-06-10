# Analysis tile as a wide modal dialog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Analysis tile open as a wide modal dialog (popup) over the dimmed main map instead of an opaque full "step" page, which also removes the stray top-left "Basemaps" layer-control panel that bleeds through the opaque page.

**Architecture:** Analysis is the only `display:"step"` tile in `gui/solara_app.py`'s `steps_data`; every other config/IO tile (About, Recipes, Optional files) is already `display:"dialog"`. We flip Analysis to `"dialog"` and size it with percentage strings (`width:"95%"`, `height:"90%"`) that MapApp.vue's `dialogWidthComputed`/`dialogHeightComputed` consume (capped to viewport minus drawer) — so no global CSS is needed and other dialogs keep their widths. The dialog scrim (~z-index 1000) dims/covers the shared `fullscreen` main map (z-index 800), hiding its layer-control bleed-through. The navigation resolver (`gui/store/navigation.py`) is already display-driven, so programmatic nav (Filter "analyze" / recipe-load → Analysis) will *open the dialog* automatically once the display type changes; only the hardcoded nav tests need updating.

**Tech Stack:** Python, Solara, pysepal `MapApp.element` (Vue), ipyvuetify, pytest, Playwright smoke driver.

**Spec:** docs/superpowers/specs/2026-06-10-analysis-tile-as-dialog-design.md

**Known risk (verify gate):** MapApp's `v-dialog` has no `eager`, so it DESTROYS dialog content on close and rebuilds on reopen. AnalysisTile builds its content with `solara.display(...)` of raw widgets; per the documented Recipe/Aux vanish-on-reopen bug, those views may not be recreated. Task 4 verifies a live close→reopen and defines the escalation if it breaks. (`step` already uses `v-if` too, so the flip does not make reopen worse than it is today.)

---

### Task 1: Encode the new navigation contract (Analysis = dialog) in the unit tests

The resolver in `gui/store/navigation.py` is already generic (it reads a `step_display` dict), so no production code changes here — but `tests/test_navigation.py` hardcodes Analysis as a `"step"` and asserts step-like behavior (dismiss any dialog). After the flip, navigating to Analysis must *open/keep* its dialog (`close_dialog == False`). This task is a real red→green for the resolver against the new layout.

**Files:**
- Modify: `tests/test_navigation.py` (comment at lines 18-19; fixture at line 20; `test_resolve_step_target_closes_panel_and_dialog` at lines 57-62; `test_apply_step_target_closes_panel_and_dialog` at lines 112-117)

- [ ] **Step 1: Rewrite the two Analysis tests to expect dialog behavior (do NOT change the fixture yet)**

Replace `test_resolve_step_target_closes_panel_and_dialog` (lines 57-62) with:

```python
def test_resolve_analysis_dialog_target_keeps_dialog_open():
    # Filter "analyze" lands on Analysis (step 7, now a wide modal dialog): the
    # dialog must open/stay (close_dialog False) and the right panel must close.
    action = resolve_navigation("analysis_tile", STEP_DISPLAY)
    assert action.step_id == 7
    assert action.panel_action == "close"
    assert action.close_dialog is False
```

Replace `test_apply_step_target_closes_panel_and_dialog` (lines 112-117) with:

```python
def test_apply_analysis_dialog_target_leaves_dialog_open():
    mapapp, nav = _FakeMapApp(), _FakeNav()
    apply_navigation(mapapp, nav, resolve_navigation("analysis_tile", STEP_DISPLAY))
    assert mapapp.panel_calls == ["close"]
    assert mapapp.step_open is True  # close_dialog False -> dialog left to open
    assert nav.current_step == 7
```

- [ ] **Step 2: Run the two tests to verify they FAIL (fixture still says step 7 = "step")**

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module && /home/jserafini/micromamba/envs/defo-alerts/bin/python -m pytest tests/test_navigation.py -k analysis_dialog -v`
Expected: FAIL — both assert `close_dialog is False` / `step_open is True`, but with `STEP_DISPLAY[7] == "step"` the resolver returns `close_dialog=True` (so `step_open` is cleared to `False`).

- [ ] **Step 3: Flip the fixture + comment to the new layout**

Change line 20 from:

```python
STEP_DISPLAY = {1: "dialog", 2: "dialog", 3: "dialog", 4: "panel", 5: "panel", 6: "panel", 7: "step"}
```

to:

```python
STEP_DISPLAY = {1: "dialog", 2: "dialog", 3: "dialog", 4: "panel", 5: "panel", 6: "panel", 7: "dialog"}
```

Change the comment at lines 18-19 from:

```python
# The real app layout (solara_app.py steps_data): About/Recipes/Aux are dialogs,
# AOI/Filter/Overview are right-panel tiles, Analysis is a full step.
```

to:

```python
# The real app layout (solara_app.py steps_data): About/Recipes/Aux AND Analysis
# are dialogs, AOI/Filter/Overview are right-panel tiles. (Analysis is the wide
# before/after dual-map review popup; it used to be a full "step" page.)
```

- [ ] **Step 4: Run the navigation tests to verify they PASS**

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module && /home/jserafini/micromamba/envs/defo-alerts/bin/python -m pytest tests/test_navigation.py -v`
Expected: PASS (all navigation tests, including the two renamed ones).

- [ ] **Step 5: Commit**

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module
git add tests/test_navigation.py
git commit -m "test(nav): Analysis is now a dialog target, not a step"
```

---

### Task 2: Flip the Analysis tile to a wide modal dialog (the production change)

**Files:**
- Modify: `gui/solara_app.py` (the Analysis entry in `steps_data`, id 7, currently lines 482-489; the display-type comment block currently lines 414-421)

- [ ] **Step 1: Change the Analysis `steps_data` entry to a dialog with percentage sizing**

Replace the Analysis entry (currently lines 482-489):

```python
        {
            "id": 7,
            "name": cm.app.drawer_item.analysis,
            "icon": "mdi-image-search",
            "display": "step",
            "content": analysis_widget,
            "right_panel_action": "close",
        },
```

with:

```python
        {
            "id": 7,
            "name": cm.app.drawer_item.analysis,
            "icon": "mdi-image-search",
            # Wide modal dialog (popup) over the dimmed main map -- NOT an opaque
            # "step" full page. width/height are percentage strings consumed by
            # MapApp.vue's dialogWidthComputed/dialogHeightComputed (capped to the
            # viewport minus the drawer), so the before/after dual-map layout gets
            # near-fullscreen room while the dialog scrim hides the shared main
            # map + its layer-control menu (was bleeding through at top-left).
            "display": "dialog",
            "width": "95%",
            "height": "90%",
            "content": analysis_widget,
            "right_panel_action": "close",
        },
```

- [ ] **Step 2: Update the display-type comment block to reflect that Analysis is now a dialog**

Replace the comment block (currently lines 414-421):

```python
    # display types under the single-map model:
    #   "dialog" → modal popup over the visible map (About, Recipes, Optional
    #              files — occasional config/IO forms; do NOT hide the map behind
    #              an opaque full panel).
    #   "panel"  → right-panel card over the visible+interactive map (AOI, Filter,
    #              Overview — the map-interactive workflow tiles).
    #   "step"   → opaque full panel (Analysis only — it owns its before/after maps
    #              inside, handled separately).
```

with:

```python
    # display types under the single-map model:
    #   "dialog" → modal popup over the (dimmed) main map. Config/IO forms (About,
    #              Recipes, Optional files) AND Analysis — the wide before/after
    #              dual-map review popup (width/height "95%"/"90%"). The dialog
    #              scrim hides the shared main map + its layer-control menu, so
    #              nothing bleeds through.
    #   "panel"  → right-panel card over the visible+interactive map (AOI, Filter,
    #              Overview — the map-interactive workflow tiles).
    #   "step"   → opaque full panel. No tile uses this now; the lifted-z-index
    #              rule in Page()'s solara.Style stays for any future step tile.
```

- [ ] **Step 3: Verify the module still imports (syntax/wiring check)**

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/gui && set -a; . ./.env; set +a && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('solara_app.py').read()); print('parse OK')"`
Expected: `parse OK` (a full import triggers EE/server setup; an AST parse confirms the edit is syntactically valid without that cost).

- [ ] **Step 4: Commit**

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module
git add gui/solara_app.py
git commit -m "feat(analysis): render Analysis as a wide modal dialog, not an opaque step page"
```

---

### Task 3: Let the smoke driver open a non-About dialog tile

The driver's `dialog` mode asserts against whatever dialog is already open. About auto-opens on load and disables the drawer, so `--mode dialog --step-id 7` currently never opens Analysis (it checks About instead). This adds a minimal close-then-click for an explicit `--step-id`, preserving the no-arg About behavior.

**Files:**
- Modify: `.claude/skills/run-deforestation-alerts/solara_driver.py` (the `if mode == "dialog":` branch, currently lines 223-232)

- [ ] **Step 1: Update the dialog branch to close About and click the target when a step id is given**

Replace the `if mode == "dialog":` branch (currently lines 223-232):

```python
                if mode == "dialog":
                    # The target IS a dialog tile. About auto-opens as the
                    # initial step; only click to open it if it isn't already
                    # showing (clicking an item while a dialog is open is a
                    # no-op anyway — the drawer is disabled).
                    dc = page.locator(".dialog-card")
                    if not (dc.count() and dc.first.is_visible()):
                        _click_step(page, step_id, drawer)
                        page.wait_for_timeout(2_000)
                    container = page.locator(".dialog-card")
```

with:

```python
                if mode == "dialog":
                    # The target IS a dialog tile. About auto-opens as the initial
                    # step and DISABLES the drawer. With an explicit --step-id we
                    # are testing ANOTHER dialog tile (e.g. Analysis, step 7): close
                    # About first, then click the target step. With no --step-id,
                    # assert against the already-open About dialog (the no-arg smoke).
                    if step_id:
                        _close_open_dialog(page)
                        _click_step(page, step_id, drawer)
                        page.wait_for_timeout(2_000)
                    else:
                        dc = page.locator(".dialog-card")
                        if not (dc.count() and dc.first.is_visible()):
                            _click_step(page, step_id, drawer)
                            page.wait_for_timeout(2_000)
                    container = page.locator(".dialog-card")
```

- [ ] **Step 2: Verify the no-arg About smoke still passes (regression guard)**

Start a server warmed with the env (kill any stale one BY PID in a separate call first; never pkill `solara run` in the same command that launches it):

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/gui
set -a; . ./.env; set +a
/home/jserafini/micromamba/envs/defo-alerts/bin/solara run solara_app.py --port 8900 --no-open
```
(run in background; first cold build can take >90s.) Then in another call:

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/.claude/skills/run-deforestation-alerts && /home/jserafini/micromamba/envs/defo-alerts/bin/python solara_driver.py smoke`
Expected: `[driver] PASS` — About dialog still found (default `--expect "RAdar for Detecting Deforestation"`).

- [ ] **Step 3: Commit**

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module
git add .claude/skills/run-deforestation-alerts/solara_driver.py
git commit -m "test(driver): support opening a non-About dialog tile by --step-id"
```

---

### Task 4: Verify the Analysis dialog renders, fixes the bleed-through, and survives reopen

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit-test suite (must stay green)**

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module && /home/jserafini/micromamba/envs/defo-alerts/bin/python -m pytest -q`
Expected: all tests pass (the pre-change baseline is "56 pytest pass" per project notes; this change only edits `test_navigation.py`, so the count stays the same).

- [ ] **Step 2: Smoke the Analysis dialog opens as a popup with its content**

With the env-warmed server from Task 3 Step 2 running on :8900, run:

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/.claude/skills/run-deforestation-alerts && /home/jserafini/micromamba/envs/defo-alerts/bin/python solara_driver.py smoke --mode dialog --step-id 7 --drawer Analysis --expect "Alert info" --slug analysis-dialog`
Expected: `[driver] PASS: ... 'Analysis' tile rendered` and the phrase `Alert info` found inside the live `.dialog-card`. Inspect the saved screenshot `screenshots/analysis-dialog-02-tile.png`: the tile is a centered modal card over a dimmed map, NOT a flat full page, and there is NO stray "Basemaps" panel pinned to the top-left over the drawer.

- [ ] **Step 3: Verify maps with a real alert + the bleed-through fix (dev recipe injection)**

The headless smoke loads zero alerts, so the two maps sit at world zoom and the layer-control bleed-through is best confirmed with data. Launch a server with the documented dev injection so an alert is selected, then re-open Analysis:

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/gui
set -a; . ./.env; set +a
DEFO_DEV_LOAD_RECIPE=/home/jserafini/Desktop/projects/deforestation_alerts/recipe_2026-02-03-113331 \
  /home/jserafini/micromamba/envs/defo-alerts/bin/solara run solara_app.py --port 8901 --no-open
```
(requires the temporary `DEFO_DEV_LOAD_RECIPE` `use_effect` in `Page()` documented in the project notes; if it is not present, add it temporarily and REMOVE it after this step.) Drive it:

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/.claude/skills/run-deforestation-alerts && /home/jserafini/micromamba/envs/defo-alerts/bin/python solara_driver.py smoke --mode dialog --step-id 7 --drawer Analysis --expect "Alert info" --slug analysis-dialog-data --port 8901 --keep`
Expected: `[driver] PASS`. Inspect `screenshots/analysis-dialog-data-02-tile.png`: the two maps render inside the popup, and there is NO stray top-left "Basemaps" panel. (The before/after maps each still have their OWN layer-control button inside the popup — that is expected; only the detached top-left one over the drawer must be gone.)

- [ ] **Step 4: Verify close → reopen survival (THE GATE for Approach A)**

With the `--keep` server from Step 3 still on :8901, drive a reopen with a short Playwright check (the driver leaves the browser closed, so use this inline snippet):

```bash
cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module/.claude/skills/run-deforestation-alerts
/home/jserafini/micromamba/envs/defo-alerts/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto("http://localhost:8901", wait_until="networkidle")
    pg.wait_for_timeout(8000)
    # close auto-opened About
    btn = pg.locator(".dialog-card .v-card__title button")
    if btn.count():
        btn.first.click(); pg.wait_for_timeout(1000)
    # open Analysis (step 7)
    pg.locator('[data-step-id="7"]').first.click(force=True); pg.wait_for_timeout(3000)
    pg.locator(".dialog-card").first.wait_for(state="visible", timeout=20000)
    first = pg.locator(".dialog-card .leaflet-container").count()
    # close the dialog
    pg.locator(".dialog-card .v-card__title button").first.click(); pg.wait_for_timeout(1500)
    # open Overview (step 6, a panel), then reopen Analysis
    pg.locator('[data-step-id="6"]').first.click(force=True); pg.wait_for_timeout(1500)
    pg.locator('[data-step-id="7"]').first.click(force=True); pg.wait_for_timeout(3000)
    pg.locator(".dialog-card").first.wait_for(state="visible", timeout=20000)
    second = pg.locator(".dialog-card .leaflet-container").count()
    pg.screenshot(path="screenshots/analysis-reopen.png")
    print(f"leaflet maps in dialog: first_open={first} reopen={second}")
    assert second >= 2, f"maps vanished on reopen (first={first}, reopen={second})"
    print("REOPEN OK: both maps present on second open")
    b.close()
PY
```
Expected: `REOPEN OK: both maps present on second open` (`reopen=2`). Inspect `screenshots/analysis-reopen.png`: both maps + the right-hand control cards (Alert info, navigator, review, edit, export) are present on the second open.

**If this FAILS (maps/cards vanish on reopen — `reopen < 2`), escalate per the spec:** wrap `analysis_widget` in a keep-mounted host toggled with `style_="display:none"` (the panel-tile pattern from `panel_wrappers`) instead of relying on MapApp's `v-if`, OR rebuild AnalysisTile as a raw-widget builder (spec Approach B). Stop and report the failure with the screenshot before implementing the escalation.

- [ ] **Step 5: Clean up the dev injection (if added) and stop servers**

If a temporary `DEFO_DEV_LOAD_RECIPE` `use_effect` was added in Step 3, remove it now and confirm `git diff gui/solara_app.py` shows only the Task 2 changes. Stop the background servers by PID.

Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module && git diff --stat`
Expected: no uncommitted changes to `gui/solara_app.py` beyond what Task 2 committed.

---

## Self-Review notes

- **Spec coverage:** popup flip (Task 2), percentage sizing/no-CSS-hack (Task 2 Step 1), bleed-through fix via scrim (verified Task 4 Steps 2-3), reopen verify gate + escalation (Task 4 Step 4), nav ripple (Task 1), smoke for the new dialog (Task 3 enables it, Task 4 Step 2 runs it), full suite green (Task 4 Step 1). Out-of-scope items (centering, imagery) are not touched.
- **No production change to `navigation.py`** is needed — confirmed it reads `step_display` generically; only the test fixture/asserts change.
- **Method/name consistency:** `_close_open_dialog`, `_click_step`, `resolve_navigation`, `apply_navigation`, `STEP_DISPLAY`, `data-step-id`, `.dialog-card`, `.step-content-container` all match the existing code read during planning.
