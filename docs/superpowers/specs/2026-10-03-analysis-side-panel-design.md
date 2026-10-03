# Analysis side panel as a review-queue console — design

**Date:** 2026-10-03
**Branch:** `feat/alert-monitoring-slices2-6`
**Replaces:** the six stacked cards of the Analysis right panel
**Mockups:** Design canvas "Analysis Panel Redesign" <https://claude.ai/artifact/41BwTTPpDGBNEe9jdgVeaU>, boards *Today — 6 stacked cards*, *Proposed — review state* (`Main.dc.html`), *Proposed — refine open* (`Refine.dc.html`)
**Sibling:** the imagery half of the same canvas shipped as `docs/superpowers/specs/2026-10-02-shared-image-timeline-design.md` (commit `26a5c7a`)

## 1. Problem

Reviewing an alert set means repeating one loop hundreds of times: look at the
two panes, answer *is this a true alert*, pick the loss driver, save, move on.
The panel that drives that loop is six equal-weight cards, every one of them
expanded all the time — alert info, navigator, questionnaire, drawing tools with
two deep-learning toolbars, and save/export with a report-template field.

Four things follow from that shape.

**The loop is split across a scroll.** *Next* lives in the navigator card near
the top; *Save* lives in the export card at the bottom, roughly 700px below it.
Every single alert costs a scroll down to save and a scroll up to advance, and
the two halves are never on screen together.

**Occasional work has the same weight as the work done every time.** The drawing
tools and both CNN toolbars are permanently expanded although most alerts are
confirmed or rejected without drawing anything; the report-template field — set
once per project — sits in the same card as the save button pressed on every
alert.

**There is no sense of position.** The panel shows one bare id field. Nothing
says which alert of how many this is, how many have been reviewed, or how much
is left, although `AlertSet.n_reviewed` and the `status` column already carry it.

**The facts are a run of bold labels.** `Source: … First Date: … Last Date: …
Status: …` as one `<div>` of `<strong>` runs and text nodes, with the status as
the word "Reviewed" — which collapses Confirmed, False Positive and Need
revision into one, although the app already has a colour and a label for each.

**The verdict is a dropdown.** Three mutually exclusive answers behind a select
that must be opened to be read, defaulting to `Confirmed` — a default that is
saved if the reviewer never touches it.

## 2. The design

The panel becomes four blocks. Everything stays a raw ipyvuetify builder; the
block structure is the change, not the technology.

| Block | Shown | Holds |
|---|---|---|
| Head | always | the description line + info dialog (`info_button`) |
| **A. Context** | always | set selector, set-status banner, queue position + progress, Previous / Next / jump, the facts grid with a status chip |
| **B. Verdict** | always | three segmented buttons, the loss driver, **Save & next** + **Save**, the save-state line |
| **C. Sections** | collapsed | *Refine polygon* (the drawing + CNN tools), *Export this alert* (template, build, downloads) |

A and B together are the loop, and they fit one screen with no scroll at the
panel's width. C is everything done occasionally, one click away.

Decisions taken during brainstorming and binding on this design:

* **App theme, not the mockups' palette.** The boards are hand-styled dark HTML
  (IBM Plex, `#1a1a1a`, gold `#bf8f2d`). The implementation reproduces their
  layout, grouping, sizes, states and copy with vuetify components and theme
  colours, working in light and dark exactly as the rest of the panel does. No
  webfont link (the deployment is Voila and must not need a CDN), no palette
  literals (§10).
