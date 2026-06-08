# Programmatic tile navigation + per-tile map-layer isolation — Design

- **Date:** 2026-06-10
- **Branch:** `feat/phase4-solara`
- **Status:** Approved (brainstorming) — ready for implementation plan
- **Related:** Phase 4 Solara migration; Task-8 deferred item "programmatic navigation foundation"; single-map re-architecture.

## Problem

In the original Voila app, jumping between tiles programmatically was trivial:
`AppTileModel.current_page_view` was an observed trait, `CustomApp.show_tile_2()`
switched the visible tile, and any code could navigate by assigning
`current_page_view = "analysis_tile"` (e.g. `recipe_helper.py:146,224` after a
recipe load).

In the Solara port this was **deliberately deferred** (`TODO(navigation
foundation)` in `aoi_tile.py`, `alerts_filter_tile.py`, `recipe_tile.py`). The
tiles already set `app_state.current_page_view.value` on every navigation
trigger, but **nothing translates that string into `MapApp.current_step`** — the
`Int` trait that actually drives the active step. So today the value changes and
the UI does not move. Affected triggers:

| Trigger | Sets `current_page_view` | Should land on |
|---|---|---|
| AOI "Search alerts" | `"filter_alerts"` | Filter (step 5) |
| Filter "Analyze" | `"analysis_tile"` | Analysis (step 7) |
| Recipe **Load** | `"analysis_tile"` | Analysis (step 7) |
| Recipe **Create** | `"aux_tile"` | Aux (step 3) |
| Overview "Go" popup | `"analysis_tile"` | Analysis (step 7) |

A second, related problem comes from the single-map re-architecture: the old app
had a **separate map per tile**, so layers were naturally isolated and the map
only needed cleaning when a new recipe was created. Now there is **one shared
map**, so a tile's layers persist on it after you switch tiles — e.g. the
Overview/Filter alert layers stay visible when you land on the AOI tile. Map
cleanup must now happen on every tile switch.

## Goals

1. Restore programmatic tile navigation for **all** triggers that set
   `current_page_view` (full foundation, matching the original).
2. Navigation must correctly handle the right panel and dialogs on each
   transition (the part the existing Vue watcher misses).
3. **Full per-tile map-layer isolation:** each shared-map tile shows only its own
   layers; switching tiles removes the layers of the tile being left, and
   returning re-renders the destination tile's layers from current state.

## Non-goals

- No changes to Analysis's own before/after maps (it uses its own maps behind an
  opaque `display:"step"` panel; out of scope, tracked separately).
- No re-run of the recipe-load EE pipeline (still `TODO(recipe-load)`; orthogonal).
- No change to *which* triggers exist — only wiring the existing ones.

## Current mechanism (verified against pysepal source)

- `nav_model` (a tiny `HasTraits` with `current_step`) is passed to
  `MapApp.element(model=...)`; `MapApp._setup_model_binding()` creates a
  **bidirectional `traitlets.link`** between every matching, non-excluded trait —
  including `current_step` and `step_open` (`vue_app.py:261-293`). `right_panel`,
  `right_panel_content`, `steps_data`, etc. are excluded.
- `MapApp.vue` has a watcher on `current_step` (`MapApp.vue:544-558`) that reacts
  to **Python-driven** changes: it sets `activeStepId` (switching the visible
  content) and, for `display:"dialog"` targets, sets `open_dialog = true`.
- That watcher is a **lite** path. Unlike `activateStep()` (the drawer-click path,
  `MapApp.vue:726-758`), it does **not** apply `right_panel_action` and does
  **not** close an already-open dialog when moving to a non-dialog step. This is
  the "controlled/uncontrolled sync loop" risk the prior session flagged; it is
  resolved by driving the panel/dialog explicitly from Python (below).
- The right panel's open/close lives in the embedded `RightPanel.is_open`
  (`vue_app.py:432-470`). `MapApp.right_panel_open` only drives CSS, so it cannot
  open/close the panel. The supported controls are
  `MapApp.vue_handle_right_panel_action("open"|"close"|"toggle")` (sets
  `RightPanel.is_open`) and setting `MapApp.step_open = False` (the Vue
  `step_open` watcher closes an open dialog). Both require a handle to the
  rendered `MapApp` widget, obtained with `solara.get_widget(element)` (verified
  available).
- Step ids / displays in `solara_app.py`: About 1 (dialog), Recipes 2 (dialog),
  Aux 3 (dialog), AOI 4 (panel), Filter 5 (panel), Overview 6 (panel),
  Analysis 7 (step).

## Design

