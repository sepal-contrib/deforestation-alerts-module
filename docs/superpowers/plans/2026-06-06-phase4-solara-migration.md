# Phase 4: Solara Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Voila/traitlets UI with a **Solara** app: a `gui/solara_app.py`
entry using the pysepal session pattern + `MapApp.element()`, tiles rewritten as
`@solara.component` functions, raw `threading`/`queue` workers replaced by
`solara.lab.use_task`, the ~19 `.observe()` sites replaced by `solara.use_effect`,
and the 2054-line `analysis_tile` god-class **decomposed into services**
(`cnn_worker`, `imagery_service`, `alert_repository`) — done incrementally, tile by
tile, with the app live throughout.

**Architecture:** Three enforceable layers (spec §4.2): **services**
(`deforestation_alerts/scripts/`, no UI imports), **state** (serializable
`pysepal.model.Model` domain models + an ephemeral `gui/store/state_manager.py`
`AppState` of `solara.reactive` values, D8), and **views** (`gui/tile/`, `gui/widget/`,
`@solara.component`, thin callbacks). Concurrency follows the pysepal guide §5: GEE →
`use_task(prefer_threaded=False)` + `await *_async`; CPU/raster/CNN →
`use_task(prefer_threaded=True)`. `MapApp` renders ipyvuetify and Solara together, so
tiles flip one at a time while the old app still runs.

**Tech Stack:** Python, **Solara** (+ `solara.lab`), `reacton.ipyvuetify as rv` for
widgets without native Solara equivalents (dialogs, grouped selects, draw control),
pysepal 3.6.1 (`MapApp.element`, `with_sepal_sessions`, `get_current_gee_interface`),
Earth Engine, TensorFlow/MightyMosaic (CNN), pytest.

**Prerequisite:** Phases 0–3 complete. The package is `deforestation_alerts/`, views
are `gui/`, the **raster pipeline is local** (`raster_export`/`vectorize`,
`alerts_gdf` carries polygon geometry), and the test suite is green. This plan
assumes that state.

**Scope note:** Fourth and final per-phase plan from
`docs/superpowers/specs/2026-06-06-pysepal-solara-migration-design.md` (spec §8
Phase 4; §4.2/§4.3; D1, D3, D8). This is the largest phase; it is sequenced so the
app is launchable after every task.

**A note on plan altitude:** net-new, testable units (services, `AppState`, the
bootstrap, the gdf-merge that fixes the race) are given in full with TDD. The
view-component rewrites are large and mechanical; for those, this plan gives the
**conversion recipe** (the exact `observe→use_effect`, `thread→use_task`,
`link→shared reactive`, `children=→declarative` transforms), full code for the small
tiles, and concrete component skeletons + per-sub-component reactive state for the big
ones — rather than reproducing ~3,000 lines verbatim. Each tile port follows the same
documented recipe; the live smoke test after each is the acceptance gate.

---

## The conversion recipe (apply consistently in every view task)

From the pysepal Solara guide §1 and §5 (`/tmp/pysepal_probe/docs/guides/solara-migration.md`):

| Current (traitlets / sepal_ui) | Solara target |
|---|---|
| `class W(v.Btn, SepalWidget): …` | `@solara.component def W(...): …` |
| `Unicode().tag(sync=True)` on a model trait | keep on `Model` if **serialized** (recipe); else `solara.reactive(...)` in `AppState` |
| `self.observe(h, "x")` / `@observe("x")` | `solara.use_effect(h, [x_reactive.value])` |
| `link((a,"v"),(b,"v"))` | one shared `solara.reactive` passed to both |
| `self.children = [...]`, `.hide()/.show()` | declarative render; `if visible.value:` |
| `v.Dialog`, `v.Select(grouped)` | `reacton.ipyvuetify as rv` (no native Solara yet) |
| raw `threading.Thread` doing **GEE** | `use_task(prefer_threaded=False)` + `await gee_interface.*_async(...)` |
| raw `threading.Thread` doing **CNN/raster/IO** | `use_task(prefer_threaded=True)` (or `asyncio.to_thread`) |
| Voila `ui.ipynb` entry | `solara run gui/solara_app.py` |

**Inventory this phase eliminates** (verified counts): ~19 `.observe()` sites
(11 in `analysis_tile`, 2 each in `overview_tile`/`overview_helper`, 1 each in
`aoi_tile`/`alerts_filter_tile`/`recipe_tile`, 3 in `custom_sw`), 5 `link()` sites
(all in `analysis_tile`), 4 worker threads + 4 queues in `analysis_tile`, the 2 S2/
Landsat fetch threads, and the `alerts_filter_tile` vectorize thread from Phase 3.

---

