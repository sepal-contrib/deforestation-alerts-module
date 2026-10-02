# Overview as a dashboard dialog — design

Date: 2026-10-02
Branch: `feat/alert-monitoring-slices2-6`
Status: approved in chat 2026-10-02 (surface = popup dialog; alert layers move
to Integrate & vectorize; palette fixed by reordering only; one slice; default
measure = area in hectares).

Mockups: <https://claude.ai/artifact/FsydPdhJqfj6kJ3R4zTnkf> — artboard
`Dashboard.dc.html` is the design. The two 480 px panel artboards predate the
move to a dialog and survive only as reference for the card internals (hero
number, KPI tiles, numbers table).

## Goal

Turn Overview from a five-row count table in the right panel into a **dashboard
in a popup window** that answers, in hectares and across periods, what the
alert sets actually say — and move the alert map layers out of it, into
Integrate & vectorize, so the alerts land on the shared map when the run
finishes and stay there.

Success looks like:

- A user who has run Integrate twice opens Overview and sees, without clicking
  anything: confirmed loss in hectares, how that compares with the previous
  period, the area split by review status, and a column per period.
- The same user closes Overview and the alerts are **still on the map**,
  because the layer belongs to Integrate now, not to the Overview view.
- A user with one period sees no history chart and no empty axes — one line of
  text in its place.

## Scope

In scope:

1. `deforestation_alerts/scripts/set_summary.py` — the aggregation layer
   (counts **and** hectares, size bins, weekly histogram, drivers, sources,
   admin rollup), Solara-free and unit-testable.
2. `gui/scripts/charts.py` — matplotlib → SVG figures, light and dark.
3. `gui/tile/overview_tile.py` — rewritten as the dashboard: hero number, KPI
   tiles, six charts, the numbers table, the existing export card.
4. `RAIL_ITEMS`: Overview becomes `display="dialog"`, `dialog_width=1300`.
5. `gui/scripts/alert_layers.py` — new home for the map plumbing that leaves
   Overview: the builders, the auto-show rule and click-to-review.
6. `gui/tile/integrate_step.py` — two layer switches (active set, earlier
   sets); the step gains `layers=`.
7. New i18n keys in all three locales (`en`, `es-ES`, `fr`).

Out of scope:

- Changing the stored palette hex values. The CVD defect is fixed by segment
  **order** (decision, 2026-10-02); re-stepping the hexes would amend §9.1 of
  the alert-monitoring spec and change the map, and is a separate proposal
  against the branch that spec lives on.
- Persisting a summary on `AlertSet` or as a sidecar. Measured as unnecessary
  (see Constraint 1).
- Exporting the summary itself. `report_builder` already covers reporting.
- Brushing between a chart and the map, or drilling from a chart to an alert.
- Cross-project comparison.
- Reading the summary for a set whose store is missing: it reads as MISSING,
  exactly as today.

## What this supersedes

The alert-monitoring workflow spec (`2026-09-30-alert-monitoring-workflow-design.md`)
is **not on this branch** — it lives in the `deforestation-alerts-module`
checkout on `integration/2026-09-30`. These paragraphs of it are superseded and
must be amended when the branches meet:

| Superseded | Was | Becomes |
|---|---|---|
| §9.2, layer ownership | "While the Overview view is open, the active set's `alerts.pmtiles` is drawn … and hidden again when the view closes." | The layer is owned by Integrate & vectorize, shown on completion, and persists until switched off or the project changes. |
| §9.2, earlier sets | "Show earlier sets" is an Overview switch. | It is an Integrate switch, beside the active-set switch. |
| §9.2, click to review | Click hit-tests "while the view is open". | Click hit-tests whenever the alert layer is on, from any view. |
| §9.3, placement | Overview is a right-panel view with a "← Workflow" button. | Overview is a 1300 px MapApp dialog; the right panel is workflow-only (spatial_risk parity). |
| §9.3, content | A five-row `v.SimpleTable` of counts. | The dashboard below. The count table survives as the numbers table, with area columns. |

`RAIL_ITEMS`' docstring in `gui/store/workflow_steps.py` states the old
placement and must be updated in the same change.

## Constraints discovered in the code

