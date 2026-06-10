# Analysis tile raw-widget rebuild (Approach B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the Analysis dialog survive close→reopen by rebuilding its layout as a raw ipyvuetify widget tree (`build_analysis_layout(...)` returning a `v.Container`) instead of a `@solara.component` rendered via `render_fixed`+`solara.display`.

**Why:** MapApp's `v-dialog` destroys its subtree on close and rebuilds on reopen. `solara.display`-captured content is NOT recreated on that rebuild (the dialog goes blank — confirmed in the spec's "Outcome of Approach A"). A raw ipyvuetify widget tree is re-viewed from its model on reopen like any ipywidget, so it survives. This is the SAME fix already shipped for the Recipe and Aux dialogs.

**Architecture:** Each Analysis sub-component is currently a `@solara.component` with an identical convertible skeleton: build widgets once (`use_memo([])`), wire handlers once (`use_effect([])`), run workers (`use_task`), react to results (`use_effect` keyed on a value), then `solara.display(card)`. We convert each into a plain `build_X(...) -> widget` function. The parent becomes `build_analysis_layout(...) -> v.Container` that builds `AnalysisWidgets` (unchanged), calls the sub-builders, assembles the two-map + control layout, and registers the per-alert orchestration via `app_state.actual_alert_id.subscribe(...)` instead of a `use_effect`.

**Tech Stack:** Python, ipyvuetify, pysepal `sepalwidgets`/`SepalMap`, `solara.reactive` (used as a plain reactive store, NOT via hooks), `threading` for workers.

**Spec:** docs/superpowers/specs/2026-06-10-analysis-tile-as-dialog-design.md (see "Outcome of Approach A").

---

## THE CONVERSION RECIPE (every task applies this)

Convert a `@solara.component def X(...)` that ends in `solara.display(root)` into a plain `def build_X(...)` that `return root`. Apply these substitutions:

1. **Drop the `@solara.component` decorator.** Rename `X` → `build_X`. Keep the same parameters.
2. **`foo = solara.use_memo(lambda: <expr>, dependencies=[])`** → **`foo = <expr>`** (just build it once; the function runs once now).
3. **`solara.use_effect(_register_handlers, dependencies=[])`** → **call `_register_handlers()` directly** (inline, near the end before `return`). Drop any returned teardown closure — the widget lives for the whole session (no unmount). If `_register_handlers` returned a cleanup, just don't return/call it.
4. **`solara.use_reactive(initial)`** → **`solara.reactive(initial)`** (a module-level/local reactive var works fine outside a component).
5. **`val = solara.lab.use_task(worker, dependencies=...)` + a `solara.use_effect(_apply, dependencies=[task.value])`** → a **daemon thread** started where the task was triggered (a button click, or inside a `.subscribe` callback). The worker does the blocking/EE work, then calls the SAME `_apply(...)` logic at the end (pass the computed result as an argument instead of reading `task.value`). Toggle a button's `.loading`/`.disabled` and surface errors on an `sw.Alert`, exactly like `gui/tile/recipe_tile.py:_on_load_click`/`_run_load` (the canonical precedent — READ IT before doing the worker conversions). Wrap the worker body in `try/except/finally`.
6. **`solara.display(root)`** → **`return root`** (return the outermost widget the component displayed).
7. **`solara.Style(...)`** calls inside a component → move the CSS string to the parent `build_analysis_layout` and inject it ONCE there via the existing pattern, OR (simplest) have `build_analysis_layout` return a `v.Html(tag="style", children=[css])` as the first child of its container. Do NOT call `solara.Style` outside a component (it is a hook). See Task 5 for where the parent's CSS goes.

**Worked example — navigator.py (already read):**

BEFORE (`@solara.component def AlertNavigator(app_state, id_field)`): builds `prev/next/go` buttons via `use_memo([])`, wires clicks via `use_effect(_wire, [])`, builds `nav_card` via `use_memo([])`, then `solara.display(nav_card)`.