* **No keyboard shortcuts.** The boards' footer legend (`← → move`, `1 2 3
  verdict`, `S save & next`, `D draw`) and the `1 · 2 · 3` hint in the verdict
  header are **not** built. The timeline's lanes already own the arrow keys
  while focused, and a document-level listener would need step, dialog and
  text-field guards for a speed-up nobody has asked for yet. *Save & next* is
  the whole speed-up this design ships.
* **Mono/tabular numerals are kept** for the queue counter and the dates — the
  counter is read while it changes, and a proportional font makes it jump.

## 3. Block A — Context

A new `gui/tile/analysis/context.py` owns the whole block and returns one raw
`v.Card`. `gui/tile/analysis/navigator.py` is absorbed into it and deleted.

**3.1 Set selector and status banner.** `build_set_selector(app_state)` and
`build_set_status_banner(app_state)` move inside the block unchanged.

**3.2 Queue position.** One line: the current alert's **1-based** position, the
total, and the review tally.

```
012 / 530                    212 reviewed · 318 left
[████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]
```

* position = `actual_alert_id + 1`, zero-padded to the width of the total
  (`012` for 530 alerts), tabular numerals, the largest type in the block;
* total = `len(alerts_gdf)`;
* reviewed = the count of rows whose `status` is present and not
  `"Not reviewed"` — the same rule `set_context.record_review` writes into
  `AlertSet.n_reviewed`, but counted from the live table so the number moves the
  moment a save lands;
* left = total − reviewed;
* the bar is a `v.ProgressLinear` of `reviewed / total`, 4px, primary colour.

The counting rule is a pure module function

```python
def review_counts(gdf) -> tuple[int, int]:   # (total, reviewed)
```

so it is tested offline with no widgets. With no alerts loaded the block renders
`— / —`, an empty bar and a disabled nav row (the panel is reachable before a
set is active).

The block refreshes on `app_state.actual_alert_id` (position) and on
`app_state.alerts_gdf` (total, tally, facts) — subscriptions registered once,
like every other builder in the tile.

**3.3 Navigation.** `Previous` and `Next` as two equal labelled buttons, 44px
high, and a third 44px icon button that reveals the jump field inline:

* `Previous` → `app_state.go_prev()`, `Next` → `app_state.go_next()`;
* the jump button toggles the visibility of the existing id `TextField` plus a
  `Go` button; `go_to(int(value))` clamps, as today. The field itself must stay
  the same widget object: `view_actual_alert` writes `.v_model` into it on every
  alert change.
* The jump field shows the same 1-based number the counter shows, so `go_to`
  takes `value - 1` and the field is seeded with `actual_alert_id + 1`. This is
  the one place where the panel's numbering and `app_state`'s differ, and it is
  converted in exactly these two spots.

**3.4 The facts grid.** A two-column grid, label column fixed, replacing
`widgets.selected_alert_info` and the `Alert info` card:

| label | value |
|---|---|
| Source | `alert_sources`, mono |
| Detected | `first → last`, mono tabular; `convert_julian_to_date`'s `"N/A"` is shown as it comes |
| Status | a chip |

The chip's label comes from the existing legend keys
(`legend.status.not_reviewed` / `.confirmed` / `.false_positive` /
`.needs_revision`) and its colour from
`legend_specs.status_colors()[STATUS_CODES[status]]`, so the panel, the map and
the charts name and paint a status identically and no hex is written here (§10).
A row with no status, or `"Not reviewed"`, gets the grey *Not reviewed* chip.

`refresh_alert_info(widgets, app_state)` keeps its name, its guards and its
place in `analysis_tile.py` — a set-mismatch returns `None` and leaves the block
alone, and a rebind with the same alert still selected refreshes it (a save
writes the status into the row and rebinds `alerts_gdf`). Only what it writes
changes: the grid and the chip instead of the `<strong>` run.

## 4. Block B — Verdict

`gui/tile/analysis/review_panel.py` is rewritten around three pieces of state.

**4.1 The verdict becomes state, not a stashed widget.** Today the answer lives
as `widgets.confirmation_select.v_model`, read back by `state.capture_alert` and
`export.review_status`. Three buttons cannot carry a `v_model`, so the answer
moves into `AnalysisState`:

```python
verdict: solara.Reactive  # None | "Confirmed" | "False Positive" | "Need revision"
```

* the three buttons write it; the selected one is filled with the primary
  colour, the other two are outlined;
* `capture_alert` reads `analysis.verdict.value` as `status`;
* `widgets.confirmation_select` and the stash comment in `review_panel.py` /
  `export.py` are deleted;
* **the default is `None`**, not `Confirmed`. An unreviewed alert starts with no
  button selected, and the existing save guard
  (`questionarie.confirmation_missing`) refuses the save until one is chosen. A
  reviewed alert starts on its stored status, set by `view_actual_alert` beside
  the driver combobox it already seeds.
* the stored status strings are unchanged, so the alert store, the PMTiles
  `status_code`, Overview's counts and `review_status`'s `None` handling are
  untouched.

Button labels are the mockups' (*True alert*, *False positive*, *Needs
review*), each with the matching icon; the group carries the old question
(`questionarie.confirmation_q`, "Is this a true alert?") as its `aria-label`,
and the block's heading is *Verdict*.

**4.2 The loss driver.** The `sw.Combobox` is unchanged (codes in, labels out,
`widgets.driver_combobox` stays the contract). It is **hidden when the verdict
is `False Positive`** and replaced by the mockup's note — *"No loss driver is
stored for a false positive."* — because `_save_attributes` stores no driver for
one. Hidden by a `display` toggle, not by dropping it from the tree (§10).

**4.3 Save & next.** The save flow stays in `export.py`, which owns
`_save_attributes`, the refusal messages, the tracked job and the writer
registration. It is exposed as a runner the verdict block drives:

```python
def make_save_runner(widgets, app_state, analysis, app_tile_model) -> Callable
#   run_save(buttons, then=None)
```

* `run_save` captures the alert on the UI thread (`capture_alert`), spawns the
  same worker through `spawn_in_context`, shows the same toasts, and calls
  `_apply_save()` on success — byte for byte today's `_on_save`, with the
  button it spins passed in and an optional `then`;
* **Save** passes no `then`; **Save & next** passes `app_state.go_next`, which
  therefore runs **only after a save that actually wrote** — a refused save
  (no verdict, another set, a rebuilt store) leaves the reviewer on the alert
  with the message that explains it;
* `then` runs inside the worker's success path, where `_apply_save` already
  rebinds `alerts_gdf`; `go_next` only sets a reactive, which is safe from a
  worker thread exactly as that rebind is.

The export section keeps the template field, *Download alert data* and the two
download buttons. `export_labels.save_btn` is reused for the secondary *Save*.

**4.4 The save-state line.** Under the two buttons, one line of three states:

| state | line |
|---|---|
| the reviewer changed something since the last save | *Unsaved changes on this alert* (warning colour) |
| a save landed on this alert in this session | *Saved HH:MM* (success colour) |
| neither | empty |

It is driven by a new reactive

```python
dirty: solara.Reactive  # bool
```

set by a verdict change, a driver-combobox change and a new `defo_layer`;
cleared by `reset_alert` (an alert change forgets it) and by a successful save.
`last_save_time` already exists and gives the `HH:MM`, formatted in local time.
A reviewed alert arrived at fresh shows the empty state, not *Saved* — the
timestamp is this session's, and the panel must not claim a time it does not
know. The status chip in block A is what reports "this alert is reviewed".

## 5. Block C — the collapsible sections

A new `gui/tile/analysis/section.py` holds one small builder shared by both:

```python
def build_section(title: str, body, badge=None) -> SimpleNamespace
#   .root    the raw widget to put in the panel
#   .badge   set_badge(text, tone) to update the header summary
#   .open    open/close programmatically
```

* the header is one 50px button: a chevron that rotates, the title, and a
  right-aligned summary badge;
* the body is wrapped in a `v.Html` whose `style_` toggles
  `display: none` — **never** `v-if`, and the body is never removed from the
  tree: these bodies hold the maps' draw controls and the CNN toolbars, whose
  imperative `.show()/.hide()/.disabled` state must survive a collapse, and the
  step container already destroys and rebuilds this DOM on close and reopen;
* both start **collapsed**, and nothing auto-expands.

**5.1 Refine polygon's badge.** *nothing drawn* when `analysis.defo_layer` is
`None`, else *N shapes · X.X ha* — the shape count from the GeoDataFrame and the
area from the same equal-area computation `export._save_attributes` already uses
for `area_ha`, extracted into one helper both call so the badge and the stored
column can never disagree.

**5.2 Export's badge.** *available after save* while the download button is
disabled, *2 files ready* once the downloads toolbar is revealed — driven by the
same two states that already gate those widgets.

## 6. Inside Refine polygon

The contents of `edit_tools.py`, relabelled and regrouped; the handlers,
threading, tokens and notifications are untouched.

* The icon-only edition toolbar becomes three labelled 44px buttons:
  **Draw** (`start_btn`), **Apply** (`save_btn`), **Stop** (`stop_btn`). The
  tooltips go: a labelled button does not need one. `edition_labels.save_edit_button`
  ("Save") is replaced by a new `edition_labels.apply_edit_button` ("Apply"):
  two buttons reading *Save* in one panel, one of which saves the review and one
  the drawing, is the single worst collision in the current layout.
* **`clear_btn` is deleted.** It is built, styled, enabled and wired to
  `_clear_edition` today but it is **not in `toolbar_edition`'s children**, so it
  has never been on screen. The mockups have three buttons; the dead fourth one
  and its handler go.
* The two CNN rows get the model names that already exist in the locale behind
  the `M 1` / `M 2` tooltips (`model_labels.dl_button1` = "Smaller and faster
  model", `dl_button2` = "Larger but slower model") as visible labels, each with
  a **Run** button, and the add/remove pair (`dl_button_add` /
  `dl_button_remove`) as labelled buttons under it. The `dl_msg` warning button
  keeps its place and its tooltip.
* **The result line is made truthful.** The worker returns a `.tif` today and
  the vectorisation (`raster_to_gdf` + `simplify_and_extract_features`) happens
  at *Add* time, so nothing can be said about the prediction when it finishes.
  The worker now vectorises right after inference, inside its tracked job, and
  records `(path, gdf, n_patches, area_ha)`; the row then reads *Found 3
  patches · 3.9 ha* and *Add* reuses the cached features instead of redoing the
  work. A vectorisation that finds nothing is the existing
  `model_labels.dl_404` warning, which is what the reviewer sees today anyway —
  one step earlier.
  `AnalysisState.prediction_files` keeps its name and its `{model: path}` shape
  (`_record_prediction`'s locked replace is unchanged); the derived numbers live
  beside it in a second reactive `prediction_shapes: {model: (n, area_ha)}`,
  cleared by the same `reset_alert`.

## 7. Inside Export this alert

Unchanged contents: the report-template field, *Download alert data*, and the
hidden downloads toolbar with *Files* and *Report*. The review **Save** is gone
from it (§4.3), so `export_labels.export_title` ("Save and Export") becomes the
section title *Export this alert*.

## 8. State and contract changes

`gui/tile/analysis/state.py`:

```python
verdict: solara.Reactive            # None | status string      (new)
dirty: solara.Reactive              # bool                      (new)
prediction_shapes: solara.Reactive  # {model: (n, area_ha)}     (new)
```

`reset_alert` clears all three (`verdict` to `None`, `dirty` to `False`,
`prediction_shapes` to `{}`) together with everything it clears today, under the
same lock and the same token bump.

`capture_alert` reads `status` from `analysis.verdict` instead of
`widgets.confirmation_select`; its `drivers`, `alerts`, `alert_id`, `grid` and
`bb` reads are unchanged.

`view_actual_alert` additionally seeds `analysis.verdict` from the row's
`status` (`None` when absent or `"Not reviewed"`), next to the driver combobox
it already seeds, and after `reset_alert` — the seeding must not be undone by
the reset.

## 9. Messages

Every new string goes into `deforestation_alerts/message/{en,es-ES,fr}/ui.json`.
New keys, all under `analysis_tile`:

| key | English |
|---|---|
| `queue.counter` | `{index} / {total}` |
| `queue.tally` | `{reviewed} reviewed · {left} left` |
| `queue.empty` | `No alert set is loaded` |
| `nav.previous` | `Previous` |
| `nav.next` | `Next` |
| `nav.jump` | `Jump to alert number` |
| `nav.go` | `Go` |
| `facts.source` | `Source` |
| `facts.detected` | `Detected` |
| `facts.status` | `Status` |
| `facts.range` | `{first} → {last}` |
| `verdict.title` | `Verdict` |
| `verdict.true` | `True alert` |
| `verdict.false` | `False positive` |
| `verdict.revision` | `Needs review` |
| `verdict.no_driver` | `No loss driver is stored for a false positive.` |
| `verdict.save_next` | `Save & next` |
| `verdict.unsaved` | `Unsaved changes on this alert` |
| `verdict.saved_at` | `Saved {time}` |
| `sections.refine` | `Refine polygon` |
| `sections.refine_empty` | `nothing drawn` |
| `sections.refine_summary` | `{shapes} · {area} ha` |
| `sections.shapes` | plural: `{n} shape` / `{n} shapes` |
| `sections.export` | `Export this alert` |
| `sections.export_after_save` | `available after save` |
| `sections.export_files` | plural: `{n} file ready` / `{n} files ready` |
| `edition_labels.apply_edit_button` | `Apply` |
| `model_labels.run` | `Run` |
| `model_labels.found` | `Found {patches} · {area} ha` |
| `model_labels.patches` | plural: `{n} patch` / `{n} patches` |

Retired, and therefore **deleted from all three locales** (the catalogues are
pruned files: an English key no code uses fails
`test_every_english_key_of_the_legacy_files_is_used`):
`alert_info.card_title`, `alert_info.alert_source`, `alert_info.first_date`,
`alert_info.last_date`, `alert_info.status`, `alert_info.status_reviewed`,
`alert_info.status_not_reviewed`, `questionarie.confirmation_yes`,
`questionarie.confirmation_no`, `questionarie.confirmation_revision`,
`edition_labels.save_edit_button`, `edition_labels.edit_title`,
`export_labels.export_title`.

Kept, with a new job: `export_labels.save_btn` is the secondary *Save* of block
B; `questionarie.confirmation_q` is the verdict group's `aria-label`;
`edition_labels.start_edit_button` and `.stop_edit_button` become visible
labels instead of tooltips.

`analysis_tile.info_md` and `analysis_tile.description` are rewritten in all
three locales: they name the navigator card, the export card and a *Save* that
has moved.

Plural keys go through `tn()`; numbers through `format_number()`. Nothing is
resolved at import time.

## 10. Constraints

Inherited whole from the branch's standing rules; each applies to every task.

* **Raw-widget builders only.** Plain functions returning `v.*`, never
  `@solara.component`: MapApp renders the step with `v-if` and a reacton tree
  rendered through `render_fixed` goes blank on reopen.
* **Reactive `.subscribe()`, not `use_effect`.** The panel is built once per
  session, so every subscription is registered once and its unsubscriber is
  deliberately dropped.
* **No traitlets, no pysepal model classes** under `gui/tile/analysis/`
  (`tests/test_analysis_state.py` enforces it).
* **No palette hex outside `legend_specs.py`** — the status chip reads
  `status_colors()`; `tests/test_legend_specs.py::test_no_other_module_writes_these_colours`
  greps every `gui/**/*.py`.
* **Collapse with `display`, never `v-if`**, and never remove a body from the
  tree (§5).
* **Blocking work off the UI thread** through `spawn_in_context`, with feedback
  through `notify_bridge.tracked_job` / `current_notifier()`.
* **The five-element `before_info` / `after_info` contract is untouched** by
  this work, as are the stored status strings and `area_ha`.
* **No new dependency.** `sepal_environment.yml` does not change.
* **Touch targets ≥ 44px**; real `<button>` / `<input>` + label semantics, which
  the vuetify components give; the verdict group carries an `aria-label`.
* **Both themes.** Nothing hard-codes a background or an ink colour; the block
  chrome follows the app theme like the stage already does.
* **Commit messages carry no `Co-Authored-By` trailer.**

## 11. Files

**Create**

| File | Responsibility |
|---|---|
| `gui/tile/analysis/context.py` | block A: `review_counts`, the queue line, nav row, facts grid, status chip, `build_context` |
| `gui/tile/analysis/section.py` | `build_section`: the collapsible header + body with its badge |
| `tests/test_analysis_context.py` | offline: counts, padding, 1-based conversion, empty state, chip colour/label, facts refresh |
| `tests/test_analysis_sections.py` | offline: collapse keeps the body in the tree, badges follow their state |
| `tests/test_analysis_verdict.py` | offline: button→state, driver hiding, dirty transitions, save & next advances only on a save that wrote |

**Modify**

| File | Change |
|---|---|
| `gui/tile/analysis/review_panel.py` | rewritten as block B (verdict buttons, driver, save buttons, state line) |
| `gui/tile/analysis/analysis_tile.py` | `controls` becomes head + A + B + C; `refresh_alert_info` writes the grid; `view_actual_alert` seeds `verdict` |
| `gui/tile/analysis/state.py` | `verdict`, `dirty`, `prediction_shapes`; `reset_alert`; `capture_alert` reads the verdict |
| `gui/tile/analysis/export.py` | `make_save_runner` extracted from `_on_save`; the save button leaves the card; `area_ha` helper shared |
| `gui/tile/analysis/edit_tools.py` | labelled buttons, `clear_btn` deleted, CNN rows with names + Run + result line, vectorise-at-run |
| `gui/tile/analysis/widgets.py` | `selected_alert_info` and `confirmation_select` drop out of the contract |
| `deforestation_alerts/message/{en,es-ES,fr}/ui.json` | §9 |
| `tests/test_analysis_alert_info.py`, `test_review_status.py`, `test_set_context.py`, `test_feedback_notifications.py`, `test_analysis_state.py`, `test_analysis_download.py` | the verdict moves off `widgets.confirmation_select`; the info card becomes the facts grid |

**Delete**

| File | Why |
|---|---|
| `gui/tile/analysis/navigator.py` | absorbed into `context.py` |

## 12. Test plan

Offline tier (the default run; anything unmarked that reaches Earth Engine
fails through `tests/conftest.py`'s `_no_live_ee` guard):

1. `review_counts` over a table with missing, `"Not reviewed"` and the three
   real statuses; an empty table; a table of all-reviewed rows.
2. The counter's zero padding follows the total's width, and the displayed
   number is 1-based while `go_to` receives 0-based.
3. The empty state: no alerts loaded renders the placeholder and does not raise;
   the nav buttons are pressable and no-op.
4. The status chip's label and colour for each of the four statuses come from
   `legend_specs` / the `legend.status.*` keys, and an absent status reads
   *Not reviewed*.
5. A review save rebinds `alerts_gdf` with the same alert selected: the chip and
   the tally both move (today's `test_analysis_alert_info` invariant, retargeted).
6. Another set's alerts leave block A alone (the existing guard).
7. The verdict buttons write `analysis.verdict`; an unreviewed alert starts at
   `None`; a reviewed one starts on its stored status; `reset_alert` clears it.
8. `False Positive` hides the driver combobox and shows the note, and the
   combobox is still in the tree.
9. `dirty` goes true on a verdict change, a driver change and a new
   `defo_layer`; false on `reset_alert` and after a save that wrote.
10. **Save & next advances only on a save that wrote**: a refused save (no
    verdict / another set / a rebuilt store) leaves `actual_alert_id` where it
    was and still shows its warning.
11. Collapsing a section leaves its body in the widget tree with its imperative
    state intact (the draw-control and CNN toolbar visibility survive a
    collapse-expand cycle).
12. The refine badge: `None` → *nothing drawn*; a two-polygon layer → the count
    and the area, equal to what `_save_attributes` would store as `area_ha`.
13. The export badge follows the download button's disabled state and the
    downloads toolbar's visibility.
14. The CNN worker records `(path, n, area_ha)` and `reset_alert` clears the
    derived numbers; *Add* consumes the cached features and adds the same
    geometry the current `raster_to_gdf` path produces.
15. `tests/test_i18n.py` passes: every new key in all three locales, no English
    left in the translations, no retired key left behind, one catalogue layout.
16. The no-traitlets guard and the no-palette-hex guard still pass.

No Earth-Engine-tier test is needed: nothing in this design touches a request
builder. The existing `ee`-marked tests must keep passing untouched.

## 13. Out of scope

* Keyboard shortcuts and the footer legend (§2).
* The workflow header and its 7/7 step bar at the top of the boards — existing
  shell chrome, unchanged.
* The imagery timeline, shipped already.
* Any change to the alert store's columns, the PMTiles styles, Overview, or the
  report.
* The deferred recipe-load re-discovery path still marked `TODO(recipe-load)` in
  `analysis_tile.py`.
