# pysepal upstream candidates — harvested from the deforestation-alerts migration

**Date:** 2026-06-10
**Source:** the Voila→Solara + pysepal migration of `deforestation-alerts-module` (branch `feat/phase4-solara`, plus `feat/programmatic-tile-nav`).
**Target framework:** `pysepal==3.6.1` (formerly `sepal_ui`). Reference apps: `se.plan`, `spatial-risk-module` (srm).

## Why this document exists

The migration was *maintainability/reliability/performance*, not features. But porting a real app onto pysepal's `MapApp` + Solara surfaced a pile of **framework-level gaps** that the app had to work around in its own code. Each workaround is a candidate to push *up* into pysepal so the next module (and se.plan / srm themselves) doesn't re-discover it. Several non-UI utilities are *already* being copy-pasted between SEPAL apps — the strongest possible signal they belong in the framework.

Every pysepal line reference below was verified against the installed source at
`…/site-packages/pysepal/sepalwidgets/{vue/MapApp.vue,vue_app.py}` and `…/mapping/sepal_map.py`.

Key facts that shape the PRs:
- pysepal 3.6.1 ships **only the legacy `MapApp` (`v.VuetifyTemplate`)**; `MapAppComponent` is documented but **not shipped**. `MapApp.element(...)` is just the generic `ipyvuetify`→reacton bridge, so it carries every gap below unchanged — there is no alternate code path where these are already fixed.
- **No tests ship for `MapApp`** (grep for `MapApp|current_step|activateStep` in pysepal `test_*.py` → nothing). Any fix below can land with the first test for that area.

---

## The chart

Classification: **[FIX]** framework defect, land mostly as-is · **[FEATURE]** new additive API · **[PORT]** move a generic util upstream · **[PATTERN]** ship a helper + docs.