AFTER:
```python
def build_navigator(app_state, id_field):
    prev_button = sw.Btn(icon="fa-solid fa-backward-step", color="primary", outlined=True, small=True)
    next_button = sw.Btn(icon="fa-solid fa-forward-step", color="primary", outlined=True, small=True)
    go_button = sw.Btn("Go", color="primary", outlined=True, small=True)

    def on_prev(*_):
        app_state.go_prev()

    def on_next(*_):
        app_state.go_next()

    def on_go(*_):
        try:
            target = int(id_field.v_model)
        except (TypeError, ValueError):
            return
        app_state.go_to(target)

    prev_button.on_event("click", on_prev)
    next_button.on_event("click", on_next)
    go_button.on_event("click", on_go)

    return v.Card(
        class_="pa-2 ma-2 d-flex justify-center",
        hover=True,
        children=[prev_button, next_button, id_field, go_button],
    )
```
(No teardown needed — the `_wire` cleanup closure is dropped.)

**Hard rules for every conversion task:**
- The conversion is BEHAVIOR-PRESERVING. Do not change what handlers do, the widget tree shape, message keys (`cm.*`), or the `AnalysisWidgets` contract. Only change the Solara-hook plumbing into imperative plumbing.
- Keep all module docstrings accurate: update the "Architecture layer" note to say the component is now a raw-widget builder (no `@solara.component`).
- Do NOT touch `gui/tile/analysis/widgets.py` (`AnalysisWidgets`) — it is already a plain class and stays. `widgets.imagery_ctx = solara.reactive(None)` stays (a plain reactive used via `.subscribe`).
- Run `python -c "import ast; ast.parse(open('<file>').read()); print('parse OK')"` after each edit (full import needs EE; AST parse is the cheap check).
- Commit after each task. COMMIT CONVENTION: NO `Co-Authored-By` trailers, NO "Generated with Claude Code" lines.

Work from: `/home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module`
Python: `/home/jserafini/micromamba/envs/defo-alerts/bin/python`

---

### Task B1: Convert navigator.py + review_panel.py to raw builders

Both are trivial (no tasks/reactives; only `use_memo([])` + one `use_effect([])` + `solara.display`).

**Files:**
- Modify: `gui/tile/analysis/navigator.py` — `AlertNavigator` → `build_navigator`
- Modify: `gui/tile/analysis/review_panel.py` — `AlertReviewPanel` → `build_review_panel`

- [ ] **Step 1: Convert `navigator.py`** to exactly the AFTER code in the worked example above (`def build_navigator(app_state, id_field): ... return v.Card(...)`). Remove `import solara`. Update the module docstring's architecture note.

- [ ] **Step 2: Convert `review_panel.py`.** Replace `@solara.component def AlertReviewPanel(widgets, app_state, analyzed_alerts_model, app_tile_model)` with `def build_review_panel(widgets, app_state, analyzed_alerts_model, app_tile_model)`. Build `confirmation_select` and `driver_combobox` inline (drop the `use_memo` wrappers, keep the exact `sw.Select`/`sw.Combobox` kwargs). Replace the `use_effect(_publish, [])` by assigning directly: `widgets.confirmation_select = confirmation_select` and `widgets.driver_combobox = driver_combobox`. Build `card` inline and `return card`. Remove `import solara`.

