# Deforestation-Alerts — pysepal + Solara Migration & Local-Vectorization Design

*Status: DRAFT for review · Date: 2026-06-06 · Author: brainstorming session*

This document specifies a four-axis migration of the **Forest Cover Loss Alerts
Analysis** SEPAL module:

1. Re-layout to the modern `data/ docs/ gui/ deforestation_alerts/` structure.
2. Migrate the framework `sepal_ui` → `pysepal`, including a full **Solara** rewrite.
3. Replace the GEE server-side vector-alert pipeline with **raster download +
   local vectorization**.
4. Reuse proven assets from `sepal-contrib/se.plan`, `sepal-contrib/spatial-risk-module`,
   and `openforis/pysepal`.

It is decision-oriented and grounded in the actual reference code (clones inspected
at `/tmp/seplan`, `/tmp/srm`, `/tmp/pysepal_probe`).

---

## 1. Goals & motivation

| Goal | Why this migration serves it |
|---|---|
| **Maintainability** | The Solara rewrite forces decomposition of the 2054-line `analysis_tile.py` god-class (and the 1143-line `mosaics_helper.py`) into thin components + services. |
| **Reliability** | A tests-first safety net (currently *zero* tests) plus replacing fragile GEE `getInfo`/grid-fallback machinery with deterministic local geoprocessing. |
| **Performance** | Local vectorization removes server-side `reduceToVectors` + repeated `getInfo` round-trips; concurrency moves to a single managed task layer. |

Not a goal: adding new user-facing features. This is a structural modernization
that preserves current capability (AOI selection, multi-source alert filtering,
CNN refinement, overview, reporting, recipes).

---

## 2. Two reframing facts (these change the brief)

1. **`pysepal` *is* `sepal_ui`, renamed.** Same author/DOI/packages
   (`sepalwidgets`, `model`, `aoi`, `mapping`, `translator`). `import sepal_ui`
   still works via a deprecation shim. The framework migration is therefore **two
   separable decisions**: (a) a near-drop-in *rename*, and (b) an optional but
   chosen *Solara reactive rewrite*. (Verified:
   `/tmp/pysepal_probe/docs/plans/2026-03-02-phase2-rename-design.md`.)

2. **The app already vectorizes rasters locally.**
   [`mosaics_helper.py:220`](../../../component/scripts/mosaics_helper.py)
   `raster_to_gdf` already runs `rasterio.features.shapes` on CNN output, and
   `geedim`, `rasterio`, `geopandas` are already dependencies. The data-pipeline
   change is the **promotion of an existing internal pattern** to become the
   primary alert path — not greenfield work.

---

## 3. Decisions (this session)

| # | Decision | Choice | Consequence |
|---|---|---|---|
| D1 | Framework scope | **Full Solara migration** (rename + restructure + raster pipeline + Solara rewrite) | Phase 4 included; god-classes decomposed; ~19 `.observe()` sites + navigation + threading inverted to reactive. |
| D2 | Alert geometry | **True pixel polygons + a `bbox` column** | `alerts_gdf` carries accurate `geometry` (polygon) *and* a bounding-box column for back-compat with current consumers/recipes. |
| D3 | CNN model | **Keep, extract to a `cnn_worker` service** | TensorFlow/MightyMosaic stay; the triplicated CNN code (`model_worker` / `mosaics_helper` / inline in `analysis_tile`) is consolidated into one service. |
| D4 | AOI scale | **Up to national scale** | Download path must be tiled/block-wise and RAM-aware from the start (geedim tiling + `optimal_block_shape`-style block reads). |

### Defaulted sub-decisions (please confirm or override at review)