1. **A projected Parquet read is 36× cheaper than a geo read, so no cache is
   needed.** Measured on a synthetic 50 000-alert GeoParquet store: reading
   `["status","area_ha","alert_date_min","alert_sources","description","admin1"]`
   through pyarrow takes **7.9 ms**; `geopandas.read_parquet` of the same file
   takes **282 ms** (35.8×). Six periods therefore cost ~47 ms. This removes
   the whole persisted-summary branch: no new `AlertSet` field, no
   `summary.json`, and no edit to `Project.save`/`load`'s hand-maintained
   mirrors (architecture contract #9).
2. **The store already holds every column needed, but two of them are filled
   only by review.** From `deforestation_alerts/scripts/alert_table.py` and
   `engine/vectorize.py` the store carries `status`, `area_ha`,
   `alert_date_min`/`alert_date_max`, `alert_sources`, `description`,
   `admin1/2/3`, `count`, `alert_id`. Three details matter:
   - `alert_date_min/max` are `year + doy/1000` (`vectorize.yyyyddd_to_decimal`:
     `2026045` → `2026.045`), so the inverse is exact —
     `date(int(v), 1, 1) + timedelta(days=round((v - int(v)) * 1000) - 1)`.
   - `description` (loss drivers) and `admin1` are written **during Analysis
     review**, per reviewed alert (`gui/tile/analysis/export.py`), not at
     vectorize time. `admin2`/`admin3` are created empty and **nothing ever
     writes them**. So the driver and admin cards cover *reviewed alerts only*,
     carry that in their subtitle, and show an empty state until there are
     reviews. The admin rollup is **admin1 only**.
   - `description` holds several comma-joined codes
     (`gui/scripts/loss_drivers.codes`). An alert's **full area counts once per
     code it names**, so the driver bars may sum past the reviewed total; the
     card's caption says so. The alternative (splitting an alert's area between
     its drivers) would invent a number the review never recorded.
   - `alert_sources` is an English prose join (`report_builder.format_list`:
     `"GLAD-L and RADD"`). It is parsed back with the existing
     `report_builder.parse_formatted_string`, never by splitting on `", "`.
   Nothing new is computed at vectorize time.
3. **The status palette fails CVD validation in map order.** The four
   `STATUS_CLASSES` of `gui/scripts/legend_specs.py` give a deuteranopia ΔE of
   **2.8** between Confirmed `#d32f2f` and False positive `#388e3c` —
   indistinguishable. Ordering the stack and the legend
   `[1, 3, 2, 0]` (Confirmed → Needs revision → False positive → Not reviewed)
   raises the worst **adjacent** pair to **13.8** (PASS) with the hexes
   untouched. All-pairs separation still fails at 2.8, so pie, donut, scatter,
   choropleth and small multiples are **forbidden** for status; stacked bars,
   grouped bars and lines are allowed.
4. **Deployment is Voila, not solara-server.** `ui.ipynb` → `run_ui.sh` →
   `/voila/render/ui.ipynb`. `solara.FigureEcharts` loads echarts from a CDN;
   `solara.FigurePlotly` needs anywidget and plotly is only a transitive
   `geemap` dependency, so using it would mean a new line in
   `sepal_environment.yml` to satisfy
   `tests/test_environment_file.py::test_every_package_the_app_imports_is_declared`.
   `matplotlib>=3.9` is already declared and renders offline.
5. **The shell already supports dialog rail items.** `_rail_items` in
   `gui/solara_app.py` emits `display` and `width` straight from `RailItem`
   (About 1300, Project 900), and the `.v-dialog__content:has(> .dialog-container)`
   rule already shifts a wide dialog clear of the left rail. The migration
   needs no new shell machinery.
6. **MapApp reports a dialog opening, never its closing.** The nav bridge
   deliberately ignores `current_step = None` (dialog closed, "Map" clicked).
   So a view-open gate for Overview cannot be made reliable, and the design
   does not use one (see Data layer).
7. **`IntegrateStep` is the only step the shell does not pass `layers` to.**
   Adding it is a one-line change.
8. **MapLayers already gives the persistence this needs.** Its contract: "a
   layer switched on in any step stays on until switched off or the project
   changes (`clear`)". Showing the alert layer from Integrate is therefore
   enough for it to survive leaving the step.
9. **A test forbids palette hexes outside `legend_specs.py`.**
   `tests/test_legend_specs.py::test_no_other_module_writes_these_colours`
   reads every `gui/**/*.py` and fails if it finds any `ALERT_CLASSES`,
   `STATUS_CLASSES` or `MASK_COLOR` hex. `gui/scripts/charts.py` must therefore
   contain **no** status hex — including the `#9e9e9e` of Not reviewed — and
   read them from `legend_specs.status_colors()`.