- [ ] **Step 3: Parse-check both files.**
Run: `cd gui/tile/analysis && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; [ast.parse(open(f).read()) for f in ('navigator.py','review_panel.py')]; print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Commit.**
```bash
git add gui/tile/analysis/navigator.py gui/tile/analysis/review_panel.py
git commit -m "refactor(analysis): navigator + review_panel as raw-widget builders"
```

---

### Task B2: Convert edit_tools.py to a raw builder

`AlertEditTools` uses: `ns = use_memo(_build_widgets, [])`, `pred1_path/pred2_path = use_reactive(None)`, `model1_task/model2_task = solara.lab.use_task(...)` (CNN runs, button-triggered), `use_effect(_register_handlers, [])`, `use_effect`s keyed on each task value (verify/enable the prediction), a `use_effect(_register_defo_observer, [])` (a traitlets observer), `card = use_memo(...)`, `solara.display(card)`.

**Files:**
- Modify: `gui/tile/analysis/edit_tools.py` — `AlertEditTools` → `build_edit_tools`

- [ ] **Step 1: Read the canonical worker precedent** `gui/tile/recipe_tile.py` (the `_on_load_click`/`_run_load` daemon-thread + `try/except/finally` + button `.loading` pattern) and the full current `edit_tools.py`.

- [ ] **Step 2: Apply the conversion recipe.**
  - `@solara.component def AlertEditTools(...)` → `def build_edit_tools(...)` (same params).
  - `ns = use_memo(_build_widgets, [])` → `ns = _build_widgets()`.
  - `pred1_path = use_reactive(None)` / `pred2_path = use_reactive(None)` → `solara.reactive(None)`.
  - The two CNN `use_task`s are button-triggered: convert each to a daemon thread started in the run-model button's click handler. Move the EE/`cnn_worker.apply_dl_model` blocking call into the worker; toggle the relevant button's `.loading`/`.disabled`; surface errors on the tile's `sw.Alert` (use the existing alert widget in `ns` if present, else the pattern from recipe). At the END of the worker (success path), call the SAME logic that the `use_effect` keyed on the task value did (the verify/enable-prediction step), passing the worker's result in — instead of reading `task.value`.
  - `use_effect(_register_handlers, [])` → call `_register_handlers()` directly (drop any teardown).
  - `use_effect(_register_defo_observer, [])` → call `_register_defo_observer()` directly. This registers a traitlets `.observe(...)`; keep that observe call (it is NOT a solara hook). Drop any returned teardown.
  - `card = use_memo(...)` → build inline. `solara.display(card)` → `return card`.
  - Remove now-unused imports (`solara.lab`, etc.) but KEEP `solara` if `solara.reactive` is still used.

- [ ] **Step 3: Parse-check.**
Run: `cd gui/tile/analysis && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('edit_tools.py').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Commit.**
```bash
git add gui/tile/analysis/edit_tools.py
git commit -m "refactor(analysis): edit_tools as a raw-widget builder (threads replace use_task)"
```

If anything about the CNN task→thread conversion is ambiguous (e.g. a `use_effect` that depends on BOTH task values, or shared draw-control state), STOP and report as BLOCKED rather than guessing.

---

### Task B3: Convert export.py to a raw builder

`AlertExport` uses: `ns = use_memo(_build_widgets, [])`, `save_task`/`download_task = solara.lab.use_task(prefer_threaded=True)` (button-triggered), `use_effect`s keyed on task value/state, `use_effect(_register_handlers, [])`, `card = use_memo(...)`, `solara.display(card)`.

**Files:**
- Modify: `gui/tile/analysis/export.py` — `AlertExport` → `build_export`

- [ ] **Step 1: Read the full current `export.py`** (and re-use the recipe worker precedent from Task B2).

- [ ] **Step 2: Apply the conversion recipe** identically to B2: `build_export(...)`; `ns = _build_widgets()`; the save/download `use_task`s → daemon threads started in the Save/Download button click handlers (toggle `.loading`, surface errors/success on the export `sw.Alert`, `try/except/finally`); the `use_effect`s keyed on task value/state → run their apply logic at the end of the corresponding worker; `use_effect(_register_handlers, [])` → call `_register_handlers()` directly; `card` built inline; `return card`.