| # | Question | Proposed default | Rationale |
|---|---|---|---|
| D5 | Voila during transition | Keep `ui.ipynb` runnable through Phase 3; add Solara entry in Phase 4; remove Voila at the end of Phase 4. | App stays launchable at every step. |
| D6 | What `data/` holds | **Bundled static inputs only** (`report_template.docx`, recipe JSON-schema). Runtime outputs stay under the user workspace (`~/module_results/deforestation_alerts/<recipe>/`) via `parameter/directory.py`. | Matches how both reference apps treat user outputs; keeps the repo clean. |
| D7 | Views location | Views (tiles/widgets) live in top-level **`gui/`**; the importable package `deforestation_alerts/` holds **models + scripts/services**. | Follows spatial-risk-module (the Solara reference); matches the requested `gui/` folder. |
| D8 | State management | Keep **serializable domain models** (`recipe`, `alerts`) as `pysepal.model.Model` for `export_data`/`import_data`; add a **`gui/store/state_manager.py` `AppState`** (`solara.reactive`) for ephemeral UI/navigation state, holding references to the domain models. | Mirrors se.plan; cleanly separates "what gets saved" from "what the UI tracks." |
| D9 | Alert sources | **Keep all four** (GLAD-L, GLAD-S2, RADD, CCDC). Flag CCDC's user-asset path as the most bespoke; candidate for later simplification, not removal now. | No feature loss; revisit if it complicates the raster path. |
| D10 | Recipe back-compat | **Support loading existing recipes.** Add a back-compat loader; adopt se.plan's JSON-schema validation. The `bbox` column (D2) keeps `alert_db.csv` consumers working. | D2 was chosen partly for compatibility; honor it. |

---

## 4. Target architecture

### 4.1 Folder structure

Layout follows **spatial-risk-module** (the closest Solara reference);
framework/logic patterns and tests follow **se.plan**.

```
deforestation_alerts/                  # repo root
├── data/                              # bundled STATIC inputs only (D6)
│   ├── report_template.docx           # from utils/report_template.docx
│   └── recipe_schema.json             # NEW — JSON-schema for recipe validation (from se.plan pattern)
├── docs/                              # single doc dir (was doc/ + utils/ABOUT_*.md)
│   ├── en.rst
│   └── about/ABOUT_{en,es,fr}.md
├── gui/                               # Solara UI layer (D7)
│   ├── solara_app.py                  # NEW entry point (replaces ui.ipynb)
│   ├── run_solara.sh  supervisord.conf  Dockerfile   # from se.plan deployment
│   ├── store/
│   │   └── state_manager.py           # AppState (solara.reactive) — D8
│   ├── tile/                          # @solara.component views (was component/tile/)
│   │   ├── recipe_tile.py  aux_tile.py  aoi_tile.py
│   │   ├── alerts_filter_tile.py  analysis_tile.py  overview_tile.py
│   └── widget/                        # reusable Solara/rv components (was custom_sw.py)
│       └── custom_sw.py
├── deforestation_alerts/             # importable PACKAGE (renamed from component/)
│   ├── model/                         # pysepal.model.Model subclasses (serializable domain state)
│   │   ├── aoi_date_model.py  aux_model.py  alerts_model.py
│   │   ├── analysis_model.py  app_model.py
│   ├── scripts/                       # services / business logic
│   │   ├── aoi_helper.py  alert_filter_helper.py  mosaics_helper.py
│   │   ├── recipe_helper.py  overview_helper.py  report_builder.py
│   │   ├── raster_export.py           # NEW — geedim ≥2.0 download (§6)
│   │   ├── vectorize.py               # NEW — raster→vector service (§6)
│   │   └── cnn_worker.py              # NEW — consolidated CNN service (D3)
│   ├── message/                       # i18n en/ es-ES/ fr/ — unchanged
│   └── parameter/directory.py
├── tests/                             # NEW (§7)
│   ├── conftest.py
│   └── data/recipes/*.json
├── pyproject.toml  requirements.txt  noxfile.py  CHANGELOG.md  README.md
```

### 4.2 Layer responsibilities (the enforceable contract)

| Layer | Folder | Single responsibility | Import rule |
|---|---|---|---|
| **State** | `deforestation_alerts/model/` + `gui/store/` | Domain models hold serializable state (recipe, alerts); `AppState` holds ephemeral UI/nav state. | No GEE, no view imports. |
| **Services** | `deforestation_alerts/scripts/` | Pure-ish logic: GEE image building, raster export, vectorization, CNN inference, gdf ops, reporting, recipe IO. | **No `solara`/`ipyvuetify`/`reacton` imports.** |
| **Views** | `gui/tile/`, `gui/widget/` | `@solara.component` functions that render UI and wire callbacks. Callbacks stay **thin**: read state → call a service/task → write state. | No business logic, no raw GEE queries. |
| **App shell** | `gui/solara_app.py` | Session bootstrap (`init_ee` + `@with_sepal_sessions` + `get_current_gee_interface`), `MapApp.element()`, navigation. | — |