10. **matplotlib emits a global `*` CSS selector.** Every figure's SVG starts
   `<style type="text/css">*{stroke-linejoin: round; stroke-linecap: butt}</style>`,
   which would leak to the whole page when inlined. It must be stripped and
   replaced with `stroke-linejoin="round" stroke-linecap="butt"` on the root
   `<svg>`. Sequential ids (`figure_1`, `axes_1`, `patch_N`) repeat between
   inlined figures but are unreferenced; the *referenced* ids
   (`url(#p3d57174d35)`, `xlink:href="#m9b8879c56b"`) are content hashes and do
   not collide.
11. **`svg.fonttype="none"` drifts.** matplotlib then measures text with
    DejaVu Sans while the browser renders Roboto, so labels laid out by
    matplotlib can collide. Path-ified text (`svg.fonttype="path"`) freezes the
    layout matplotlib measured; it costs ~20–32 KB per figure instead of
    5–17 KB, which is immaterial.

## Data layer

New module `deforestation_alerts/scripts/set_summary.py`. Solara-free, no
ipyvuetify, heavy imports inside functions (architecture contract #7).

```python
@dataclass(frozen=True)
class StatusBreakdown:
    code: int            # STATUS_CODES value
    n: int
    area_ha: float
    median_ha: float

@dataclass(frozen=True)
class SetSummary:
    set_id: str
    n_alerts: int
    area_ha: float
    by_status: tuple[StatusBreakdown, ...]   # in STACK_ORDER
    size_bins: tuple[tuple[float, float, int, float], ...]   # lo, hi, n, ha
    by_week: tuple[tuple[date, float, int, bool], ...]       # week, ha, n, in_period
    by_driver: tuple[tuple[str, float, int], ...]            # code, ha, n — sorted
    by_source: tuple[tuple[str, float, int], ...]
    by_admin: tuple[tuple[str, float, float, int], ...]      # admin1, ha, confirmed_ha, n
    n_reviewed: int      # the denominator of by_driver and by_admin
    largest: tuple[int, float, str, date] | None             # alert_id, ha, admin, date

SUMMARY_COLUMNS = ["alert_id", "status", "area_ha", "alert_date_min",
                   "alert_sources", "description", "admin1"]

def summarize_alerts(frame, *, period_start=None) -> SetSummary: ...
def read_summary(store_path, set_id, *, period_start=None) -> SetSummary: ...
def decimal_year_to_date(value: float) -> date: ...
```

- `summarize_alerts` is **pure** — it takes a DataFrame, so every branch is
  unit-testable without a store or a render harness.
- `read_summary` does the projected pyarrow read (Constraint 1) and calls it.
- `period_start` marks look-back weeks: `by_week` rows before it carry
  `in_period=False` and are drawn de-emphasised (Constraint 2 — the dates are
  decimal years, so `decimal_year_to_date` is the one conversion).
- Missing or unexpected values never raise: an unknown `status` falls to code 0,
  a null `description` to the `"unknown"` driver code, a null `admin1` to
  "Unattributed". An empty frame yields a zeroed `SetSummary`, never `None`.

**No open-gate, no persisted cache.** Overview computes through
`solara.lab.use_task(prefer_threaded=True)` keyed on
`(project name, set id, store generation)` for the selected set, and on
`tuple((s.id, s.fingerprint, s.n_reviewed) for s in sets)` for the history, with
a module-level memo dict on the same keys. A review Save changes
`n_reviewed` and the store generation, so the key changes and the summary
recomputes by itself. Constraint 1 makes the open-gate Constraint 6 would have
made unreliable unnecessary: the worst case is ~47 ms of worker time on a set
switch the user never looks at.

Overview therefore **no longer reads `app_state.alerts_gdf` for anything but
the export card**, and `_set_alerts`'s "these alerts are still the previous
set's" guard narrows to that one use. Any set can be summarised without loading
it.

## The dashboard

`RailItem("overview", 3, "overview_tile", "app.drawer_item.overview", "mdi-chart-bar", "dialog", 1300)`.

Deleted from the shell: the `_wrap("overview")` panel wrapper, the
`"right_panel_action": "open"` branch for a panel rail item (no panel rail
items remain), and the `panel_view == "overview"` reads. Deleted from the tile:
`OVERVIEW_VIEW`, `_back_to_workflow`, and the back button.

`OverviewTile` gains `dismiss=`, wired by the shell exactly as `ProjectDialog`'s
is (`dismiss_dialog(mapapp_ref.get("widget"), nav_model)`): **Review alerts**
dismisses the dialog and then sets `current_page_view` to `analysis_tile`,
rather than opening Analysis behind an open modal.

Layout at 1300 px, two columns (see `Dashboard.dc.html`):

- **Header row** — set selector (`SetSelector`, unchanged), the measure
  selector (**Area (ha)** default, or Alerts), and the STALE/MISSING status
  text. One measure for the whole page: this is what keeps every chart on a
  single axis.
- **KPI strip** — Confirmed loss (hero, with a period-over-period delta and a
  sparkline), Detected area, Reviewed %, Largest alert. A delta carries an
  arrow glyph **and** the words "vs <period>"; never colour alone. An increase
  is drawn in the critical ink, because more loss is worse.
- **Left column** — area by review status; history; size distribution;
  by administrative area (a table, `admin1`, reviewed alerts only, with an
  empty state).
- **Right column** — the numbers table; when loss was detected; loss drivers;
  by alert source.
- **Footer** — the existing export card (`build_export_card`), unchanged.

## The charts

New module `gui/scripts/charts.py`: one function per figure, each taking a
`SetSummary` (or a list of them), a measure, and a theme dict; each returning an
SVG string. No Solara import.

| Function | Form | Ruling |
|---|---|---|
| `chart_spark` | 120×30 sparkline | Hero support. No axes, no legend. |
| `chart_status` | one horizontal stacked bar, 420×74 | Part-to-whole, 4 parts. 2 px surface gap between segments (`edgecolor=theme["gap"], linewidth=2.0`). An in-segment % label only when the segment exceeds 11% of the total, so labels never collide; its ink is chosen by measured luminance, never hardcoded white. Two-column legend, each entry carrying that status's value. |
| `chart_trend` | stacked columns per period, 420×196 | Rendered **only when `len(project.periods) > 1`**; otherwise the card is replaced by one line of text. The period still under review gets `hatch="///"` on its Not-reviewed segment — "not final" without spending a colour. Totals are direct-labelled. |
| `chart_sizes` | log-binned histogram, 420×150 | Size is a magnitude: **one hue**, never the status palette. |
| `chart_timing` | columns by week of first detection, 420×162 | Weeks before `period.start` are drawn in the de-emphasis grey with a "period starts" rule, so look-back alerts cannot read as this period's signal. |
| `chart_drivers` | horizontal bars, 420×160 | **Reviewed alerts only** (Constraint 2), stated in the subtitle, with an empty state until there are reviews. Top 5 + Other, **one hue**: nine nominal classes, so a ninth hue is never generated and a value ramp would invent a ranking the codes do not have. An alert's full area counts once per code it names, so the bars may sum past the reviewed total — the caption says so. |
| `chart_sources` | horizontal bars, 420×110 | One bar per distinct source combination, parsed with `report_builder.parse_formatted_string`. An alert seen by two sources is counted once, under its combination ("GLAD-L + RADD"), never in both single-source bars. |

Rendering and embedding:

- `mpl.rcParams["svg.fonttype"] = "path"` (Constraint 11),
  `hatch.linewidth = 0.6`, `font.family = ["Roboto", "Helvetica Neue", "Arial", "sans-serif"]`.
- `charts.py` exposes `to_inline_svg(fig)` which saves to a string buffer,
  strips the global `*` `<style>` block and XML comments, and injects
  `stroke-linejoin="round" stroke-linecap="butt"`, `role="img"` and
  `aria-label=…` onto the root `<svg>` (Constraint 11).
- The figure is embedded with `solara.HTML(tag="div", unsafe_innerHTML=svg)`, so
  it scales with its card and needs no CDN. `solara.FigureMatplotlib` is the
  fallback if inlining misbehaves under Voila; it wraps the SVG in an
  `ipywidgets.Image`, which cannot inherit the page font — which is why the
  text is path-ified either way.
- `matplotlib.font_manager`'s `findfont` logger is silenced at import: SEPAL
  has no Roboto installed and the warning floods the app log.

## Colour and accessibility

- `STACK_ORDER = (1, 3, 2, 0)` lives in `gui/scripts/legend_specs.py` beside
  `STATUS_CLASSES`, so the one palette table stays the one place colour
  decisions are made (§9.1 of the superseded spec). Charts and legends read it;
  the map does not change.
- Colours come only from `legend_specs`. No hex literal in `charts.py` for a
  data colour; theme chrome (surface, ink, grid, de-emphasis) is the one
  exception and lives in a single `THEMES` dict.
- Every chart with ≥2 series carries a legend **and** direct labels, so
  identity is never colour-alone. This is not optional: it is the relief for
  Constraint 3.
- The numbers table is the table-view twin for the whole page. A status name in
  it carries a small colour chip beside it; the text itself stays in the ink
  tokens, never in the series colour.
- Dark mode is a chosen second set of steps validated against `#1e1e1e`, not an
  automatic flip. `app_state.dark` picks the theme dict; a theme change
  re-renders the figures.

## Alert layers move to Integrate & vectorize

Today ~350 of the 643 lines of `gui/tile/overview_tile.py` are map plumbing, and
the alert layer exists only while the Overview panel is open.

**New `gui/scripts/alert_layers.py`** takes, moved rather than rewritten:
`earlier_of`, `_centroid_style_for`, `_CENTROID_LAYER_STYLE`,
`add_centroid_layer`, the active-set `build()` closure (tiles →
`StoreBusy` deferral → centroid fallback → warn-once tippecanoe set), and
`earlier_build`. Keys rename `overview:set` → `alerts:set`,
`overview:set:<id>` → `alerts:set:<id>`, `overview:popup` → `alerts:popup`.
`set_legend` / `earlier_set_legend` / `earlier_set_name` stay in
`gui/scripts/source_layers.py`.

`overview_tile._set_alerts` has two callers after the split — the export card
and the hit test — so it moves to `gui/scripts/set_store.py` as
`active_set_alerts(project, gdf)`, beside the `alerts_belong_to` it wraps.

**`gui/tile/integrate_step.py`** gains `layers=` (one line in `gui/solara_app.py`)
and two switches built on the `_toggle` pattern of `gui/tile/layers_step.py`:
*Show alerts on map* and *Show earlier sets* (the second rendered only when
`earlier_of` is non-empty). Neither hides a key that is still in
`layers.inflight` (ruling #10). Persistence needs no code: Constraint 8.

**A shell leaf `_AlertLayers` in `gui/solara_app.py`** owns the layer's
behaviour, with every body in `alert_layers.py` (architecture contract #7). It
sits beside the existing `_FloatingLegend` leaf, which is the precedent: a leaf
that owns map-wide state lives in the shell, not in `gui/widget/` (render-only
by contract) and not in a step.

The effects cannot live in `IntegrateStep`: that component early-returns
`solara.Info(t("workflow.lock_integrate"))` when no source is ready, so hooks
added below the return would be conditionally mounted, and `_IntegrateForm` is
re-keyed per project and period, so it remounts. A project that has a set but
whose harmonized files were cleaned hits exactly that path. The switches stay in
`IntegrateStep` — they only call `layers.show`/`layers.hide` — while the leaf
owns:

- An effect keyed on `(project name, active set id, set fingerprint)` that
  calls `layers.show(ALERT_LAYER_KEY, …)`. A finished run records the set and
  flips `active_set` in `integrate_jobs.apply_result`, so the effect fires
  **once per new set** — on completion, and on a set switch. Nothing re-shows a
  layer the user hid until the set changes again, so the switch and the
  auto-show never fight.
- The earlier-set effects (`_sync_earlier`, `_retry_earlier_when_free`) and the
  `earlier_alerts` hit-test cache, keyed as today on
  `(project, set id, fingerprint)`.
- The `is_writing` / `StoreBusy` deferral and its retry, moved verbatim: it is
  about the store being rewritten, not about which view is open.
- Click-to-review, **no longer view-gated**: with the layer on, a map click
  hit-tests the active set's alerts first, then the earlier sets shown, newest
  first, and opens the popup with **Review** (`set_store.go_to_alert`). The
  popup keeps its key (a `Popup` added without one breaks pysepal's key
  lookups).
- `_on_install`, which clears the hit-test cache when a project is installed.

**Nothing in the leaf may read a view flag.** `integrate_step`'s `showing`
(`panel_view == "workflow" and active_step == integrate`) exists for the drawing
control, which must only be live while the step is visible (ruling #77); the
layer and its clicks must outlive both the step and the Overview dialog.

The auto-show does not thread `layers` into `run_integration_job`: the worker
keeps its current signature, and the reactive change it already makes
(`apply_result` flipping `active_set`) is the trigger.

## Error handling

- A store that cannot be read: `read_summary` raises; the dashboard shows the
  existing MISSING text and no charts. The dialog never renders a broken axis.
- A chart that raises: caught per card, logged, and replaced by that card's
  numbers in text. One bad figure never blanks the dashboard.
- Tile build failures keep today's behaviour exactly — centroid fallback, one
  warning toast per (project, set), silence while the project is being written.
- An empty set: zeroed `SetSummary`, no charts, the empty-state text the step
  already shows.
- One period: no history card, one line of text.

## Testing

`tests/test_set_summary.py`
- empty frame → zeroed summary, no exception
- all-unreviewed, all-confirmed, mixed
- unknown status code → falls to 0
- null `description` → `"unknown"` driver; null `admin1` → "Unattributed"
- `decimal_year_to_date` round-trips a known pair, and handles a leap year
- `period_start` marks look-back weeks `in_period=False`
- `SUMMARY_COLUMNS` are all present in a fixture written by `alert_table`
  (guards against a column rename drifting away from the reader)

`tests/test_charts.py`
- every figure returns a non-empty SVG for a zeroed and for a populated summary
- the returned SVG contains **no** `<style` block and no `*{` selector
  (Constraint 11)
- the root `<svg>` carries `role="img"` and a non-empty `aria-label`
- light and dark differ in their surface colour

`tests/test_legend_specs.py` (extend)
- `STACK_ORDER` is a permutation of the `STATUS_CLASSES` codes
- Confirmed and False positive are never adjacent in `STACK_ORDER`

`tests/test_workflow_steps.py` (extend)
- the Overview rail item is a dialog with a width
- no `RAIL_ITEMS` entry has `display == "panel"` (the wrapper is gone)

`tests/test_i18n.py` — passes unchanged: every new key resolves in all three
locales (`en`, `es-ES`, `fr`).

`tests/test_environment_file.py` — passes unchanged: no new third-party import.

## Files touched

New:
- `deforestation_alerts/scripts/set_summary.py`
- `gui/scripts/charts.py`
- `gui/scripts/alert_layers.py`
- `tests/test_set_summary.py`, `tests/test_charts.py`

Changed:
- `gui/tile/overview_tile.py` — rewritten as the dashboard; the map plumbing
  leaves
- `gui/tile/integrate_step.py` — `layers=`, two switches
- `gui/store/workflow_steps.py` — the Overview `RailItem`, and its docstring
- `gui/solara_app.py` — `layers=` into `IntegrateStep`, `dismiss=` into
  `OverviewTile`, drop the Overview panel wrapper
- `gui/scripts/legend_specs.py` — `STACK_ORDER`
- `gui/scripts/set_store.py` — `active_set_alerts` (moved from `overview_tile`)
- `deforestation_alerts/message/{en,es-ES,fr}/*.json` — new keys
- `tests/test_legend_specs.py`, `tests/test_workflow_steps.py`
- `tests/test_overview_render.py`, `tests/test_overview_export.py` — the tile
  they render is rewritten; the export assertions survive, the count-table ones
  are replaced by the numbers table

Unchanged on purpose: `deforestation_alerts/project/models.py`,
`Project.save`/`load`, `sepal_environment.yml`, `engine/pmtiles.py`,
`engine/vectorize.py`, `gui/scripts/map_layers.py`,
`gui/scripts/integrate_jobs.py`, `gui/scripts/pmtiles_map.py`,
`gui/scripts/source_layers.py`, `gui/widget/set_selector.py`.

Retired: `deforestation_alerts/scripts/overview_helper.py`. Its one function,
`calculate_alert_classes`, has exactly one caller (`overview_tile`) and no test
of its own; `set_summary` supersedes it and `tests/test_set_summary.py` gives it
the coverage it never had. The module is deleted in the same change.

## Deferred

- Re-stepping the Confirmed / False positive hexes so all-pairs separation
  passes, which would unlock pie and donut forms for status. Needs an amendment
  to §9.1 on `integration/2026-09-30`.
- Reading the export card's alerts from the store instead of
  `app_state.alerts_gdf`, so Overview could export a set it has not loaded.
- Brushing from a chart to the map.
- Attributing `admin2`/`admin3` (and `admin1` for unreviewed alerts). Nothing
  writes those columns today; filling them would mean a GAUL lookup per alert at
  vectorize time, which is its own design.