- [ ] **Step 3: Parse-check.**
Run: `cd gui/tile/analysis && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('export.py').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Commit.**
```bash
git add gui/tile/analysis/export.py
git commit -m "refactor(analysis): export as a raw-widget builder (threads replace use_task)"
```

---

### Task B4: Convert imagery.py to a raw builder

`AlertImagery` is the most involved: a `use_effect(_build_planet, dependencies=[ctx])` (Planet slide-group, runs on each fresh ctx), `s2_task`/`landsat_task = solara.lab.use_task(prefer_threaded=True)` (EE fetches that AUTO-run), and `use_effect(_rebuild_s2, [s2_result])` / `use_effect(_rebuild_landsat, [landsat_result])` that rebuild each source's slide-group from the fetched dict. The driver is `widgets.imagery_ctx` (a `solara.reactive` set by the parent's `view_actual_alert`).

**Files:**
- Modify: `gui/tile/analysis/imagery.py` — `AlertImagery` → `build_imagery`

- [ ] **Step 1: Read the full current `imagery.py`.**

- [ ] **Step 2: Apply the conversion recipe, driven by a `.subscribe` on `widgets.imagery_ctx`** (this replaces the `use_effect(dependencies=[ctx])` model):
  - `@solara.component def AlertImagery(...)` → `def build_imagery(...)` (same params: `widgets, app_state, selected_alerts_model, analyzed_alerts_model`).
  - `build_imagery` does NOT return a card to embed in the right column — it currently has no visible card of its own (it orchestrates the slide-groups inside the map columns the parent already lays out). Confirm by reading whether the component returns/displays anything; if it only runs effects (no `solara.display`), then `build_imagery` registers its subscriptions/handlers and returns `None` (the parent calls it for its side effects). If it DOES `solara.display` something, return that widget.
  - Register one callback on `widgets.imagery_ctx.subscribe(_on_ctx)`; `_on_ctx(ctx)` runs the former `_build_planet` logic synchronously AND starts two daemon threads (S2 + Landsat) that do the EE fetches; each thread, on completion, calls the former `_rebuild_s2(result)` / `_rebuild_landsat(result)` logic (pass the fetched dict in). Show/clear the slide-group spinners exactly as the originals did. Guard `_on_ctx` against `ctx is None`.
  - Because `imagery_ctx` already holds a value if an alert was selected before `build_imagery` runs, also call `_on_ctx(widgets.imagery_ctx.value)` once at the end (mirrors a `use_effect` firing on mount). Guard for `None`.
  - Replace any `use_reactive`/`use_memo`/`use_effect` per the recipe.

- [ ] **Step 3: Parse-check.**
Run: `cd gui/tile/analysis && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('imagery.py').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Commit.**
```bash
git add gui/tile/analysis/imagery.py
git commit -m "refactor(analysis): imagery as a raw-widget builder (ctx.subscribe + threads)"
```

If the slide-group rebuild touches widgets in ways that look unsafe from a thread, note it as DONE_WITH_CONCERNS (ipywidgets trait sets from a daemon thread are how recipe_tile already works, so it is expected to be OK — but flag anything beyond simple trait sets).

---

### Task B5: Convert the parent analysis_tile.py to build_analysis_layout

`AnalysisTile` (`@solara.component`) builds `widgets` + `id_field` via `use_memo([])`, injects CSS via `solara.Style`, runs `use_effect(view_actual_alert, [actual_alert_id])` and `use_effect(_select_first_alert, [alerts_gdf])`, then lays out the two-map column + control column with `solara.display(...)` and the sub-components. `view_actual_alert(...)` (the module-level function at the top) stays as-is — only how it is TRIGGERED changes.

**Files:**
- Modify: `gui/tile/analysis/analysis_tile.py` — `AnalysisTile` → `build_analysis_layout`