Two mechanically-checkable rules make the refactor enforceable and are candidates
for a CI lint guard: **services never import a UI framework**, and **view
callbacks never contain GEE/DataFrame logic**.

### 4.3 Concurrency model (Solara)

Per the pysepal Solara guide (`/tmp/pysepal_probe/docs/guides/solara-migration.md` §5,
"GEE + SessionManager Migration Rule"):

- **GEE work** → `solara.lab.use_task(prefer_threaded=False)` calling
  `await gee_interface.*_async(...)`. Do **not** scaffold `create_task()` flows or
  default to `asyncio.to_thread` for GEE.
- **Local CPU-heavy work** (CNN inference, vectorization, raster IO) →
  `use_task(prefer_threaded=True)` or `asyncio.to_thread(...)`.

This replaces every raw `threading.Thread` + `queue.Queue` worker in the current
code (`model_worker.py`, `worker_m1/worker_m2`, `start_*_thread`).

---

## 5. pysepal migration mapping (condensed)

The *rename* is 1:1 (`from sepal_ui …` → `from pysepal …`). The Solara rewrite is
the real work; key transforms (full table in the pysepal guide §1):

| Current (`sepal_ui` / traitlets) | Solara target |
|---|---|
| `class W(v.Btn, SepalWidget)` | `@solara.component def W(...)` |
| `Unicode().tag(sync=True)` | `solara.reactive(...)` (module/AppState) or param |
| `@observe("x")` / `self.observe(h, "x")` | `solara.use_effect(h, [dep])` |
| `link((a,"v"),(b,"v"))` | one shared `solara.reactive` passed to both |
| `self.children = [...]` / `.hide()/.show()` | declarative render / `if visible:` |
| `v.Dialog`, `v.Select(grouped)` | `reacton.ipyvuetify as rv` (no native Solara yet) |
| raw `threading.Thread` (GEE) | `use_task(prefer_threaded=False)` + `await *_async` |
| raw `threading.Thread` (CNN/raster) | `use_task(prefer_threaded=True)` |
| Voila `ui.ipynb` entry | `solara run gui/solara_app.py` |

**Verify-early risks (Phase 1):**
- `AoiDateModel` subclasses `AoiModel` and reuses **private** setters
  (`_from_admin/_from_vector/_from_geo_json/_from_asset`, `.feature_collection`) —
  confirm these survive in pysepal.
- pysepal ships GAUL **2024** (`pygaul>=0.4.2`); admin codes may shift. se.plan
  needed an explicit GAUL 2015→2024 migration. **Saved recipes storing admin codes
  may need a shim** (ties to D10).
- `PlanetView` API (used in `aux_tile`) — confirm unchanged.
- Pin a known-good pysepal (se.plan pins `3.5.0`; probe is `3.6.1`). Note
  `MapAppComponent` is documented but **not shipped** in 3.6.1 — use `MapApp.element()`.

---

## 6. Raster-download + local-vectorization redesign

### 6.1 What is replaced

Today, alert "vectors" are bounding boxes computed **inside GEE** via
`alert_raster.reduceToVectors(geometryType="bb")` + `getInfo()`, with grid-fallback
machinery (`obtener_datos_gee_total_v3`, `obtener_datos_gee_parcial_map`,
`coveringGrid`, `evaluate_with_retry`) in
[`alert_filter_helper.py`](../../../component/scripts/alert_filter_helper.py).
**That GEE-vector machinery is deleted.** The `ee.Image` building/merging (Stages A/B
in `aoi_helper.py` / `alerts_filter_tile.py`) is **kept**.

### 6.2 New pipeline