### Approach

**Centralized bridge (Approach A).** A single, invisible leaf component drives
navigation; the carefully-built tiles keep their existing triggers
(`current_page_view` writes) unchanged. This mirrors the original architecture
(one `CustomApp` observer translated `current_page_view` → visible tile) and
keeps the name↔step mapping in one place.

### Component: `_NavBridge`

A `@solara.component` rendered as a sibling of `MapApp` inside `Page()`. It is a
**leaf** on purpose: it reads `app_state.current_page_view` and thus re-renders on
nav events, but `Page` / `MapApp` / `steps_data` do **not** — they are not
rebuilt. (Reading the reactive in `Page` itself would rebuild `steps_data` and
re-call `MapApp.element` on every navigation, which we avoid.)

Props: `app_state`, `nav_model`, and the `MapApp` element (for
`solara.get_widget`). It renders a hidden no-op widget.

### Single source of truth — name↔step mapping

Module-level in `solara_app.py`:

```
_PAGE_VIEW_TO_STEP = {
    "about_tile": 1, "recipe_tile": 2, "aux_tile": 3, "aoi_tile": 4,
    "filter_alerts": 5, "overview_tile": 6, "analysis_tile": 7,
}
_STEP_TO_PAGE_VIEW = {v: k for k, v in _PAGE_VIEW_TO_STEP.items()}
```

Only 1/3/5/7 are exercised by current triggers; the rest are mapped for
completeness and to back the reverse sync below. `"filter_alerts"` (not
`"filter_tile"`) is intentional — it is the exact string the AOI tile already
sets for step 5.

### Bidirectional sync (correctness-critical) + loop safety

The bridge keeps `current_page_view` a faithful two-way mirror of the active tile:

- **view → step** (programmatic nav): a `use_effect` keyed on
  `current_page_view.value` navigates to the mapped step.
- **step → view** (drawer clicks): the existing `_on_step` observer (already
  watching `nav_model.current_step`) is extended to set
  `current_page_view.value = _STEP_TO_PAGE_VIEW[step_id]`.

**Why the reverse sync is required:** without it, a second "Search alerts" would
assign `current_page_view = "filter_alerts"` — the *same* value it already holds
after the first search — so the effect would not re-fire and nothing would
navigate. Keeping the string synced to the active page (exactly as the original
did) guarantees the value changes between programmatic navigations.

**Loop safety:** the effect early-returns when the mapped target equals the
current step (`if step_id == nav_model.current_step: return`). So a reverse-sync
write that merely reflects the current step produces a no-op effect run, and a
programmatic write that lands on the same step writes nothing back. `traitlets`
links and the Vue watcher also do not re-fire on equal values. No infinite loop.

### Per-transition panel/dialog handling

A pure, unit-testable resolver decides the actions for a target view:

```
_resolve_navigation(view, step_display) -> (step_id, panel_action, close_dialog)
  step_id       = _PAGE_VIEW_TO_STEP[view]            (or None -> no-op)
  display       = step_display[step_id]               ("panel"/"dialog"/"step")
  panel_action  = "open" if display == "panel" else "close"
  close_dialog  = (display != "dialog")
```

The effect applies it to the `MapApp` widget (via `solara.get_widget`), in order:

1. `mapapp.vue_handle_right_panel_action(panel_action)` — open the right panel for
   panel tiles (AOI/Filter/Overview), close it for dialog/step tiles.
2. if `close_dialog`: `mapapp.step_open = False` — closes any lingering dialog
   (e.g. Recipe → Analysis). Verified no-op when no dialog is open (the
   `step_open` watcher only acts when `open_dialog` is true; `step_open` does not
   drive step-panel visibility).
3. `nav_model.current_step = step_id` — the Vue `current_step` watcher switches
   content and opens the dialog for dialog targets.

### Threading

Search/Analyze run in `solara.lab.use_task(prefer_threaded=True)` worker threads;
Recipe Load runs in a daemon thread. They only set the `current_page_view`
reactive (as they already do, at the **end** of the task body, so navigation
fires only on success). Solara marshals the resulting `_NavBridge` re-render and
its `use_effect` onto the main thread, so all widget mutation (panel/dialog/map)
stays on the main thread — honoring the established "no widget mutation in a task
body" rule.

### Per-tile map-layer isolation

The bridge already makes `current_page_view` a faithful "active tile" signal.
Each shared-map tile (AOI 4, Filter 5, Overview 6) gains **one activation
`use_effect`** keyed on `app_state.current_page_view` (plus its own state) that:

- **active** (`view == my_view`) → render its layers from current model/state;
- **inactive** → clear its own named layers.

All three react independently to the same view change, so switching to AOI makes
Filter and Overview clear their layers while AOI renders its own → the map shows
only the AOI selection, with no alert bleed-through. This keeps layer-name
knowledge **inside the owning tile** and never calls `map_.remove_all()`. Effects
are idempotent (clear-by-name / add-by-name), so the `render_fixed` double-mount
is safe.

Each tile reuses existing pieces:

- **Overview** — `_clear_overview_layers(map_)` (already removes only `Popup`s +
  `_COLOR_DICT`-named `GeoData`) and `_sync_overview()` / `add_colored_layers`
  (renders points from `alerts_gdf`). Today `_sync_overview` keys on `alerts_gdf`;
  it gains a dependency on the active view and clears when inactive.
- **Filter** — the `tracked = {"names": [...]}` holder + the existing
  draw routine (`update_tile`-style: removes `tracked["names"]`, re-adds the
  `"AOI"` + alert-raster layers from `available_alerts_raster_list`). Clear =
  remove `tracked["names"]`; render = the existing draw routine.
- **AOI** — the `AoiView` (`_build_aoi_view`) draws the AOI selection layer on the
  shared map. Active → ensure the AOI layer is drawn from `aoi_date_model`;
  inactive → remove the AoiView-managed AOI layer by name. (Exact AoiView
  redraw/remove call to be confirmed in the plan; AoiView manages a named GeoJSON
  layer on the map — the implementation must not assume a redraw method exists
  without verifying it.)

## Data flow (traced)

1. **AOI → Filter (search):** task sets `current_page_view="filter_alerts"` →
   effect: panel stays open (panel→panel), `current_step=5` → Filter content in
   panel. Activation effects: AOI clears, Filter renders, Overview clears.
2. **Filter → Analysis (analyze):** `="analysis_tile"` → effect: panel **closed**,
   `step_open=False`, `current_step=7` → Analysis step shows. All shared-map tiles
   clear (Analysis uses its own maps).
3. **Recipe Load → Analysis:** worker sets `="analysis_tile"` → effect (main
   thread): panel closed, **dialog closed** (`step_open=False`), `current_step=7`.
4. **Recipe Create → Aux:** `="aux_tile"` → effect: dialog stays open
   (dialog→dialog), `current_step=3` → dialog content swaps to Aux.
5. **Overview "Go" → Analysis:** same as (2).
6. **Manual drawer click (any tile):** Vue `activateStep` sets `current_step` →
   `_on_step` reverse-syncs `current_page_view` → activation effects fire so the
   map shows only the clicked tile's layers; the nav effect is a guarded no-op.

## Affected files

- `gui/solara_app.py` — add `_PAGE_VIEW_TO_STEP` / reverse map, `_resolve_navigation`,
  `_NavBridge`; extend `_on_step` with the reverse sync; render `_NavBridge`.
- `gui/tile/overview_tile.py` — activation effect (clear-when-inactive).
- `gui/tile/alerts_filter_tile.py` — activation effect (clear-when-inactive).
- `gui/tile/aoi_tile.py` — activation effect (clear/redraw AOI layer).
- All three tiles + recipe: delete the now-resolved `TODO(navigation foundation)`
  comments (no trigger-logic change).

## Testing strategy

- **Unit (pure):** `_PAGE_VIEW_TO_STEP` round-trips with `_STEP_TO_PAGE_VIEW`;
  `_resolve_navigation` returns the correct `(step_id, panel_action, close_dialog)`
  for every view, including the panel→step, dialog→step, and dialog→dialog cases.
- **Existing suite:** 56 pytest must stay green.
- **Headless smoke** (per `mapapp-solara-tile-gotchas`): drive at least the
  Filter-panel → Analysis-step transition and assert the right panel is closed and
  the step content shows; verify AOI shows no alert layers after a switch.

## Risks / open items

- **AoiView redraw on activation:** AoiView manages its own map layer; the plan
  must confirm the exact remove/redraw call rather than assume one. If AoiView
  cannot cleanly redraw from the model, the AOI clear-when-inactive still works
  and redraw can defer to AoiView's own model observers.
- **`solara.get_widget` timing:** the bridge resolves the `MapApp` widget after
  mount inside the effect; verify it is non-`None` before use and skip gracefully
  if not yet mounted.

## Conventions

- Honor `no-coauthor-commits` (no `Co-Authored-By` trailers).
- Branch `feat/phase4-solara` has uncommitted WIP (single-map re-architecture);
  this work continues on it.