- [ ] **Step 1: Convert the parent to a builder returning a raw `v.Container`.**
  - Keep the module-level `view_actual_alert(...)` function and `_ALERT_BB_STYLE` unchanged.
  - `@solara.component def AnalysisTile(...)` → `def build_analysis_layout(...)` (same params: `aux_model, selected_alerts_model, analyzed_alerts_model, app_tile_model, app_state, gee_interface`).
  - `widgets = use_memo(lambda: AnalysisWidgets(gee_interface=gee_interface), [])` → `widgets = AnalysisWidgets(gee_interface=gee_interface)`.
  - `id_field = use_memo(lambda: v.TextField(...), [])` → build inline.
  - `solara.Style("...")` → make the CSS string the first child of the returned container as a raw style element: `v.Html(tag="style", children=[<the same CSS string>])`. Keep the exact CSS (`.custom-map-class2 { height: 55vh !important; } ...`).
  - `use_effect(_run_view_actual_alert, [actual_alert_id.value])` → register `app_state.actual_alert_id.subscribe(lambda *_: view_actual_alert(widgets, app_state, analyzed_alerts_model, app_tile_model, id_field))`. Also call `view_actual_alert(...)` once immediately so a pre-selected alert renders on first build.
  - `use_effect(_select_first_alert, [alerts_gdf.value])` → register `app_state.alerts_gdf.subscribe(_select_first_alert_cb)` where `_select_first_alert_cb(*_)` runs the same body (auto-select index 0 when a gdf first arrives and `actual_alert_id < 0`). Also call it once immediately.
  - The layout: replace the `with solara.Row(...)/solara.Column(...): solara.display(widgets.mapXX)` reacton tree with a raw `v.Row`/`v.Container` tree holding the SAME children in the SAME order. The two-map left side: a column with a row of two 50%-width columns, each holding `widgets.map31`/`widgets.imgSelection1` and `widgets.map32`/`widgets.imgSelection2`. Call `build_imagery(widgets=..., app_state=..., selected_alerts_model=..., analyzed_alerts_model=...)` for its side effects (place its returned widget if it returns one, else just call it). The right control column holds, in order: the Alert-info `v.Card` (built inline, same as today), `build_navigator(app_state, id_field)`, `build_review_panel(...)`, `build_edit_tools(...)`, `build_export(...)`. Use ipyvuetify layout widgets (`v.Row`/`v.Col`/`v.Container` with `style_`/`class_`) to reproduce the current fl/width styling (left flexible, right `flex: 0 0 18rem; overflow:auto`).
  - `return <outermost v.Container>`.
  - Update imports: import the `build_*` names instead of the old component names. Remove `import solara` only if nothing (e.g. `solara.reactive`) still needs it.
  - Update the module docstring to describe the builder (no `@solara.component`, subscriptions instead of effects).

- [ ] **Step 2: Parse-check.**
Run: `cd gui/tile/analysis && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('analysis_tile.py').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 3: Update the package export.** In `gui/tile/analysis/__init__.py`, if it exports `AnalysisTile`, change the export to `build_analysis_layout` (read it first; match its existing style).

- [ ] **Step 4: Commit.**
```bash
git add gui/tile/analysis/analysis_tile.py gui/tile/analysis/__init__.py
git commit -m "refactor(analysis): parent as build_analysis_layout (subscribe replaces use_effect)"
```

---

### Task B6: Wire build_analysis_layout into solara_app.py and verify the reopen gate

**Files:**
- Modify: `gui/solara_app.py` — the `analysis_widget` construction (currently `_render_detached(lambda: AnalysisTile(...))`) and the import.

- [ ] **Step 1: Replace the analysis widget construction.** Find:
```python
    analysis_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: AnalysisTile(
                aux_model=aux_model,
                selected_alerts_model=selected_alerts_model,
                analyzed_alerts_model=analyzed_alerts_model,
                app_tile_model=app_tile_model,
                app_state=app_state,
                gee_interface=gee_interface,
            )
        ),
        dependencies=[],
    )
```
Replace with (the Aux/Recipe pattern — a raw builder, no `_render_detached`):
```python
    # Analysis tile: build the RAW ipyvuetify layout once and hand it straight to
    # MapApp (same as Aux/Recipe). NOT via render_fixed + solara.display: a
    # solara.display-captured widget's view is not recreated when MapApp's
    # v-dialog destroys + rebuilds the dialog DOM on close/reopen, so the cards
    # vanished on the 2nd open. A raw widget tree survives reopen.
    analysis_widget = solara.use_memo(
        lambda: build_analysis_layout(
            aux_model=aux_model,
            selected_alerts_model=selected_alerts_model,
            analyzed_alerts_model=analyzed_alerts_model,
            app_tile_model=app_tile_model,
            app_state=app_state,
            gee_interface=gee_interface,
        ),
        dependencies=[],
    )
