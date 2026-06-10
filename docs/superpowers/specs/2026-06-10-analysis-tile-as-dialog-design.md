# Analysis tile as a wide modal dialog (popup)

Date: 2026-06-10
Status: Approved (design)

## Problem

The Analysis tile is the only drawer tile that renders with MapApp `display:"step"`
— an opaque full-page panel (`.step-content-container`). Two issues:

1. **It is a "blank opaque page" rather than a popup.** Every other config/IO tile
   (About, Recipes, Optional files) is a `display:"dialog"` modal over the visible
   map. Analysis should behave the same: a popup over the (dimmed) main map.
2. **A stray "Basemaps" panel bleeds through at the top-left, over the drawer.**
   The shared main map is `SepalMap(fullscreen=True)` → `position:fixed; z-index:800`
   (`solara_app.py` `_make_shared_map`). The `step` panel is lifted to only
   `z-index:801` (`Page()`'s `solara.Style`). The main map's own layer-control menu
   portals above that and shows top-left. This is the bug the user reported.

## Root cause linkage

The bug and the popup request share a fix. A `dialog` renders behind a Vuetify
scrim (~z-index 1000), which **dims and covers the main map and its layer-control
menu**. So converting Analysis from `step` to `dialog` removes the bleed-through as
a side effect — no separate z-index hack needed.

## Approach (chosen: A — minimal flip + verify gate)

Considered three approaches to make the dialog survive close→reopen (MapApp renders
BOTH `step` and `dialog` content behind a Vue `v-if`, so DOM is destroyed on close
and rebuilt on reopen; `solara.display`-captured views are not always recreated —
the documented Recipe/Aux vanish-on-reopen bug):

- **A (chosen):** Flip `display:"step"` → `"dialog"`, give it a wide width, verify a
  live close→reopen. Since `step` *already* uses `v-if`, Analysis either already
  survives reopen today (then dialog is safe) or it is a latent bug the flip does
  not worsen. Escalate only if reopen actually breaks.
- B: Rebuild AnalysisTile + 5 sub-components as raw-widget builders. Most robust,
  large refactor, high regression risk. Rejected as overkill for this ask.
- C: Render Analysis in a separate always-mounted Solara `v.Dialog`. Avoids `v-if`
  but fights MapApp nav/scrim. Rejected.

## Changes

All in `gui/solara_app.py` `Page()`:

1. **`steps_data` Analysis entry (id 7):** change `"display": "step"` →
   `"display": "dialog"`; add `"width"` (a large value; the precise width is set
   via CSS below, but MapApp's dialog branch expects a width — match the pattern of
   the About/Recipes entries). Keep `"right_panel_action": "close"` and the existing
   `"content": analysis_widget`.

2. **CSS in the existing `solara.Style` block:** add a rule targeting the Analysis
   dialog card so it renders near-fullscreen (`~92vw` wide, tall enough for the two
   maps + control column). About's 1300px is too narrow for before/after dual maps.
   Scope the rule so it does NOT affect the other (narrower) dialog tiles — e.g. via
   a dedicated width on the Analysis step entry and/or a targeted selector. Confirm
   the existing `.dialog-card .v-application--wrap { min-height: 0 }` rule does not
   collapse the Analysis maps (the maps set their own height via
   `.custom-map-class2 { height: 55vh }` in `analysis_tile.py`); adjust if it does.

3. **Update the `steps_data` display-type comment** (currently documents `step` =
   "Analysis only") to reflect that Analysis is now a `dialog`.

No changes to `analysis_tile.py` / `widgets.py` unless the verify gate (below) fails.

## Verification (the gate that decides whether A is enough)

Run the app with a recipe loaded (so an alert is selected) and:

1. Open Analysis from the drawer → confirm it opens as a modal popup over the dimmed
   main map, the two before/after maps render, controls render on the right.
2. Confirm the stray top-left "Basemaps" panel is GONE (main map dimmed behind scrim).
3. **Close Analysis, open another tile, reopen Analysis** → confirm the two maps and
   all control cards STILL render (the vanish-on-reopen check). If they vanish,
   escalate: wrap the analysis content in a keep-mounted host toggled with
   `display:none` (panel-tile pattern) or rebuild as a raw widget (Approach B).
4. Smoke: `solara_driver.py smoke --mode dialog --step-id 7 --expect "Alert info"`
   (Analysis is now a dialog, not a step). Confirm existing pytest suite still passes.

## Out of scope

- The "maps don't center on alert" and "No images available" behaviors (separate, not
  this change).
- Any Analysis sub-component (imagery/review/edit/export) behavior changes.