```
Stage A,B (KEEP): build + merge ee.Image  → SelectedAlertsModel.filtered_alert_raster
        │                                     (bands: alert-code, date)
        ▼  scripts/raster_export.py  (NEW)
   download_ee_image(image, out.tif, region=aoi, scale, crs, unmask_value=255, nodata_value=255)
        │   ← copy from /tmp/srm/spatialrisk/gee/ee_raster_export.py  (geedim ≥2.0 .gd accessor)
        │   ← tiled automatically (max_tile_size/max_tile_dim); national-scale safe (D4)
        ▼
   local GeoTIFF
        │  optional: metric-CRS reproject for area_ha  (srm geo_utils.calculate_utm_rioxarray)
        ▼  scripts/vectorize.py  (NEW)
   rasterio.features.sieve(min_size_pixels)  →  rasterio.features.shapes  →  shapely polygons
        │   ← block-wise read for large rasters (srm optimal_block_shape pattern, D4)
        │   ← template already in mosaics_helper.raster_to_gdf (line 220)
        ▼
   convert_to_geopandas(...)  ← REUSE (alert_filter_helper.py:486), re-pointed to local shapes
        │   produces geometry=polygon (D2) + bbox column + point(centroid)
        │   + alert_sources/date columns, preserving the integer alert-code decoding
        ▼
   AlertAnalysisModel.alerts_gdf   (downstream consumers unchanged)
```

### 6.3 Reusable functions

- **Download:** `download_ee_image(...)` from `/tmp/srm/spatialrisk/gee/ee_raster_export.py`
  (uses `image.gd.prepareForExport(...)` + `img.gd.toGeoTIFF(...)`). Copy near-verbatim.
- **Vectorize:** generalize your own `mosaics_helper.raster_to_gdf` (already
  `rasterio.features.shapes`); add `features.sieve` for `min_size_pixels`
  (already computed in `create_vector_download_params`).
- **gdf assembly:** reuse `convert_to_geopandas` (`alert_filter_helper.py:486`),
  re-pointed to local shapes; add the `bbox` column (D2).
- **Block/metric helpers (D4):** `srm/spatialrisk/geo_utils.py`
  (`calculate_utm_rioxarray`, `get_utm_proj_str_from_lat_lon`),
  `srm/spatialrisk/utilities/resources.py::optimal_block_shape`.

### 6.4 Dependency changes

| Dep | Now | Target | Reason |
|---|---|---|---|
| `geedim` | `==1.8.1` | **`>=2.0.0`** | srm's `download_ee_image` uses the geedim 2.0 `.gd` accessor; 1.8.1 lacks it. Re-lock env; check `geemap` compat (current uses `geemap.download_ee_image`). |
| `rasterio` | `==1.3.10` | keep (`<1.3.11`) | `features.shapes/sieve`. |
| `geopandas`/`shapely`/`fiona` | pinned | keep | gdf assembly. |
| `rioxarray` / `odc-geo` | — | **optional** | only if adopting srm's xarray remap/reproject; plain rasterio equivalents avoid the dep. |
| `dask` | — | **skip v1** | geedim tiling + block reads should cover national scale without a cluster. |

**Net:** no new *hard* deps beyond bumping geedim.

### 6.5 Fidelity to verify (Phase 3 integration tests)

- The integer alert-code band (source codes spread across decimal digits, decoded
  in `report_builder.get_unique_alerts`) must survive GeoTIFF export + vectorize
  exactly (dtype/nodata). **Test this.**
- New geometry is true polygons (D2), not bboxes; the `bbox` column reproduces the
  old shape for back-compat. Downstream area/report logic validated against a
  fixture gdf.

---

## 7. Testing strategy (se.plan's two-tier model)

Currently **zero** tests. We introduce them *before* risky changes.

- **Tier 1 — pure fixtures, no GEE/UI (Phase 0).** Golden recipe round-trip
  (save→load→compare on `recipe_parameters.json` + `alert_db.csv`); report-builder
  placeholder substitution against a fixture gdf. Template:
  `/tmp/seplan/tests/test_recipe.py`, `test_export_csv.py`. These lock the
  observable contracts (recipe IO, report output) the migration must preserve.
- **Tier 2 — EE integration, skip-if-no-creds (Phase 3).** `conftest.py` runs
  `init_ee()` once and skips when unauthenticated; a tiny
  `ee.Geometry.Rectangle` AOI downloads a known alert raster and asserts the
  resulting gdf matches the old `reduceToVectors` output's shape/columns/codes.
  Template: `/tmp/seplan/tests/test_statistics_integration.py`.
- CI: add a pytest job alongside the existing ruff/pre-commit lint.

---