| # | Change we made (app) | pysepal gap it addresses (file:line) | Why integrate into pysepal | Type | Effort / risk | PR |
|---|---|---|---|---|---|----|
| 1 | App CSS `.step-content-container{z-index:801}` (`gui/solara_app.py:173`) | Fullscreen map `z-index:800` paints over step panel `z-index:0` — `MapApp.vue:1054` vs `:901` | Every app using `SepalMap(fullscreen=True)` + a `display:"step"` tile hits this; form tiles look broken | FIX | XS / low | **PR-1** |
| 2 | `_NavBridge` + `gui/store/navigation.py` drive `vue_handle_right_panel_action` + `step_open=False` by hand to jump tiles (`solara_app.py:105-139`, `navigation.py:69-91`) | Programmatic `current_step` is a *lite* watcher: `MapApp.vue:545-558` ignores `right_panel_action` and won't close a lingering dialog, unlike `activateStep` (`:726-758`). No public "activate step" or "step changed" API | Programmatic navigation is broken/asymmetric vs a drawer click; every app re-derives this. A real `activate_step()` + `on_step_change` retires ~150 lines of app glue | FIX + FEATURE | S / low | **PR-2** |
| 3 | (Couldn't open the right panel from Python; had to call the method) | `right_panel_open` trait is **output-only** — set post-init it's a no-op; only `vue_handle_right_panel_action()` works (`vue_app.py:40,318-327,358-360`) | A `sync=True` Bool trait that silently doesn't control its widget is a footgun; make it a real two-way control | FIX | S / low | **PR-3** |
| 4 | Learned by trial that `display:"panel"` renders nowhere; documented the step/dialog rule in a memory note | `steps_data[].display` is an unvalidated `Unicode()` (`vue_app.py:81`); only `"step"`/`"dialog"` render (`MapApp.vue:22`, dialog branch, autoActivate filter `:831,:862`). Anything else → silent drop, no warning | Fail-loud beats a silently-missing tile; cheap to add a validate/warn | FIX | S / low | **PR-4** |
| 5 | Hardened the test driver + app to always close the auto-opened About dialog first | While any `dialog` tile is open, `drawerDisabled` early-returns every drawer click (`MapApp.vue:506-508,728-730,793-795`); no feedback | A modal that silently swallows nav with no cue is a UX trap; let a drawer click dismiss it (or gate the trap behind a flag) | FIX | S / low-med (UX) | **PR-5** |
| 6 | Recipe/Aux dialog tiles authored as **raw ipyvuetify builders** (`build_recipe_layout`, `build_aux_layout`) instead of Solara components | MapApp renders dialog content under `v-if` (`MapApp.vue:263`) → DOM destroyed on close; a `solara.display`-captured widget's view is not recreated → cards vanish on 2nd open | Lets dialog tiles be normal `@solara.component`s; removes a whole authoring constraint | FIX | S-M / low-med | **PR-6** |
| 7 | `_render_detached(thunk)` saves/clears `reacton…container_adders` around `render_fixed` (`solara_app.py:70-102`) | MapApp's step/dialog `content` is a synced `Instance(DOMWidget)` slot, not a reacton child; building a tile Element during render auto-parents a duplicate inline over the map | Every Solara tile handed to MapApp needs this; ship one supported helper instead of each app poking reacton internals | PATTERN | S / low | **PR-7** |
| 8 | Hand-wired single-map: one memoized `SepalMap`, `display:"panel"` tiles, a `panel_host` v.Container whose child is show/hidden, a shadow `HasTraits` nav_model (`solara_app.py:210-220,375,387-422,488-519`) | No first-class "tile renders controls into the docked right panel + layers onto the shared map" support; `right_panel_content` is build-once, one-section, no host/slot (GAP F, `vue_app.py:62-92`) | This is *the* SEPAL single-map app shape (se.plan/srm do it too); make it a supported pattern, not an exploit of an unrendered `display` value | FEATURE | L / low (additive) | **PR-8** |
| 9 | Per-tile layer isolation: each tile adds layers when active, clears its **own** layers by name when inactive, never `remove_all()` (`overview_tile.py:87-103`, `aoi_tile.py:270-293`, `alerts_filter_tile.py:209-218`) | One shared map has no per-owner layer scoping; `SepalMap.remove_all` has `keep_names=` and `remove_layer(name)` but no "layer group/owner" concept (`sepal_map.py:742,762`) | A named layer-group API lets every multi-tile single-map app add/clear a set atomically instead of bookkeeping name lists | FEATURE | M / low | **PR-9** |
| 10 | `use_memo([])` around every imperative pysepal widget; `model.observe(...)` inside `use_effect([])` to bridge traitlets→Solara (pervasive across tiles) | pysepal widgets are imperative ipyvuetify and `pysepal.model.Model` is traitlets — neither participates in Solara's reactive graph | Ship `use_sepal_widget(factory)` + `use_model_trait(model, name)` so model↔view reactivity is declarative (with correct thread marshaling) | PATTERN | M / low | **PR-10** |
| 11 | `solara_driver.py` headless Playwright harness encoding all of MapApp's render quirks (close-dialog-first, `.v-application`-never-visible, `display`→container map, `render_fixed`-dup guard, `SOLARA_TEST` env) | pysepal ships **no** test tooling for a MapApp/Solara app; every module re-learns these quirks | A `pysepal.testing.mapapp_smoke(...)` fixture is arguably the highest-ROI thing this migration produced for pysepal | PATTERN | M / low | **PR-11** |
| 12 | `deforestation_alerts/scripts/geo_utils.py` — UTM/UPS EPSG picker + RAM-bounded `optimal_block_shape` | Header literally says *"copied from spatial-risk-module"*; pysepal has no metric-CRS picker or block sizer | Already copy-pasted between ≥2 SEPAL apps — textbook upstream signal; pure functions, zero coupling | PORT | S / low | **PR-12** |
| 13 | `deforestation_alerts/scripts/raster_export.py` — `download_ee_image()` GEE→GeoTIFF via geedim 2.0 | Header: *"Copied near-verbatim from spatial-risk-module"*; no GEE→local raster helper in pysepal | Same copy-paste signal; national-scale GEE download is generic SEPAL plumbing | PORT | S / low | **PR-13** |
| 14 | `deforestation_alerts/scripts/vectorize.py` — outer fn app-specific, but inner "polygonize categorical raster with size sieve, block-stitched" is generic | pysepal has no raster→vector helper | Pairs with `optimal_block_shape`; common need for any alert/mask app | PORT | M / low | **PR-14** |

App-specific (NOT upstream): `cnn_worker.py` (TensorFlow/HF models), `imagery_service.py` (NICFI/Planet/S2/Landsat domain dicts), `alert_repository.py` (gdf merge keyed on app columns), `state_manager.AppState`, the folder restructure, the CNN-service extraction. The *patterns* (pure-function extraction to kill a thread race; `use_task(prefer_threaded=…)` for GEE vs CPU) are good lessons but not shippable code.

---

## PR plans

Sequenced in three waves. **Wave 1** = surgical correctness fixes, each independently mergeable and individually retires an app workaround. **Wave 2** = additive ergonomics. **Wave 3** = independent utilities/testing that can merge anytime (lowest risk, good first PRs).

### Wave 1 — surgical MapApp/Solara correctness fixes

#### PR-1 — Fix fullscreen-map z-index bleed-through
- **Problem:** `.full-screen-map > .leaflet-container` is `position:fixed; z-index:800` (`MapApp.vue:1054`); the step panel `.step-content-container` is `z-index:0` (`MapApp.vue:901`). The map paints over any form tile. (Note: `.step-content-container.right-panel-open` is already bumped to `z-index:1` at `:914` — so only the *base* rule is wrong.)
- **Change (pysepal):** In `MapApp.vue`, raise the base `.step-content-container` above the map (e.g. `z-index: 810`) and audit the stacking scale so dialog/scrim (`:1005/:1010`) stay on top. One stylesheet edit.
- **Test:** snapshot/DOM test that `.step-content-container` computed z-index > `.full-screen-map` when a `step` tile is active.
- **Backward-compat:** none — fixes a pure visual defect.
- **Retires in app:** the `solara.Style(".step-content-container{z-index:801}")` patch.

#### PR-2 — Programmatic navigation parity: public `activate_step()` + `on_step_change`
- **Problem:** Setting the `current_step` trait fires a *lite* Vue watcher (`MapApp.vue:545-558`) that switches content but does **not** apply `step.right_panel_action` and does **not** close a lingering dialog — unlike `activateStep` (`:726-758`, which calls `handleRightPanelAction` at `:733-735` and forces `open_dialog=false` for non-dialog steps at `:745-746`). There is also no public, observable "active step changed" signal, so the app built a shadow `HasTraits` model just to read it.
- **Change (pysepal):**
  1. Bring the `current_step` watcher to parity with `activateStep` (factor a shared `_applyStep(step)` used by both: apply `right_panel_action`, close dialog for `step`-targets).
  2. Add a Python method `MapApp.activate_step(step_id)` that drives the same path (so callers don't poke a sync trait + manually fix up panel/dialog state).
  3. Expose step changes: either a documented observable `current_step` or an `on_step_change` callback param, so apps don't need a `model=` shadow just to read the active step.
- **Test:** programmatic `activate_step(panel_id)` opens the right panel; `activate_step(step_id)` from an open dialog closes the dialog; `on_step_change` fires with the new id.
- **Backward-compat:** the watcher now applies `right_panel_action` — but that already matches drawer-click behavior, so it's the intended semantics. Keep `current_step`-as-trait working.
- **Retires in app:** `_NavBridge` (`solara_app.py:105-139`), `gui/store/navigation.py` resolver, the `nav_model` shadow `HasTraits`, and the `solara.get_widget(...)` holder dance (`solara_app.py:545-553`).
- **Superseded on 2026-09-03:** the workflow steps no longer are MapApp steps; only the rail entries use `current_step`.

#### PR-3 — Make `right_panel_open` a real (two-way) control trait
- **Problem:** `right_panel_open = Bool().tag(sync=True)` (`vue_app.py:40`) only drives CSS and is *written* from the panel by `_on_right_panel_change` (`:358-360`); setting it from Python after init is a no-op. The only way to open/close is `vue_handle_right_panel_action()` (`:318-327`). It's special-cased exactly once in `__init__` (`:131-132`).
- **Change (pysepal):** Add an observer in `MapApp.__init__` (next to the existing observe block ~`:160`) forwarding `right_panel_open` → `self.right_panel[0].is_open`, guarded against the `_on_right_panel_change` echo (equality check or re-entrancy flag).
- **Test:** `mapapp.right_panel_open = True` opens the panel; toggling back closes it; no infinite echo.
- **Backward-compat:** additive; existing `vue_handle_right_panel_action` path unaffected.
- **Retires in app:** the need to grab the widget handle just to call the method for simple open/close.

#### PR-4 — Validate / warn on unknown `steps_data[].display`
- **Problem:** `display` is an unvalidated `Unicode()` (`vue_app.py:81`); only `"step"`/`"dialog"` ever render. `"panel"`, `""`, or a typo → tile silently rendered nowhere.
- **Change (pysepal):** Normalize/validate `steps_data` in `vue_app.py` (a `@validate` or constructor check) emitting a `warnings.warn`/`console.warn` for any `display` not in the known set; optionally a `v-else` warn branch in `MapApp.vue`. **Warn, don't raise** — an app may rely on the silent drop (this one did, for `"panel"`), so don't break it; once PR-8 lands, `"panel"` becomes a *valid* value.
- **Test:** constructing with `display:"bogus"` warns; `"step"/"dialog"/"panel"` do not.
- **Backward-compat:** warning only.

#### PR-5 — Let a drawer click dismiss an open dialog
- **Problem:** `drawerDisabled` (`MapApp.vue:506-508`) returns `open_dialog`, and `activateStep`/`showMainMap` early-return while it's true (`:728-730,:793-795`), with `.drawer-disabled{pointer-events:none}` (`:1101-1108`). The auto-opened initial dialog (e.g. About at `initial_step`) silently eats every drawer click until closed.
- **Change (pysepal):** Instead of a bare `return`, let a drawer click `closeDialog()` and proceed — or gate the modal trap behind an explicit `step.persistent`/`dismissesDialog` flag so apps opt in.
- **Test:** with a dialog open, clicking another drawer item closes the dialog and activates the target.
- **Backward-compat:** mild — some apps may *want* the trap; the opt-in flag preserves it. Choose during review.
- **Retires in app:** the always-close-About-first hardening in the test driver/app.

#### PR-6 — Keep dialog (and panel) tile content alive across reopen
- **Problem:** dialog content is under `v-if` (`MapApp.vue:263`), so the DOM is destroyed on close. A `solara.display`-captured widget lives in an `Output` whose view is *not* recreated on rebuild → cards vanish on the 2nd open (only reacton-managed buttons survive).
- **Change (pysepal):** render dialog/panel `content` with `v-show` (or a `<keep-alive>`) instead of `v-if`, so the widget DOM/view persists across open/close.
- **Test:** open a dialog tile, close, reopen — `solara.display`-rendered content is still present.
- **Backward-compat:** keeps content mounted (slightly more DOM); negligible. Verify no double-mount side effects for tiles that assumed teardown.
- **Retires in app:** the raw-ipyvuetify-builder requirement — Recipe/Aux can become normal `@solara.component`s; and the panel `display:none`-host kludge (`solara_app.py:387-422`) becomes unnecessary.

#### PR-7 — `pysepal.solara.render_to_widget(component_thunk)` helper
- **Problem:** MapApp's `content` slot is a synced `Instance(DOMWidget)`, not a reacton child. `solara.render_fixed(Tile(...))` during a parent's render auto-parents a *duplicate* of the tile inline (reacton adds every constructed `Element` to the active render context). The app wrote `_render_detached` (`solara_app.py:70-102`) to save/clear `reacton.core.get_render_context().container_adders` around the call and pass a thunk.
- **Change (pysepal):** ship `render_to_widget(component_thunk)` (in `pysepal.solara`) that encapsulates the detach-adders + `render_fixed(make())[0]` dance, returning a standalone widget for any synced widget-slot (MapApp `content`, right-panel content, etc.). Document it as *the* way to put a Solara component into a pysepal widget slot.
- **Test:** rendering a component via the helper during a parent render yields exactly one widget and does not leak a duplicate into the parent's output.
- **Backward-compat:** new helper, additive.
- **Retires in app:** `_render_detached`.

### Wave 2 — additive ergonomics (the "single-map app" shape)

#### PR-8 — First-class right-panel tile hosting + shared-map app shape
- **Problem (GAP F):** The canonical SEPAL shape — one shared `SepalMap`, tiles render their **controls into the docked right panel** and their **layers onto the shared map** — has no first-class support. Today you exploit `display:"panel"` (a value MapApp doesn't render) + a throwaway `content` + `right_panel_action`, and hand-roll a `panel_host` v.Container whose single child you swap, plus a shadow nav model to know which tile is active. `right_panel_content` (`vue_app.py:62-92`) is build-once, single-section, and (per PR-3) can't even be opened by trait.
- **Change (pysepal):** introduce a real **`display:"panel"` tile type** that hosts the tile's `content` widget in the docked right panel (multi-section, keyed by step id, visibility-toggled per PR-6's keep-alive), wired so activating it opens the panel and deactivating closes it. Pair with PR-2's `on_step_change` so no shadow model is needed. This is the natural home for a future `MapAppComponent`/dataclass API if pysepal wants one.
- **Test:** two panel tiles + one step tile; switching shows exactly one panel section, keeps the others alive, opens/closes the panel correctly, never covers the map.
- **Backward-compat:** purely additive (new valid `display` value + new kwargs); existing `right_panel_content` keeps working.
- **Retires in app:** the `display:"panel"` trick, `panel_host` + `_wire_panel_swap` (`solara_app.py:387-422`), and the shadow `nav_model`.

#### PR-9 — `SepalMap` named layer-group / owner API
- **Problem:** With one shared map, tiles must each clear *their own* layers by name on every switch and must never call `remove_all()` (which wipes other tiles' layers + basemap). `remove_all` already has `keep_names=` and `remove_layer(name, none_ok=)` (`sepal_map.py:742,762`) but there's no group concept.
- **Change (pysepal):** add a layer-group/owner API, e.g. `group = map_.layer_group("filter"); group.set([...]) / group.clear()`, so a tile declares a named set and adds/clears it atomically. Implement on top of the existing name-based primitives.
- **Test:** two groups coexist; clearing one leaves the other + basemap intact.
- **Backward-compat:** additive.
- **Retires in app:** `_clear_filter_layers`/`_clear_overview_layers`/`_clear_aoi_layer` name bookkeeping (`alerts_filter_tile.py:209-218`, `overview_tile.py:87-103`, `aoi_tile.py:270-293`).

#### PR-10 — Solara adapters for imperative widgets + traitlets models
- **Problem:** pysepal widgets are imperative ipyvuetify and `pysepal.model.Model` is traitlets — neither is reactive in Solara, so every port wraps widgets in `use_memo([])` and registers `model.observe(...)` inside `use_effect([])` (with manual thread marshaling, since observers fire on worker threads).
- **Change (pysepal):** ship `pysepal.solara` helpers: `use_sepal_widget(factory)` (stable identity, bind-once) and `use_model_trait(model, "trait")` returning a Solara-reactive view of a traitlets trait with cleanup + main-thread marshaling handled.
- **Test:** mutating a `Model` trait re-renders a component reading it via `use_model_trait`; a widget from `use_sepal_widget` keeps identity across re-renders; observers are torn down on unmount.
- **Backward-compat:** additive helpers.
- **Retires in app:** the pervasive hand-rolled `use_memo([])` + `observe`-in-`use_effect` bridges.

### Wave 3 — independent utilities & test tooling (lowest risk; good first merges)

#### PR-11 — `pysepal.testing.mapapp_smoke(...)` headless render harness
- **Problem:** pysepal ships no test tooling for a MapApp/Solara app. The driver (`.claude/skills/run-deforestation-alerts/solara_driver.py`) had to encode: wait on `[data-step-id=N]` (not `.v-application`, which never becomes "visible"); close the auto-open dialog first (else drawer clicks are silent no-ops); map `display`→container (`.dialog-card`/`.right-panel`/`.step-content-container`); assert *inside* the live container to dodge the `render_fixed` duplicate false-positive; tolerate the benign `[Vue warn] <v-main>`; inject `SOLARA_TEST`/`LOCAL_SEPAL_*` for `session_manager`.
- **Change (pysepal):** extract a generic `mapapp_smoke(app_path, step_id, mode, expect)` pytest fixture/plugin under `pysepal.testing`, parameterizing the app-specific bits (port, expected text, env path). Bundles all the hard-won quirk knowledge so any module can assert "shell + tile N renders."
- **Test:** runs against pysepal's own template app.
- **Backward-compat:** new module.
- **Retires in app:** ~70% of `solara_driver.py`.

#### PR-12 — Upstream `geo_utils` (UTM/UPS picker + `optimal_block_shape`)
- **Problem:** `geo_utils.py` header: *"copied from spatial-risk-module."* Pure functions (UTM/UPS EPSG from lon/lat incl. Norway/Svalbard exceptions; RAM-bounded block rows), zero GEE/UI coupling, already duplicated across apps.
- **Change (pysepal):** add to a `pysepal.geo`/`pysepal.mapping` util module (credit srm as origin). Straight port + docstrings + tests.
- **Test:** known lon/lat → expected EPSG (incl. the polar/Norway edge cases); block-shape respects a RAM bound.
- **Backward-compat:** new functions.
- **Retires in app:** `deforestation_alerts/scripts/geo_utils.py` (app imports from pysepal).

#### PR-13 — Upstream `download_ee_image` (GEE→GeoTIFF via geedim 2.0)
- **Problem:** `raster_export.py` header: *"Copied near-verbatim from spatial-risk-module."* Generic GEE image → local GeoTIFF with auto-tiling and `unmask_value`/`nodata` handling.
- **Change (pysepal):** add to a `pysepal.gee`/scripts surface (credit srm). The app's only special convention (`unmask_value=255`) stays a parameter.
- **Test:** a small `ee.Image` exports to a readable GeoTIFF with expected nodata (Tier-2/EE-gated, mark accordingly).
- **Backward-compat:** new function; depends on `geedim>=2.0` (already pulled in this env — note the `ee` version interplay flagged in the migration).
- **Retires in app:** `deforestation_alerts/scripts/raster_export.py`.

#### PR-14 — `polygonize_with_sieve` raster→vector core
- **Problem:** `vectorize.py`'s outer `vectorize_alert_raster` is app-specific (2-band alert schema), but its inner capability — *polygonize a single-band categorical raster with a connected-component size sieve, block-read and edge-stitched for large rasters* — is framework-grade and pairs with `optimal_block_shape`.
- **Change (pysepal):** extract `polygonize_with_sieve(raster, min_size, connectivity)` + the block `_stitch` helper into the same util surface as PR-12/13. Keep the alert-shaped wrapper in the app.
- **Test:** a synthetic categorical raster polygonizes with small clusters sieved out; block-stitched result equals the whole-raster result.
- **Backward-compat:** new function.
- **Retires in app:** the generic core of `vectorize.py` (wrapper stays).

---

## Suggested execution order

1. **Open PR-12, PR-13, PR-14, PR-11 first** — independent, low-risk, no MapApp behavior change; easy reviews that also build credibility with maintainers.
2. **Then Wave 1 (PR-1…PR-7)** — each small, each retires a named app workaround; PR-2 is the highest-value single fix.
3. **Then Wave 2 (PR-8…PR-10)** — additive ergonomics; PR-8 depends conceptually on PR-2/PR-3/PR-6.

For each PR, the app branch can immediately **dogfood** the upstreamed version and delete its workaround — that diff is the PR's proof it works.

## Open questions before filing

- Confirm the contribution target/process for pysepal (issue-first? a fork + PR against `openforis/pysepal`? CLA?).
- PR-12/13/14 originate from **spatial-risk-module**, not this app — coordinate so srm is credited as origin (or filed by an srm maintainer).
- PR-5 and PR-8 touch dialog/panel UX — agree the backward-compat stance (opt-in flag vs. behavior change) with maintainers up front.