## Target file structure (created/modified)

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt`, `sepal_environment.yml` | Modify | Add `solara`; (Voila removed at the end). |
| `gui/run_solara.sh`, `gui/supervisord.conf`, `gui/Dockerfile` | Create | Deployment (from se.plan). |
| `gui/store/state_manager.py` | Create | `AppState` (`solara.reactive`) — ephemeral UI/nav state (D8). |
| `deforestation_alerts/scripts/cnn_worker.py` | Create | Consolidated CNN service (D3): model load, `RepeatElements`, `apply_dl_model`, `save_prediction_prob`. |
| `deforestation_alerts/scripts/imagery_service.py` | Create | GEE imagery fetch (S2/Landsat/Planet) extracted from `analysis_tile`, async-ready. |
| `deforestation_alerts/scripts/alert_repository.py` | Create | gdf merge/save logic extracted from `analysis_tile` (fixes the `create_gdf_full` race). |
| `gui/solara_app.py` | Create | Solara entry: session bootstrap + `MapApp.element()` + navigation. |
| `gui/tile/*.py` | Modify (rewrite) | Each tile → `@solara.component`. |
| `gui/widget/custom_sw.py` | Modify (rewrite) | Custom widgets → components / `rv` wrappers. |
| `deforestation_alerts/scripts/{mosaics_helper,overview_helper,model_worker}.py` | Modify | De-dup `convert_julian_to_date`; remove `model_worker.py` (folded into `cnn_worker`). |
| `deforestation_alerts/scripts/recipe_helper.py` | Modify | Remove dead `aoi_tile.process_alerts2()` call. |
| `tests/test_cnn_worker.py`, `tests/test_alert_repository.py`, `tests/test_app_state.py` | Create | TDD for the extracted services + state. |
| `ui.ipynb`, `noxfile.py`, `pyproject.toml`, `.github/workflows/ci.yaml` | Modify | Remove Voila entry; switch run/CI to Solara (final task). |

---

# PART A — Foundations (services + state + shell), app stays on Voila

These land without touching the live Voila app, so the suite stays green and the app
stays launchable.

### Task 1: Add Solara + deployment scaffolding

**Files:**
- Modify: `requirements.txt`, `sepal_environment.yml`, `pyproject.toml`
- Create: `gui/run_solara.sh`, `gui/supervisord.conf`, `gui/Dockerfile`

- [ ] **Step 1: Add `solara` to deps**

Add `solara` to `requirements.txt` (below `voila` for now — both coexist during the
transition) and to the `pip:` block of `sepal_environment.yml`.

- [ ] **Step 2: Install and confirm Solara + pysepal MapApp are importable**

```bash
pip install solara
python -c "import solara, solara.lab; from pysepal.sepalwidgets.vue_app import MapApp, ThemeToggle; \
from pysepal.solara import with_sepal_sessions, get_current_gee_interface, setup_sessions; \
print('solara + pysepal MapApp ok')"
```
Expected: `solara + pysepal MapApp ok`. If `MapAppComponent` is referenced anywhere, use `MapApp.element()` (3.6.1 ships `MapApp`, not `MapAppComponent` — spec §5).

- [ ] **Step 3: Create `gui/run_solara.sh`** (adapted from se.plan)

```bash
#!/bin/bash
# Sources .env (if present) and runs the Solara app.
# Usage: ./run_solara.sh [filename] [port]
SOLARA_FILE="${1:-solara_app.py}"
PORT="${2:-8900}"
if [ -f .env ]; then
  while IFS= read -r line; do
    [[ $line =~ ^#.*$ || -z $line ]] && continue
    if [[ $line =~ ^([^=]+)=(.*)$ ]]; then
      name="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
      value="${value#[\"\']}"; value="${value%[\"\']}"
      export "$name=$value"
    fi
  done < .env
fi
solara run "$SOLARA_FILE" --port "$PORT" --no-open
```
Make it executable: `chmod +x gui/run_solara.sh`.

- [ ] **Step 4: Create `gui/supervisord.conf`** (adapted from se.plan; module name `deforestation_alerts`)

```ini
[supervisord]
user=root
nodaemon=true
loglevel=info

[unix_http_server]
file=/tmp/supervisor.sock
chmod=0777

[supervisorctl]
serverurl=unix:///tmp/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[program:solara]
command=bash -c "micromamba run -n deforestation_alerts solara run gui/solara_app.py --host=0.0.0.0 --root-path=/api/app-launcher/deforestation-alerts --production"
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
```

- [ ] **Step 5: Create `gui/Dockerfile`** (adapted from se.plan; adjust image label/paths)

Copy `/tmp/seplan/Dockerfile`, replacing `seplan`→`deforestation_alerts`, the
`WORKDIR`/source URL, and `COPY supervisord.conf …` → `COPY gui/supervisord.conf /etc/supervisor/conf.d/supervisord.conf`. Keep the micromamba + `requirements.lock` pattern. (Lockfile generation is a deployment detail; note it as a follow-up if `requirements.lock` does not yet exist.)

- [ ] **Step 6: Add Solara-friendly pytest import mode to `pyproject.toml`**

Append to `[tool.pytest.ini_options]`:
```toml
addopts = "-ra --import-mode=importlib"
```
(Matches se.plan; avoids import-file-mismatch as the suite grows. Keep `pythonpath = ["."]`.)

- [ ] **Step 7: Run the suite (nothing wired yet)**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt sepal_environment.yml pyproject.toml gui/run_solara.sh gui/supervisord.conf gui/Dockerfile
git commit -m "build: add solara dep + Solara deployment scaffolding (run/supervisor/docker)"
```

---

### Task 2: `AppState` — the reactive store (D8)

Ephemeral UI/navigation state as `solara.reactive`. Domain models (recipe-serialized)
stay as `pysepal.model.Model`; `AppState` holds references to them plus nav/loading.

**Files:**
- Create: `gui/store/state_manager.py`, `gui/store/__init__.py`
- Create: `tests/test_app_state.py`

- [ ] **Step 1: Write failing tests for the store**

Create `tests/test_app_state.py`:
```python
"""AppState: ephemeral reactive UI/nav state (D8)."""

from gui.store.state_manager import AppState


def test_defaults():
    s = AppState()
    assert s.current_page_view.value == "about_tile"
    assert s.actual_alert_id.value == -1
    assert s.alerts_gdf.value is None
    assert s.loading.value is False


def test_reactives_are_independent_per_instance():
    a, b = AppState(), AppState()
    a.current_page_view.value = "aoi_tile"
    assert b.current_page_view.value == "about_tile"


def test_navigation_helpers():
    s = AppState()
    s.alerts_gdf.value = list(range(5))  # stand-in: 5 alerts
    s.actual_alert_id.value = 0
    s.go_next()
    assert s.actual_alert_id.value == 1
    s.go_to(4)
    s.go_next()  # clamp at last
    assert s.actual_alert_id.value == 4
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/test_app_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gui.store'`.

- [ ] **Step 3: Implement `AppState`**

Create `gui/store/__init__.py` (empty) and `gui/store/state_manager.py`:
```python
"""Ephemeral, non-serialized UI/navigation state (D8). Serializable domain state
(recipe, alerts) stays in pysepal.model.Model subclasses; AppState only holds
references + transient UI flags as solara.reactive values.

Layer rule (spec §4.2): this module imports solara but NO views and NO GEE."""

import solara


class AppState:
    def __init__(self):
        # navigation
        self.current_page_view = solara.reactive("about_tile")
        # alert review
        self.actual_alert_id = solara.reactive(-1)
        self.alerts_gdf = solara.reactive(None)  # ref to the domain gdf
        # transient flags
        self.loading = solara.reactive(False)
        self.error_message = solara.reactive(None)
        self.recipe_name = solara.reactive("")

    @property
    def max_alert_id(self):
        gdf = self.alerts_gdf.value
        return (len(gdf) - 1) if gdf is not None else -1

    def go_to(self, idx):
        self.actual_alert_id.value = max(0, min(idx, self.max_alert_id))

    def go_next(self):
        self.go_to(self.actual_alert_id.value + 1)

    def go_prev(self):
        self.go_to(self.actual_alert_id.value - 1)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_app_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/store/ tests/test_app_state.py
git commit -m "feat: add AppState reactive store for ephemeral UI/nav state (D8)"
```

---

### Task 3: Extract the CNN service `cnn_worker` (D3)

CNN code is triplicated: `model_worker.py`, `mosaics_helper.apply_dl_model`, and the
inline `worker_m1`/`worker_m2` in `analysis_tile`. Consolidate into one service. This
is pure CPU (TensorFlow) — testable without GEE.

**Files:**
- Create: `deforestation_alerts/scripts/cnn_worker.py`
- Create: `tests/test_cnn_worker.py`

- [ ] **Step 1: Write tests for the testable surface (no model download)**

Create `tests/test_cnn_worker.py`:
```python
"""cnn_worker: consolidated CNN inference service (D3).

We avoid downloading the real .h5/.keras models in unit tests; we cover the pure,
fast pieces: the serializable RepeatElements layer and save_prediction_prob's
GeoTIFF writer (shape/dtype/transform preserved)."""

import numpy as np
import rasterio
from rasterio.transform import from_origin

from deforestation_alerts.scripts.cnn_worker import (
    RepeatElements,
    save_prediction_prob,
)


def test_repeat_elements_is_keras_serializable():
    import tensorflow as tf  # noqa: F401
    layer = RepeatElements(rep=3)
    cfg = layer.get_config()
    assert cfg["rep"] == 3
    RepeatElements.from_config(cfg)  # round-trips


def _write_src(tmp_path):
    arr = np.zeros((1, 8, 8), dtype="uint8")
    transform = from_origin(10.0, 0.0, 0.01, 0.01)
    p = tmp_path / "src.tif"
    with rasterio.open(p, "w", driver="GTiff", height=8, width=8, count=1,
                       dtype="uint8", crs="EPSG:4326", transform=transform) as d:
        d.write(arr)
    return str(p)


def test_save_prediction_prob_writes_float32_geotiff(tmp_path):
    src = _write_src(tmp_path)
    pred = np.random.rand(8, 8).astype("float32")
    out = str(tmp_path / "pred.tif")
    save_prediction_prob(src, pred, out)

    with rasterio.open(src) as s, rasterio.open(out) as o:
        assert (o.height, o.width) == (s.height, s.width)
        assert o.transform == s.transform
        assert o.dtypes[0] == "float32"
        assert np.allclose(o.read(1), pred)
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/test_cnn_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create `cnn_worker.py` by consolidating the three copies**

Move the canonical implementation here (from `model_worker.py` lines 12–109, which is
identical to `mosaics_helper.apply_dl_model`), exposing:
```python
"""Consolidated CNN inference service (D3). Single home for what was triplicated
across model_worker.py, mosaics_helper.apply_dl_model, and analysis_tile inline.

Pure CPU/IO: no solara, no GEE (spec §4.2 service rule)."""

import numpy as np
import rasterio
import tensorflow as tf
from keras import layers, models
from MightyMosaic import MightyMosaic
from huggingface_hub import hf_hub_download

_MODELS = {
    "m1": ("joseserafinig/forest_cover_change_cnn_m1", "Model1.h5"),
    "m2": ("joseserafinig/forest_cover_change_cnn_m2", "Model2.keras"),
}


@tf.keras.utils.register_keras_serializable()
class RepeatElements(layers.Layer):
    def __init__(self, rep, **kwargs):
        super().__init__(**kwargs)
        self.rep = rep

    def call(self, inputs):
        return tf.repeat(inputs, self.rep, axis=-1)

    def get_config(self):
        cfg = super().get_config()
        cfg.update({"rep": self.rep})
        return cfg


def get_model_path(which):
    """Download (cached) and return the local path of model 'm1' or 'm2'."""
    repo_id, filename = _MODELS[which]
    return hf_hub_download(repo_id=repo_id, filename=filename, local_dir="/tmp")


def save_prediction_prob(original_img_path, prediction, output_path):
    """Write `prediction` (HxW float32) as a GeoTIFF matching the source grid."""
    with rasterio.open(original_img_path) as src:
        profile = src.profile.copy()
    profile.update(count=1, dtype="float32")
    with rasterio.open(output_path, "w", **profile) as dst:
        dst.write(prediction.astype("float32"), 1)
    return output_path


def apply_dl_model(image_path, which, suffix):
    """Run CNN `which` over `image_path`; write the fused prob raster; return path."""
    model_path = get_model_path(which)
    model = models.load_model(
        model_path, custom_objects={"RepeatElements": RepeatElements}, compile=False
    )
    with rasterio.open(image_path) as src:
        img = src.read().transpose(1, 2, 0).astype("float32") / 255.0

    mosaic = MightyMosaic.from_array(img, (256, 256), overlap_factor=2)
    pred = mosaic.apply(lambda x: model.predict(x, verbose=0),
                        progress_bar=False, batch_size=2).get_fusion()
    out = image_path.replace(".tif", f"_prediction{suffix}.tif")
    return save_prediction_prob(image_path, pred[..., 0], out)
```
Adjust `RepeatElements.call`/`get_config` to match the **exact** original layer
(copy from `model_worker.py` lines 71–89 — keep its real behavior; the test only
pins serializability and the `rep` config key). If the original `apply_dl_model`
signature differs (e.g. takes a loaded model object), keep a thin compatible wrapper.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_cnn_worker.py -v`
Expected: PASS. If TensorFlow import is heavy/unavailable in the test env, mark the file `@pytest.mark.slow` and ensure CI installs TF; do not delete the coverage.

- [ ] **Step 5: Re-point the two old copies at `cnn_worker` (keep behavior)**

- In `mosaics_helper.py`, replace its `apply_dl_model`/`RepeatElements` definitions with `from deforestation_alerts.scripts.cnn_worker import apply_dl_model, save_prediction_prob, RepeatElements`.
- In `model_worker.py`, re-export from `cnn_worker` (so existing imports keep working) — or delete it and update its importers. The only importer is `analysis_tile` (`from deforestation_alerts.scripts.model_worker import apply_dl_model, save_prediction_prob`); that file is rewritten in Task 9, so either keep `model_worker.py` as a shim now and delete in Task 9, or update the import now. Choose the shim to keep this task non-breaking.

- [ ] **Step 6: Verify no behavior drift and suite green**

```bash
grep -rn "class RepeatElements\|def apply_dl_model" deforestation_alerts/
pytest tests/ -v
```
Expected: `RepeatElements`/`apply_dl_model` defined **only** in `cnn_worker.py` (others import it); tests PASS.

- [ ] **Step 7: Commit**

```bash
git add deforestation_alerts/scripts/cnn_worker.py deforestation_alerts/scripts/mosaics_helper.py deforestation_alerts/scripts/model_worker.py tests/test_cnn_worker.py
git commit -m "refactor: consolidate triplicated CNN code into cnn_worker service (D3)"
```

---

### Task 4: Extract `alert_repository` (gdf merge) — fixes the race

The `create_gdf_partial`/`create_gdf_full` merge logic in `analysis_tile`
(≈ lines 803–902) mutates `alerts_gdf` cross-thread without a lock (spec tech-debt).
Extract it as pure functions returning new gdfs — no shared mutation, race gone.

**Files:**
- Create: `deforestation_alerts/scripts/alert_repository.py`
- Create: `tests/test_alert_repository.py`

- [ ] **Step 1: Write tests for the pure merge**

Create `tests/test_alert_repository.py`:
```python
"""alert_repository: pure gdf merge/save (extracted from analysis_tile)."""

import geopandas as gpd
from shapely.geometry import Polygon

from deforestation_alerts.scripts.alert_repository import merge_reviewed_into_full


def _gdf(statuses):
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {"status": statuses, "geometry": [poly] * len(statuses)},
        geometry="geometry", crs="EPSG:4326",
    )


def test_reviewed_rows_are_preserved_over_fresh_full():
    current = _gdf(["Reviewed", "Not reviewed"])      # row 0 was reviewed
    full = _gdf(["Not reviewed", "Not reviewed", "Not reviewed"])  # fresh download
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    # the reviewed row survives; the merge does not mutate inputs
    assert (merged["status"] == "Reviewed").sum() == 1
    assert (current["status"] == "Reviewed").sum() == 1  # input untouched


def test_returns_new_object_not_alias():
    current = _gdf(["Not reviewed"])
    full = _gdf(["Not reviewed", "Not reviewed"])
    merged = merge_reviewed_into_full(current, full, actual_alert_id=0)
    assert merged is not current and merged is not full
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

Run: `pytest tests/test_alert_repository.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the pure merge (port `create_gdf_full` logic)**

Create `deforestation_alerts/scripts/alert_repository.py`:
```python
"""Pure gdf merge/save for the alert review state. Extracted from analysis_tile's
create_gdf_partial/create_gdf_full; returns NEW GeoDataFrames (no shared-state
mutation), which removes the cross-thread race.

Service layer: no solara, no GEE."""

import geopandas as gpd
import pandas as pd


def merge_reviewed_into_full(current_gdf, full_gdf, actual_alert_id):
    """Combine reviewed/active rows from `current_gdf` with a freshly downloaded
    `full_gdf`. Port of create_gdf_full (analysis_tile ~840-902) without in-place
    mutation. Returns a new, index-reset GeoDataFrame."""
    if current_gdf is None:
        return full_gdf.reset_index(drop=True).copy()

    mask = (current_gdf["status"] != "Not reviewed") | (
        current_gdf.index == actual_alert_id
    )
    reviewed = current_gdf[mask]
    # keep the fresh full rows not already represented by a reviewed row
    keep_full = full_gdf.iloc[len(reviewed):] if len(full_gdf) > len(reviewed) else full_gdf.iloc[0:0]
    combined = gpd.GeoDataFrame(
        pd.concat([reviewed, keep_full]), crs=current_gdf.crs
    ).reset_index(drop=True)
    return combined
```
Refine the `keep_full` slice to match the **exact** semantics of the original
`create_gdf_full` (read it carefully — the original filtered by reviewed status and
re-attached the remaining "total" rows). The tests pin the load-bearing invariants
(reviewed rows survive, inputs untouched); adjust the body until both pass and the
behavior matches the original on a representative fixture.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_alert_repository.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deforestation_alerts/scripts/alert_repository.py tests/test_alert_repository.py
git commit -m "refactor: extract pure alert_repository.merge_reviewed_into_full (fixes race)"
```

---

### Task 5: Extract `imagery_service` (GEE imagery fetch)

The S2/Landsat/Planet fetch logic lives inline in `analysis_tile` (`view_actual_alert`,
`start_s2_dictionary_thread`, `start_landsat_dictionary_thread`,
`getIndividualS2`/`getIndividualLandsat`). Extract into a service so Task 9's view can
call it from a `use_task`.

**Files:**
- Create: `deforestation_alerts/scripts/imagery_service.py`

- [ ] **Step 1: Move the GEE imagery functions into the service**

Create `deforestation_alerts/scripts/imagery_service.py` and move the
imagery-building functions (`getIndividualS2`, `getIndividualLandsat`, and the Planet
monthly helpers) out of `analysis_tile`/`mosaics_helper` into it, with plain
signatures (alert geometry/date in, image dict out). Keep them synchronous GEE calls
for now; Task 9 wraps them in `use_task(prefer_threaded=False)` and, where a
`gee_interface.*_async` exists, switches to `await`. No UI imports.

- [ ] **Step 2: Verify it imports and the suite is green**

```bash
python -c "from deforestation_alerts.scripts import imagery_service; print('imagery_service ok')"
pytest tests/ -v
```
Expected: `imagery_service ok`; PASS. (Deep GEE behavior is covered by the manual app run in Task 9; here we only relocate.)

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: extract imagery_service (S2/Landsat/Planet fetch) from analysis_tile"
```

---

# PART B — Solara shell + tiles (incremental, app stays live via MapApp)

### Task 6: Bootstrap `gui/solara_app.py`

The Solara entry, modeled on `/tmp/seplan/solara_app.py`. At first it renders only
the About tile (Task 7 fills in the rest). `MapApp` coexists with the legacy
ipyvuetify tiles, so we add tiles one at a time.

**Files:**
- Create: `gui/solara_app.py`

- [ ] **Step 1: Write the bootstrap**

Create `gui/solara_app.py`:
```python
from pysepal.logger import setup_logging

logger = setup_logging(logger_name="DEFORESTATION_ALERTS")

import logging

import solara
from solara.lab.components.theming import theme

from pysepal.scripts.utils import init_ee
from pysepal.sepalwidgets.vue_app import MapApp, ThemeToggle
from pysepal.solara import (
    setup_sessions,
    with_sepal_sessions,
    get_current_gee_interface,
    get_current_sepal_client,
    setup_theme_colors,
    setup_solara_server,
)
from pysepal.mapping import SepalMap

from deforestation_alerts.message import cm
from gui.store.state_manager import AppState

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

init_ee()
setup_solara_server()


@solara.lab.on_kernel_start
def init_gee():
    return setup_sessions()


@solara.component
@with_sepal_sessions(module_name="deforestation_alerts")
def Page():
    setup_theme_colors()
    theme_toggle = ThemeToggle()
    theme_toggle.observe(lambda e: setattr(theme, "dark", e["new"]), "dark")

    gee_interface = get_current_gee_interface()
    sepal_client = get_current_sepal_client()

    app_state = AppState()
    map_ = SepalMap(gee_interface=gee_interface)

    # Tiles are added here as they are ported (Tasks 7-9). Each entry's `content`
    # is a @solara.component element; About first.
    from gui.tile.about_tile import AboutTile

    steps_data = [
        {"id": 1, "name": cm.app.drawer.about, "icon": "mdi-help-circle",
         "display": "dialog", "content": AboutTile(), "width": 1300},
    ]

    MapApp.element(
        app_title="Forest Cover Loss Alerts",
        app_icon="mdi-tree",
        main_map=[map_],
        steps_data=steps_data,
        initial_step=1,
        theme_toggle=[theme_toggle],
        repo_url="https://github.com/<org>/deforestation-alerts-module",
    )
```
(Adjust `cm.app.drawer.*` keys to the actual message JSON; the icons/labels are
cosmetic and can be tuned.)

- [ ] **Step 2: Smoke-test the server starts (About not built yet → expect a clear import error)**

Run: `solara run gui/solara_app.py --port 8900 --no-open` (Ctrl-C after it binds)
Expected: it starts and, on first page load, errors on `gui.tile.about_tile` (built next). That's the green light that the bootstrap itself is wired. If it fails *before* serving (session/MapApp errors), fix those first.

- [ ] **Step 3: Commit**

```bash
git add gui/solara_app.py
git commit -m "feat: bootstrap gui/solara_app.py (pysepal session pattern + MapApp.element)"
```

---

### Task 7: Port the simplest tile — About

**Files:**
- Create: `gui/tile/about_tile.py`

- [ ] **Step 1: Write the About component**

Create `gui/tile/about_tile.py`:
```python
"""About tile as a Solara component (was the ui.ipynb TileAbout)."""

from pathlib import Path

import solara

from deforestation_alerts.message import cm


@solara.component
def AboutTile():
    about_path = Path("docs/about") / cm.about.pathname
    solara.Markdown(about_path.read_text())
```
(If `cm.about.pathname` is a language-suffixed filename like `ABOUT_en.md`, this reads
the correct localized file from the Phase-2 `docs/about/` location.)

- [ ] **Step 2: Run the app and verify About renders**

Run: `solara run gui/solara_app.py --port 8900 --no-open`, open the app, click the
About drawer item.
Expected: the localized About markdown renders inside the MapApp dialog. Take a screenshot or note success.

- [ ] **Step 3: Commit**

```bash
git add gui/tile/about_tile.py
git commit -m "feat: port About tile to a Solara component"
```

---

### Task 8: Port the medium tiles (Overview, AOI, Aux, Recipe, Filter)

Port these in order of complexity (simplest first), each followed by a live smoke
test. Each conversion applies the recipe table at the top of this plan. They share
`app_state`, `gee_interface`, and the domain models passed down from `Page()`.

> **Per-tile step template** (repeat for each tile in Steps 1–5):
> 1. Create `gui/tile/<name>_tile.py` with `@solara.component def <Name>Tile(app_state, recipe_models..., gee_interface): …`.
> 2. Convert each `self.observe(h,"x")` → `solara.use_effect(h, [reactive.value])`; each `link()` → a shared reactive; each `.hide()/.show()` → `if cond.value:`; each `v.Dialog`/grouped `v.Select` → `rv.Dialog`/`rv.Select`.
> 3. Move any GEE/DataFrame logic out of callbacks into the relevant service; keep callbacks thin (read state → call service/`use_task` → write state).
> 4. Add the tile's element to `steps_data` in `gui/solara_app.py`.
> 5. `solara run …`, exercise the tile, confirm parity with the Voila version.
> 6. `pytest tests/ -v` (services covered) + a focused commit (one per tile).

- [ ] **Step 1: Overview tile** (`overview_tile.py`, 183 lines, 2 observers)
  - Observers on `analyzed_alerts_model` `last_save_time` and `alerts_gdf` → one `use_effect` keyed on `[app_state.alerts_gdf.value]` that recomputes the summary table.
  - Stats computation moves to `overview_helper` (already a service); the component only renders.
  - Smoke: load a recipe with alerts → table + colored layers appear.

- [ ] **Step 2: AOI tile** (`aoi_tile.py`, 255 lines, 1 observer)
  - The `aux_model` `ccdc_layer` observer → `use_effect`.
  - AOI selection uses the pysepal `AoiView`/`AoiModel`; pass `gee_interface`. Watch the GAUL-2024 admin-code risk (spec §5) — if a loaded recipe's admin codes don't resolve, surface it (don't crash).
  - Smoke: select an admin AOI + dates → models update.

- [ ] **Step 3: Aux tile** (`aux_tile.py`, 75 lines, no observers)
  - Simplest after About: form inputs bound to `aux_model` traits. `PlanetView` usage (confirm unchanged per the Phase 1 guard). Custom report-template upload writes `aux_model.custom_report_template`.
  - Smoke: set CCDC/mask/aux layers + upload a template.

- [ ] **Step 4: Recipe tile** (`recipe_tile.py`, 251 lines)
  - Orchestrates save/load across models. Uses `recipe_helper` (service). Remove the dead `aoi_tile.process_alerts2()` call here or in Task 10.
  - Recipe load uses the Phase-3 `backfill_geometry` for old recipes.
  - Smoke: save a recipe, reload it, confirm round-trip (the Phase 0/3 recipe tests already guard the IO).

- [ ] **Step 5: Filter tile** (`alerts_filter_tile.py`, 704 lines, 1 observer + the Phase-3 vectorize thread)
  - Observer on `alert_filter_model.available_alerts_raster_list` → `use_effect`.
  - **Replace the Phase-3 `run_local_vectorize` thread** with `use_task(prefer_threaded=True)` (download + vectorize are CPU/IO): the task calls `raster_export.download_ee_image` then `vectorize_alert_raster`, and writes `app_state.alerts_gdf`. The "Analyze" button triggers the task; show its `.pending` as the loader.
  - Grouped alert-source `v.Select` → `rv.Select`. Draw-control for the area polygon → keep `CustomDrawControl` (`rv`-based, no native Solara equivalent — spec §5).
  - Smoke: filter by source/size/area, run analysis, confirm polygon alerts publish to `app_state.alerts_gdf`.

- [ ] **Step 6: After each tile — suite + commit**

After each tile above: `pytest tests/ -v` (PASS) and a focused commit, e.g.:
```bash
git add gui/tile/overview_tile.py gui/solara_app.py
git commit -m "feat: port Overview tile to Solara (use_effect for alerts_gdf)"
```
(Five commits total for this task — one per tile.)

---

### Task 9: Port `analysis_tile` (the god-class) onto the services

The 2054-line god-class becomes a thin `@solara.component` tree over the Part-A
services. Decompose into sub-components, each owning a slice of reactive state.

**Files:**
- Create: `gui/tile/analysis_tile.py` (rewrite), optionally `gui/tile/analysis/` sub-components.

- [ ] **Step 1: Map the god-class to components + services**

Target decomposition (each is a `@solara.component`):
| Sub-component | Replaces (old methods) | Reactive state | Service used |
|---|---|---|---|
| `AlertNavigator` | `navigate`, `view_actual_alert` trigger | `app_state.actual_alert_id` | — |
| `AlertImagery` (S2/Landsat/Planet sliders) | `start_s2/landsat_dictionary_thread`, `slider_*`, `image_slider_*` | per-source `images`/`time` reactives | `imagery_service` via `use_task(prefer_threaded=False)` |
| `CnnPanel` | `worker_m1/m2`, `check_results1/2`, `run_dl_model_1/2`, `verify_model*`, `enable_dl*`, `add/remove_model*_prediction` | `model1/2_pred`, `loading_m1/2` | `cnn_worker` via `use_task(prefer_threaded=True)` |
| `EditTools` | `start/save/stop/clear_edition_function`, `defo_dl_layer` | `editing`, `defo_layer` | — (CustomDrawControl `rv`) |
| `AlertExport` | `save_attributes_to_gdf`, `download_data`, `save_alerts_to_gdf` | — | `alert_repository`, `report_builder` |

The two map sync `link()`s become a shared `MapLocation` reactive (center/zoom) passed
to both maps (seplan pattern).

- [ ] **Step 2: Convert the CNN workers to `use_task` (the headline threading change)**

Replace the 4-thread + 4-queue machinery with:
```python
@solara.component
def CnnPanel(image_path_reactive, app_state):
    from deforestation_alerts.scripts import cnn_worker

    run_m1, set_run_m1 = solara.use_state(0)  # bump to trigger

    result_m1 = solara.lab.use_task(
        lambda: cnn_worker.apply_dl_model(image_path_reactive.value, "m1", "_m1"),
        dependencies=[run_m1], prefer_threaded=True,   # CPU-bound CNN
    )

    solara.Button("Run model 1", on_click=lambda: set_run_m1(run_m1 + 1),
                  loading=result_m1.pending, disabled=image_path_reactive.value is None)
    if result_m1.finished:
        # was: verify_model1_output observer; now plain post-task code
        _verify_and_add_prediction(result_m1.value, app_state)
    if result_m1.error:
        solara.Error(f"Model 1 failed: {result_m1.error}")
```
Do the same for model 2. No queues, no result-checker threads, no `model*_prediction_time`
trait pinging — `use_task` state replaces all of it.

- [ ] **Step 3: Convert GEE imagery fetch to `use_task(prefer_threaded=False)`**

```python
images_s2 = solara.lab.use_task(
    lambda: imagery_service.getIndividualS2(alert_geom.value, dates.value),
    dependencies=[app_state.actual_alert_id.value], prefer_threaded=False,
)
```
Where a `gee_interface.*_async` exists, make the task `async def` and `await` it
(guide §5). The S2/Landsat "slider rebuild" observers become rendering keyed on
`images_s2.value`.

- [ ] **Step 4: Convert observers → `use_effect`, links → shared reactives**

Each of the 11 `analysis_tile` observers maps to a `use_effect` keyed on the
corresponding reactive (table in Step 1). The 5 `link()`s: map center/zoom → one
shared `MapLocation`; the tab syncs → shared `solara.reactive` for `v_model`; the draw
sync → one shared draw-data reactive bound to both `CustomDrawControl`s.

- [ ] **Step 5: Use `alert_repository.merge_reviewed_into_full` for the gdf merge**

Replace `create_gdf_partial`/`create_gdf_full` with calls to the pure service from
Task 4, writing the result to `app_state.alerts_gdf`. The cross-thread race is gone
(no shared mutation; `use_task` results flow through reactives).

- [ ] **Step 6: Add `AnalysisTile` to `steps_data` and smoke-test end to end**

Wire the component into `gui/solara_app.py`. Run the app; for a real alert: navigate,
load S2/Landsat imagery, run both CNN models, edit a polygon, save attributes, and
download the report. Confirm parity with the Voila version. This is the acceptance
gate for the whole phase.

- [ ] **Step 7: Suite + commit**

Run: `pytest tests/ -v` → PASS (services covered by Tasks 3–5).
```bash
git add gui/tile/analysis_tile.py gui/tile/analysis/ gui/solara_app.py 2>/dev/null; git add -A
git commit -m "feat: port analysis_tile to Solara components over cnn_worker/imagery_service/alert_repository"
```

---

# PART C — Cleanup + remove Voila

### Task 10: Opportunistic tech-debt fixes

**Files:**
- Modify: `deforestation_alerts/scripts/mosaics_helper.py`, `overview_helper.py`, `recipe_helper.py`, and any remaining `model_worker.py`

- [ ] **Step 1: De-duplicate `convert_julian_to_date`**

It is identical in `mosaics_helper.py` (≈18–28) and `overview_helper.py` (≈62–72).
Keep one (in `mosaics_helper`), import it in `overview_helper`, delete the copy.
Add a tiny test:
```python
# tests/test_date_utils.py
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date

def test_julian_to_date():
    assert convert_julian_to_date(2023.001) == "2023-01-01"
    assert convert_julian_to_date(2020.060) == "2020-02-29"  # leap day
```

- [ ] **Step 2: Remove the dead `process_alerts2()` call**

In `recipe_helper.load_parameters_from_json` (≈ line 116) delete the call to the
non-existent `aoi_tile.process_alerts2()` (spec tech-debt: dead path). Confirm the
load path is exercised by the recipe round-trip test.

- [ ] **Step 3: Delete the now-unused `model_worker.py` shim**

If Task 3 left `model_worker.py` as a re-export shim and nothing imports it after the
Task 9 rewrite, delete it.
```bash
grep -rn "model_worker" deforestation_alerts/ gui/ tests/ || echo "no model_worker refs"
```
Delete the file if the grep is empty.

- [ ] **Step 4: Run tests + commit**

Run: `pytest tests/ -v` → PASS.
```bash
git add -A
git commit -m "refactor: de-dup convert_julian_to_date, drop dead process_alerts2 + model_worker shim"
```

---

### Task 11: Remove the Voila entry and switch run/CI to Solara

Only after every tile renders under Solara (the spec's exit condition).

**Files:**
- Modify: `noxfile.py`, `pyproject.toml`, `requirements.txt`, `sepal_environment.yml`, `.github/workflows/ci.yaml`
- Remove: `ui.ipynb` (and `about_ui.ipynb`/`no_ui.ipynb` if unused)

- [ ] **Step 1: Confirm full Solara parity first**

Manually verify the whole workflow runs under `solara run gui/solara_app.py`: About,
AOI+dates, aux layers, recipe save/load, filter+analyze (polygon alerts), analysis
(imagery, both CNNs, edit, save, report), overview. Only proceed when all pass. If any
tile is incomplete, STOP — finish it (Tasks 7–9) before removing Voila.

- [ ] **Step 2: Point `noxfile.py app` session at Solara**

Replace the `app` session body:
```python
@nox.session(reuse_venv=True)
def app(session):
    """Run the Solara application."""
    session.install("-r", "requirements.txt")
    session.run("solara", "run", "gui/solara_app.py", "--port", "8900", "--no-open")
```
Remove the `tomli.load(...)["sepal-ui"]["init-notebook"]` line and the Voila call.

- [ ] **Step 3: Update `pyproject.toml` entry metadata**

Replace the `[sepal-ui]` table's `init-notebook = "ui.ipynb"` with the Solara entry
convention the deployment uses (confirm the SEPAL platform key for Solara modules;
se.plan deploys via `supervisord.conf` running `solara run solara_app.py`, so the
`[sepal-ui] init-notebook` key may no longer be needed). Keep `[tool.pytest…]`,
`[tool.ruff…]`, `[tool.codespell…]`.

- [ ] **Step 4: Drop Voila from deps**

Remove the `voila==0.5.8` line from `requirements.txt` and `sepal_environment.yml`.
Keep `jupyter`/`ipykernel` only if still needed by tooling.

- [ ] **Step 5: Remove the notebooks**

```bash
git rm ui.ipynb
git rm about_ui.ipynb no_ui.ipynb   # only if confirmed unused after the port
```

- [ ] **Step 6: Replace the CI notebook checks with a Solara import/health check**

In `.github/workflows/ci.yaml`, remove the "Verify notebook kernelspec" and
"Test UI notebook" (nbmake) steps; add a Solara smoke step:
```yaml
      - name: Solara app imports
        shell: micromamba-shell {0}
        run: python -c "import gui.solara_app; print('solara app import ok')"
```
Keep the ee-fork check and the `pytest tests/` unit step.

- [ ] **Step 7: Validate CI YAML + full suite + app start**

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('yaml ok')"
pytest tests/ -v
python -c "import gui.solara_app; print('solara app import ok')"
```
Expected: `yaml ok`, tests PASS, `solara app import ok`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "build: remove Voila entry; run + CI on Solara only"
```

---

## Definition of done (Phase 4)

- [ ] `solara run gui/solara_app.py` serves the full app; every tile (About, AOI, Aux, Recipe, Filter, Analysis, Overview) renders and works.
- [ ] No `threading.Thread`/`queue.Queue` in `gui/` — concurrency is `use_task` (GEE: `prefer_threaded=False`; CPU: `prefer_threaded=True`).
- [ ] No `.observe(`/`link(` in `gui/` — replaced by `use_effect`/shared reactives (`grep -rn '\.observe(\|link(' gui/` returns nothing).
- [ ] `analysis_tile` god-class is gone; CNN logic lives only in `cnn_worker`, imagery in `imagery_service`, gdf merge in `alert_repository`.
- [ ] Services contain **no** `solara`/`ipyvuetify`/`reacton` imports (`grep -rn 'import solara\|ipyvuetify\|reacton' deforestation_alerts/scripts/` returns nothing).
- [ ] `AppState` holds ephemeral UI/nav state; serialized domain state stays on `Model` subclasses (D8).
- [ ] Tech-debt fixed: `convert_julian_to_date` de-duped, dead `process_alerts2` removed, CNN race gone.
- [ ] Voila entry removed; `noxfile`/CI run Solara; `pytest tests/ -v` green.

---

## Self-review notes (author)

- **Spec coverage:** Implements spec §8 Phase 4 steps (a)–(e), the §4.2 layer
  contract, the §4.3 concurrency model, and D1 (full Solara), D3 (`cnn_worker`), D8
  (`AppState`). Also lands the §8 opportunistic tech-debt list (CNN consolidation,
  `convert_julian_to_date` de-dup, dead `process_alerts2`, the `create_gdf_full` race).
- **Sequencing keeps the app live:** Part A extracts services + state without touching
  the live Voila UI (suite stays green); Part B ports tiles one at a time under
  `MapApp` (which renders ipyvuetify + Solara together, spec §8); Part C removes Voila
  only after full parity (Task 11 Step 1 gate).
- **Plan altitude (declared):** net-new testable units (`AppState`, `cnn_worker`,
  `alert_repository`) get full TDD; the view rewrites (Tasks 7–9) give the exact
  conversion recipe + full code for small tiles + concrete component/state
  decomposition for the big ones, with the live smoke test as the acceptance gate.
  Reproducing the 2054-line `analysis_tile` verbatim is intentionally not done — the
  decomposition table (Task 9 Step 1) is the contract; the per-sub-component reactive
  state and service mapping are specified.
- **Verify-early risks carried from spec §5:** pin a known-good pysepal (3.6.1 or fall
  back to 3.5.0); use `MapApp.element()` (not the unshipped `MapAppComponent`); GAUL-2024
  admin-code churn surfaces in the AOI tile (Task 8 Step 2) and is handled with the
  Phase-3 recipe backfill; `PlanetView` confirmed in the Phase-1 guard test.
- **Open item flagged, not guessed:** the exact SEPAL deployment key for a Solara
  module (Task 11 Step 3) — se.plan deploys via supervisord running `solara run`, so
  the `[sepal-ui] init-notebook` key likely becomes unused; confirm against the SEPAL
  platform docs before deleting it, rather than assuming.
- **Type consistency:** `AppState.alerts_gdf` (reactive) ↔ `app_state.alerts_gdf.value`
  (the gdf) used consistently across Tasks 2, 8, 9; `cnn_worker.apply_dl_model(path,
  which, suffix)` signature is consistent between Task 3's definition and Task 9's
  `use_task` call; `merge_reviewed_into_full(current, full, actual_alert_id)` matches
  between Task 4 and Task 9 Step 5.