## 8. Phased migration sequence

Each phase keeps the app runnable and ships independently.

**Phase 0 — Safety net (tests-first).** Add `tests/` Tier-1 suite + `conftest.py`.
No production code changes. *Exit:* green tests pin current recipe/report behavior.

**Phase 1 — Rename `sepal_ui`→`pysepal`.** Bump dep; first run *unchanged* (shim
proves it), then sed imports. Resolve verify-early risks (§5: AoiModel setters,
GAUL codes, PlanetView). *Exit:* Phase 0 tests still green, no behavior change.

**Phase 2 — Folder restructure.** `component/` → `deforestation_alerts/`; tiles/
widgets → `gui/`; docs consolidated; `report_template.docx` → `data/`. Fix all
`from component.x` imports. Still Voila-runnable (D5). *Exit:* tests green.

**Phase 3 — Raster-download + local-vectorize (load-bearing).** Bump geedim ≥2.0;
add `raster_export.py` + `vectorize.py`; re-point `convert_to_geopandas` (D2 columns);
delete the GEE-vector machinery in `alert_filter_helper.py`. Add Tier-2 integration
tests + national-scale block path (D4). Done on the stable traitlets base *before*
Solara, so the data change is testable in isolation. *Exit:* new pipeline matches
golden gdf; recipe round-trip green.

**Phase 4 — Solara migration (incremental, tile-by-tile).**
(a) Bootstrap `gui/solara_app.py` (se.plan session pattern + `MapApp.element()`);
(b) port simplest tiles first (About/Overview);
(c) migrate `alerts_filter_tile` and `analysis_tile`, converting raw-thread workers
to `use_task` (§4.3) and **decomposing the 2054-line god-class into services**
(`cnn_worker` per D3, plus `imagery_service`, `alert_repository`);
(d) introduce `gui/store/state_manager.py` `AppState` (D8);
(e) remove Voila entry once all tiles render under Solara.
`MapApp` renders ipyvuetify + Solara together, so tiles flip one at a time with the
app live. *Exit:* Solara entry only; god-classes gone; tests green.

### Opportunistic tech-debt fixes (during the relevant phase)
- Consolidate triplicated CNN code → `cnn_worker` (Phase 4 / D3).
- De-duplicate `convert_julian_to_date` (`mosaics_helper` + `overview_helper`).
- Fix dead load path: `load_parameters_from_json` calls non-existent
  `aoi_tile.process_alerts2()`.
- Remove the unlocked cross-thread mutation race in `create_gdf_full`
  (naturally resolved by the `use_task` migration).

---

## 9. Risks & open items

| Risk | Mitigation |
|---|---|
| National-scale raster download size/RAM/time (biggest unknown, D4) | geedim tiling + block-wise vectorize; validate on a large AOI early in Phase 3; keep dask as a later escape hatch. |
| geedim 2.0 bump breaks `geemap.download_ee_image` usage | Phase 3 verifies/replaces the geemap wrapper with the direct `.gd` path. |
| pysepal 3.6.1 gaps (`MapAppComponent` unshipped; partial Solara widgets) | Pin a known-good version; use `MapApp.element()` + `rv` fallbacks. |
| AOI/GAUL code churn breaks saved recipes | Recipe back-compat loader + admin-code shim (D10). |
| Solara rewrite scope (19 observers + navigation + threading) | Incremental tile-by-tile; coexistence via `MapApp`; tests guard each step. |

---

## 10. Out of scope (YAGNI)

- New alert sources or new analysis features.
- dask cluster download path (revisit only if block-wise local proves insufficient).
- Rewriting the docx/cartopy reporting (richer than either reference; just relocate
  template to `data/`).
- Dropping any of the four alert sources (D9 keeps all).

---

*Reference clones: `/tmp/seplan` (se.plan), `/tmp/srm` (spatial-risk-module),
`/tmp/pysepal_probe` (pysepal 3.6.1). Key reuse targets:
`srm/spatialrisk/gee/ee_raster_export.py`, `seplan/solara_app.py`,
`seplan/component/scripts/file_handler.py`, `seplan/tests/`,
`pysepal_probe/docs/guides/solara-migration.md`, and your own
`component/scripts/mosaics_helper.py::raster_to_gdf`.*
