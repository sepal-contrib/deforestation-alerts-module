# Custom alert source (GEE id) + "Analyze alerts" progress feedback

Date: 2026-06-10
Status: Approved (design)

Two independent features that both touch the Optional-files (Aux) tile and the
Filter/analyze flow. They can be implemented in either order.

---

## Feature 1 — Custom alert source from a GEE asset id

### Goal

Let an advanced user add their **own** alert product (an Earth Engine raster asset)
as a named alert source that behaves like the built-in sources (GLAD-L, RADD,
GLAD-S2, CCDC): it appears in the Filter source-select, participates in the
multi-source union/filter, and its resulting clusters flow into the Analysis tile.

### Decisions (from brainstorming)

- **GEE asset id only.** Loading from a *local raster* is explicitly deferred to a
  follow-up (no GEE ingestion in this change).
- **RADD-identical raw input.** The asset is assumed to use RADD's raw encoding —
  an `Alert` band with values `0/1/2/3` and a `Date` band as `days/1000 + 2000` —
  and is normalized with the same transform RADD uses.
- **Exactly one** custom source at a time (mirrors CCDC's single-asset design).
- **User-named.** The user gives the source a name; that name is the source-select
  label AND the value decoded into the output `alert_sources` column.

### Approach (chosen: generalize the CCDC mechanism)

CCDC already implements "a user-supplied asset as an alert source": an Aux-tile
`AssetSelect` → an `AuxModel` trait → a traitlets observer that inserts an entry
into `alert_filter_model.alerts_dictionary` → `get_alerts` dispatch → a normalizer
(`_from_nrt`) → it appears as a selectable source and flows through the existing
union/download/vectorize/analysis path. The custom source is that same path with a
user-given name as the dictionary key and a RADD-style normalizer targeting a spare
alert-code slot.

Alternatives rejected:
- **Bake into `create_basic_alerts_dictionary`** — that builder is static and runs
  before the user provides the asset; it cannot hold user-configured input.
- **Separate "vectorize-and-concat" path** — would skip the server-side union (so a
  custom alert overlapping a built-in alert would not merge into one cluster) and add
  a parallel code path, diverging from "included in the filter process".

### Alert-code slot (the key technical point)

Built-in alert codes use a **positional digit scheme**, summed across sources during
the union and decoded in `report_builder.get_unique_alerts`:

| Source   | Digit position | Codes        |
|----------|----------------|--------------|
| GLAD-L   | ones           | 1, 2         |
| RADD     | tens           | 10, 20       |
| GLAD-S2  | hundreds       | 100, 200     |
| CCDC     | thousands      | 1000, 2000   |
| **custom** | **ten-thousands** | **10000, 20000** |

The alert band is `int16` (max 32767), so the ten-thousands slot is the only free
position — hence exactly one custom source. A pixel that is both GLAD-L (1) and
custom (10000) sums to 10001 and decodes to *both* sources, exactly like the
existing scheme.

### Changes

1. **`deforestation_alerts/model/aux_model.py`** — add two traits:
   `custom_alert_layer = Any(None)` (the asset id) and
   `custom_alert_name = Any(None)` (the user's name). Add both to
   `export_dictionary` and `import_from_dictionary` so they persist into the recipe
   JSON (same shape as `ccdc_layer`).

2. **`gui/tile/aux_tile.py` `build_aux_layout`** — add a new card (alongside, not
   replacing, the CCDC card): a `TextField` for the name + an
   `AssetSelect(types=["IMAGE","IMAGE_COLLECTION"])` for the asset. Bind them to the
   two new traits in the existing `aux_model.bind(...)` chain.

3. **`gui/tile/aoi_tile.py`** — add `_update_dictionary_custom(aux_model,
   alert_filter_model)` mirroring `_update_dictionary_ccdc`: when
   `custom_alert_layer` is set, insert/replace a dictionary entry **keyed by
   `custom_alert_name`** with
   `{"collection": <asset id>, "kind": "CUSTOM", "start_date": "2015-01-01",
   "end_date": datetime.today().strftime("%Y-%m-%d")}`; when cleared, remove it.
   On a **name change**, remove the old key and add the new one (re-key). Register it
   as a traitlets observer on `custom_alert_layer` **and** `custom_alert_name` in
   `AoiTile` via `use_effect([])`, alongside the existing ccdc observer. The wide
   date range only lets the availability check pass; real date filtering is done by
   the normalizer.

4. **`deforestation_alerts/scripts/aoi_helper.py`**:
   - New `_from_custom(start, end, aoi, asset)` — a copy of `_from_radd` that reads
     the `Alert`/`Date` bands and remaps `Alert [0,1,2,3] → [0,0,20000,10000]` (the
     ten-thousands slot) instead of `[0,0,20,10]`. Date handling is identical
     (`Date/1000 + 2000`, masked to the user's `start`/`end`). Accept an IMAGE or
     IMAGE_COLLECTION asset (wrap a single image as RADD does its collection).
   - `get_alerts` — dispatch on the entry's `kind == "CUSTOM"` (not on the name,
     which is now user-defined) → call `_from_custom` with the asset id from the
     dictionary entry. Thread `kind` + the per-source asset through
     `create_filtered_alert_raster_dictionary` (it currently passes only the single
     `ccdc_asset` to every source).
   - `create_available_alert_dictionary` — extend the existing `if name == "CCDC"`
     single-IMAGE wrapping to also cover `kind == "CUSTOM"` (a custom IMAGE asset
     must be wrapped in `ee.ImageCollection.fromImages([...])` before
     `aoi_filterbounds_check`, just like CCDC).

5. **`deforestation_alerts/scripts/report_builder.py` `get_unique_alerts`** — add an
   optional `custom_name=None` param; decode the ten-thousands digit
   (`(value % 100000)//10000 in [1,2]`) → add `custom_name` (fallback `"Custom"`).

6. **`deforestation_alerts/scripts/alert_filter_helper.py` `convert_to_geopandas`**
   — add an optional `custom_name=None` param and pass it through to
   `get_unique_alerts`. The Filter tile's `_run_analyze` calls
   `convert_to_geopandas(gdf, custom_name=aux_model.custom_alert_name)`.

7. **`deforestation_alerts/message/{en,es-ES,fr}/ui.json`** — add `aux_tile`
   labels for the custom-source card title/name-hint/asset-hint (3 locales).

### Edge cases / notes

- **Band names**: the asset must expose `Alert` + `Date` bands (RADD's names). This
  is the "RADD-identical raw" contract; document it in the asset-hint label.
- **Analysis tile**: expected to need **no** changes — clusters flow via
  `app_state.alerts_gdf` with the custom name already in `alert_sources`, and the
  per-cluster imagery/CNN/edit/export do not branch on source. *Verify this
  assumption* (grep the analysis package for source-name branching) during
  implementation; if any branch exists, handle the custom name there.
- **Persistence/recipe-load**: saving the two traits is in scope. Full recipe **load**
  re-discovery remains the pre-existing deferred `TODO(recipe-load)`; this change
  does not close it, only stops it from regressing.

---

## Feature 2 — "Analyze alerts" progress feedback

### Goal

During the (multi-second) Analyze run, show the user which stage is executing —
e.g. "joining alerts", "downloading", "vectorizing" — with the counts that are
cheap to know.

### Approach

A component-local reactive status string, set from the analyze task as it advances,
rendered under the Analyze button. Setting a **reactive** from the task's worker
thread is safe and already done in `_run_analyze` (it sets
`app_state.alerts_gdf.value` / `current_page_view.value` the same way) — this is
distinct from the "no **widget** (`.items`/`.v_model`) mutation in a task body" rule.

### Changes (all in `gui/tile/alerts_filter_tile.py`)

1. Add `analyze_status = solara.use_reactive("")` in `AlertsFilterTile`.
2. In `_run_analyze`, set it through the existing stages (reset to stage 1 at the
   top of each run):
   - `"Joining {N} alert source(s)…"` — `N = len(alert_source)` (the
     `create_filtered_alert_raster` union step).
   - `"Downloading alert raster…"` then `"Vectorizing clusters…"` — download and
     vectorize happen inside one helper (`_run_local_vectorize`). Give that helper an
     optional `on_status=None` callback and have it call `on_status("Downloading alert
     raster…")` before `download_ee_image` and `on_status("Vectorizing clusters…")`
     before `vectorize_alert_raster`. `_run_analyze` passes `on_status=analyze_status.set`,
     keeping the download/vectorize logic in one place.
   - `"Found {M} alerts — assembling…"` — `M = len(gdf)` after vectorize.
3. Render `solara.Info(analyze_status.value)` under the Analyze button, shown only
   while `analyze_task.pending`. The button keeps its existing spinner; the existing
   `solara.Error(analyze_task.exception)` still handles failures.
4. Add the label strings to `message/{en,es-ES,fr}/ui.json` as
   `filter_tile.analyze_status_*` keys (i18n-consistent with the rest of the UI).

### Out of scope

- Live download percentage / progress bar (a heavier option that was not chosen).
- Applying the same progress treatment to the AOI **search** task (could be a later,
  symmetric change).

---

## Verification (both features)

Run the app (`run-deforestation-alerts`) with the real SEPAL session:

1. **Custom source appears**: in Optional files, set a name + a RADD-shaped GEE asset
   id; run an AOI search → confirm the named source shows in the Filter source-select.
2. **Analyze with custom selected**: select the custom source (alone, then together
   with GLAD-L) → Analyze → confirm clusters are produced and the `alert_sources`
   column shows the user's name (and "GLAD-L, <name>" for overlaps).
3. **Decode**: confirm the ten-thousands codes decode to the custom name, not "RADD".
4. **Progress**: confirm the status line cycles Joining → Downloading → Vectorizing →
   Found N, then navigates to Analysis.
5. **Analysis flow**: confirm a custom-sourced alert opens and reviews normally.
6. **Regression**: existing pytest suite still passes; built-in sources unaffected;
   CCDC still works. Add unit tests for `_from_custom` band remap and the
   `get_unique_alerts` ten-thousands decode.