```
Update the comment block above it that mentioned `render_fixed`/`view_actual_alert use_effect` to reflect the raw build.

- [ ] **Step 2: Update the import.** Change `from gui.tile.analysis import AnalysisTile` (around line 48) to `from gui.tile.analysis import build_analysis_layout` (match whatever `__init__.py` now exports from Task B5 Step 3).

- [ ] **Step 3: Parse-check.**
Run: `cd gui && /home/jserafini/micromamba/envs/defo-alerts/bin/python -c "import ast; ast.parse(open('solara_app.py').read()); print('parse OK')"`
Expected: `parse OK`

- [ ] **Step 4: Run the full unit suite.**
Run: `cd /home/jserafini/Desktop/projects/deforestation_alerts/deforestation-alerts-module && /home/jserafini/micromamba/envs/defo-alerts/bin/python -m pytest -q`
Expected: all pass (baseline this session: 71 passed). No analysis-specific unit tests exist, but this catches import breakage if anything imports the analysis package.

- [ ] **Step 5: Commit.**
```bash
git add gui/solara_app.py
git commit -m "feat(analysis): hand MapApp the raw Analysis layout so the dialog survives reopen"
```

- [ ] **Step 6: Restart the dev server and run the live reopen gate.**
The running :8900 server hot-reloads, but a structural change like this is safest with a clean restart. Kill the old server BY PID in a SEPARATE call from any relaunch (never pkill a `solara run` pattern in the same command that launches one). Relaunch:
```bash
cd gui && set -a; . ./.env; set +a
/home/jserafini/micromamba/envs/defo-alerts/bin/solara run solara_app.py --port 8900 --no-open
```
(background; first build can take >90s). Then (a) smoke that it opens:
Run: `cd .claude/skills/run-deforestation-alerts && /home/jserafini/micromamba/envs/defo-alerts/bin/python solara_driver.py smoke --mode dialog --step-id 7 --drawer Analysis --expect "Alert info" --slug analysis-b`
Expected: `[driver] PASS`.

  (b) Run the close→reopen check (the same snippet used in the Approach-A gate) against :8900 — open Analysis, close it, open Overview, reopen Analysis, and assert `>= 2` `.dialog-card .leaflet-container` AND `"Alert info"` present on the SECOND open. Inspect `screenshots/analysis-reopen.png`.
Expected: `REOPEN OK: both maps + Alert info present on second open` (the Approach-A run printed `reopen=0`; this must now print `reopen=2`).

- [ ] **Step 7:** If the reopen gate passes, the rebuild is done. If it still fails, STOP and report which widgets survive vs vanish (the snippet prints map + card counts) — a partial vanish points to a remaining `solara.display`/component left in one sub-builder.

---

## Self-Review notes

- **Coverage:** every `@solara.component` in `gui/tile/analysis/` (navigator, review_panel, edit_tools, export, imagery, analysis_tile) is converted (B1-B5); the parent's two `use_effect`s become `.subscribe` (B5); the shell wiring + import change + reopen verification (B6). `widgets.py` is intentionally untouched (already a plain class).
- **Precedent:** the worker-thread + `.subscribe` pattern is exactly `gui/tile/recipe_tile.py` (load/save) and `app_state.recipe_folder_path.subscribe` — cited so implementers copy a shipped pattern, not invent one.
- **No placeholders:** the trivial conversions (B1) have full AFTER code; the worker conversions (B2-B4) reference the complete recipe_tile precedent + give per-file hook inventories so the implementer applies a known transform to code they read in full.
- **Name consistency:** new builder names `build_navigator`, `build_review_panel`, `build_edit_tools`, `build_export`, `build_imagery`, `build_analysis_layout` are used consistently in B5's parent wiring and B6's shell wiring/import.
- **Risk:** threads mutating ipyvuetify/ipyleaflet widgets — accepted (recipe_tile does it); flagged in B2/B4 to report anything beyond simple trait sets.
