# Phase 0–1: Safety Net + pysepal Rename — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish an automated test safety net around the current app's pure logic (Phase 0), then migrate the framework dependency `sepal_ui==2.22.1` → `pysepal==3.6.1` with no behavior change (Phase 1).

**Architecture:** Phase 0 adds a `pytest` suite covering the *pure, GEE-free, UI-free* functions that encode the app's load-bearing contracts (alert-code decoding, recipe serialization, report text formatting). These tests are the regression guard for every later phase. Phase 1 then swaps the dependency: first proving `pysepal`'s backward-compatibility shim runs the app unchanged, then replacing `from sepal_ui …` with `from pysepal …` across 16 `.py` files and 3 notebooks, with the Phase 0 tests staying green throughout.

**Tech Stack:** Python, pytest, sepal_ui/pysepal (ipyvuetify + traitlets), geopandas/shapely, Earth Engine (not called in these tests). This plan keeps the legacy `component/` layout (folder restructure is Phase 2).

**Scope note:** This is the first of several per-phase plans derived from
`docs/superpowers/specs/2026-06-06-pysepal-solara-migration-design.md`. Phases 2
(restructure), 3 (raster pipeline), and 4 (Solara) get their own plans later.

**A note on characterization tests:** The functions under test in Phase 0 already
exist. We are *pinning their current behavior*, not test-driving new code. So the
usual "write a failing test first" step is replaced by: write the test asserting
the behavior derived from reading the code, run it, and expect **PASS**. If it
fails, the real output differs from what we read — update the assertion to the
*actual* observed value (that is the characterization) and note it. Do not change
production code to make a Phase 0 test pass.

---

## File structure (created/modified in this plan)

| File | Phase | Responsibility |
|---|---|---|
| `pyproject.toml` | 0 | Add `[tool.pytest.ini_options]` (pythonpath, testpaths). Modify. |
| `tests/conftest.py` | 0 | Shared fixtures (currently minimal). Create. |
| `tests/test_report_builder.py` | 0 | Pin `get_unique_alerts`, `format_list`, `parse_formatted_string`, `ensure_list`. Create. |
| `tests/test_recipe_io.py` | 0 | Pin `load_gdf_from_csv`, `update_saved_dictionary`, `save_model_parameters_to_json`. Create. |
| `tests/test_models.py` | 0 | Pin model `export_dictionary`/`import_from_dictionary` round-trips. Create. |
| `.github/workflows/ci.yaml` | 0 | Add a "Run unit tests" step. Modify. |
| `tests/test_pysepal_api.py` | 1 | Guard: pysepal exposes the AoiModel private setters + PlanetView this app depends on. Create. |
| `requirements.txt` | 1 | `sepal_ui==2.22.1` → `pysepal==3.6.1`. Modify line 21. |
| `sepal_environment.yml` | 1 | Same bump. Modify line 37. |
| `component/**/*.py` (16 files) | 1 | `from sepal_ui …` → `from pysepal …`. Modify. |
| `ui.ipynb`, `no_ui.ipynb`, `about_ui.ipynb` | 1 | Same import swap inside notebook JSON. Modify. |

---

# PHASE 0 — Safety net (tests-first)

### Task 1: Test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest config to `pyproject.toml`**

Append this block to `pyproject.toml` (after the existing `[tool.ruff]`/`[tool.codespell]` sections):

```toml
[tool.pytest.ini_options]
# Put the repo root on sys.path so `import component...` works without installing the app.
pythonpath = ["."]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures for the deforestation-alerts test suite.

Phase 0 tests are pure: no Earth Engine calls, no UI rendering. We deliberately
do NOT call init_ee() here so the suite runs offline in CI without credentials.
"""

import pytest


@pytest.fixture()
def sample_wkt_csv(tmp_path):
    """Write a tiny CSV with a WKT geometry column and return its path.

    Mirrors the shape of the recipe `alert_db.csv` that `load_gdf_from_csv`
    reads: a geometry column stored as WKT strings plus a plain attribute column.
    """
    csv_path = tmp_path / "alert_db.csv"
    csv_path.write_text(
        "id,point\n"
        "0,POINT (1 2)\n"
        "1,\n"  # empty geometry -> should decode to None
    )
    return csv_path
```

- [ ] **Step 3: Verify pytest collects an empty suite**

Run: `pytest -q`
Expected: `no tests ran` (exit code 5 is acceptable here) — confirms config loads and `tests/` is discovered. If you see an import/config error, fix it before continuing.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/conftest.py
git commit -m "test: add pytest infrastructure and shared fixtures"
```

---

### Task 2: Pin the alert-code decoder (`get_unique_alerts`)

This is the single most important characterization test: `get_unique_alerts`
decodes alert-source codes packed into decimal digits (GLAD-L=ones, RADD=tens,
GLAD-S2=hundreds, CCDC=thousands). Phase 3 (raster export + local vectorize) must
preserve this exact integer encoding, so this test is the contract Phase 3 is
checked against.

**Files:**
- Create: `tests/test_report_builder.py`

- [ ] **Step 1: Write the characterization test**

```python
"""Characterization tests for component/scripts/report_builder.py pure helpers."""

import pytest

from component.scripts.report_builder import (
    get_unique_alerts,
    format_list,
    parse_formatted_string,
    ensure_list,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, {"GLAD-L"}),          # ones digit
        (10, {"RADD"}),           # tens digit
        (100, {"GLAD-S2"}),       # hundreds digit
        (1000, {"CCDC"}),         # thousands digit
        (1111, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 1
        (2222, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 2
        (0, set()),               # no source triggered
    ],
)
def test_get_unique_alerts_decodes_digit_encoding(value, expected):
    assert set(get_unique_alerts([value])) == expected


def test_get_unique_alerts_accepts_stringified_list():
    # Recipe storage round-trips this column as a string; the decoder must parse it.
    assert set(get_unique_alerts("[1, 10]")) == {"GLAD-L", "RADD"}


def test_get_unique_alerts_invalid_string_returns_empty():
    assert get_unique_alerts("not a list") == []
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_report_builder.py -v`
Expected: PASS (all parametrized cases + the two string cases). If any case fails, the real decoding differs from the documented digit rule — record the actual `set(...)` output as the expected value and note the discrepancy in the commit message.

- [ ] **Step 3: Add the text-formatting characterization tests**

Append to `tests/test_report_builder.py`:

```python
def test_format_list_join_rules():
    assert format_list([]) == ""
    assert format_list(["GLAD-L"]) == "GLAD-L"
    assert format_list(["GLAD-L", "RADD"]) == "GLAD-L and RADD"
    assert format_list(["GLAD-L", "RADD", "CCDC"]) == "GLAD-L, RADD and CCDC"


def test_parse_formatted_string_is_inverse_of_format_list():
    for items in (["GLAD-L"], ["GLAD-L", "RADD"], ["GLAD-L", "RADD", "CCDC"]):
        assert parse_formatted_string(format_list(items)) == items
    assert parse_formatted_string("") == []


def test_ensure_list():
    assert ensure_list("x") == ["x"]
    assert ensure_list(["x", "y"]) == ["x", "y"]
    with pytest.raises(ValueError):
        ensure_list(123)
```

- [ ] **Step 4: Run the full file**

Run: `pytest tests/test_report_builder.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_report_builder.py
git commit -m "test: characterize alert-code decoder and report text helpers"
```

---

### Task 3: Pin recipe geometry decode + JSON update

**Files:**
- Create: `tests/test_recipe_io.py`

- [ ] **Step 1: Write `load_gdf_from_csv` characterization test**

```python
"""Characterization tests for component/scripts/recipe_helper.py pure I/O."""

import json
from pathlib import Path

from shapely.geometry import Point

from component.scripts.recipe_helper import (
    load_gdf_from_csv,
    update_saved_dictionary,
)


def test_load_gdf_from_csv_decodes_wkt_geometry(sample_wkt_csv):
    gdf = load_gdf_from_csv(str(sample_wkt_csv), ["point"])
    # WKT string decoded into a shapely Point
    first = gdf["point"].iloc[0]
    assert isinstance(first, Point)
    assert (first.x, first.y) == (1.0, 2.0)
    # empty geometry cell decodes to None (not an error)
    assert gdf["point"].iloc[1] is None
    # non-geometry column is left untouched
    assert list(gdf["id"]) == [0, 1]
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_recipe_io.py -v`
Expected: PASS.

- [ ] **Step 3: Write `update_saved_dictionary` characterization test**

Append to `tests/test_recipe_io.py`:

```python
def test_update_saved_dictionary_overwrites_and_adds_keys(tmp_path):
    p = tmp_path / "recipe_parameters.json"
    p.write_text(json.dumps({"actual_alert_id": 0}))

    update_saved_dictionary(str(p), "actual_alert_id", 5)
    update_saved_dictionary(str(p), "recipe_name", "recipe_test")

    data = json.loads(p.read_text())
    assert data["actual_alert_id"] == 5          # overwritten
    assert data["recipe_name"] == "recipe_test"  # added
```

- [ ] **Step 4: Run it**

Run: `pytest tests/test_recipe_io.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_recipe_io.py
git commit -m "test: characterize recipe geometry decode and json update"
```

---

### Task 4: Pin model serialization round-trips

The recipe save/load contract is built on each model's `export_dictionary` /
`import_from_dictionary`. These tests pin the keys and round-trip behavior of the
GEE-free models. (`AoiDateModel` is intentionally excluded — instantiating/loading
it touches Earth Engine; it is covered indirectly via the stub in Task 5.)

**Files:**
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the model round-trip tests**

```python
"""Characterization tests for model export/import dictionaries (GEE-free models)."""

from component.model.app_model import AppTileModel
from component.model.aux_model import AuxModel
from component.model.alerts_model import SelectedAlertsModel
from component.model.analysis_model import AlertAnalysisModel


def test_app_tile_model_roundtrip():
    m = AppTileModel()
    m.recipe_name = "recipe_test"
    m.recipe_folder_path = "/tmp/recipe_test"
    m.temporary_recipe_name = "tmp"
    exported = m.export_dictionary()
    assert exported["recipe_name"] == "recipe_test"

    loaded = AppTileModel()
    loaded.import_from_dictionary(exported)
    assert loaded.recipe_name == "recipe_test"
    assert loaded.recipe_folder_path == "/tmp/recipe_test"
    assert loaded.temporary_recipe_name == "tmp"


def test_aux_model_roundtrip():
    m = AuxModel()
    m.aux_layer = "projects/x/aux"
    m.aux_layer_vis = {"palette": ["#000000"]}
    exported = m.export_dictionary()
    assert set(exported) == {
        "ccdc_layer", "mask_layer", "aux_layer", "aux_layer_vis",
        "custom_report_template",
    }

    loaded = AuxModel()
    loaded.import_from_dictionary(exported)
    assert loaded.aux_layer == "projects/x/aux"
    assert loaded.aux_layer_vis == {"palette": ["#000000"]}


def test_selected_alerts_model_export_keys_and_numeric_codes():
    m = SelectedAlertsModel()
    m.alert_selection_area_n = 2
    m.alert_sorting_method_n = 1
    exported = m.export_dictionary()
    # the export deliberately stores the *numeric* (language-independent) codes
    assert exported["alert_selection_area"] == 2
    assert exported["alert_sorting_method"] == 1
    assert set(exported) == {
        "selected_alert_sources", "alert_selection_polygons", "min_area",
        "max_number_alerts", "alert_selection_area", "alert_sorting_method",
    }


def test_analysis_model_export_defaults_and_id_clamp():
    m = AlertAnalysisModel()
    # default actual_alert_id is -1; export clamps it to 0
    exported = m.export_dictionary()
    assert exported["actual_alert_id"] == 0
    assert exported["max_alert_id"] == 0
    assert exported["last_save_time"] == 0.0

    # import restores actual_alert_id and max_alert_id verbatim
    m.import_from_dictionary({"actual_alert_id": 7, "max_alert_id": 9,
                              "last_save_time": 123.0})
    assert m.actual_alert_id == 7
    assert m.max_alert_id == 9
    assert m.last_save_time == 123.0
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/test_models.py -v`
Expected: PASS. (If `test_analysis_model_export_defaults_and_id_clamp` fails on `max_alert_id`, note: `export_dictionary` currently derives `max_alert_id` from `actual_alert_id` — record the observed value; this is a known quirk to preserve, not fix, in Phase 0.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_models.py
git commit -m "test: characterize model export/import round-trips"
```

---

### Task 5: Pin the recipe-merge writer (`save_model_parameters_to_json`)

`save_model_parameters_to_json` merges five models' dictionaries (`a|b|c|d|e`) and
writes the recipe JSON. It ignores its `alert_filter_model` argument. We pin this
with real GEE-free models plus a lightweight stub for the GEE-coupled
`aoi_date_model`, so the test stays offline.

**Files:**
- Modify: `tests/test_recipe_io.py`

- [ ] **Step 1: Add the merge-writer test**

Append to `tests/test_recipe_io.py`:

```python
from component.model.aux_model import AuxModel
from component.model.alerts_model import SelectedAlertsModel
from component.model.analysis_model import AlertAnalysisModel
from component.model.app_model import AppTileModel
from component.scripts.recipe_helper import save_model_parameters_to_json


class _AoiDateStub:
    """Stands in for AoiDateModel so the test never touches Earth Engine."""

    def export_dictionary(self):
        return {"aoi_method": "ADMIN0", "aoi_admin": ["CIV"]}


def test_save_model_parameters_merges_all_dicts(tmp_path):
    app = AppTileModel()
    app.recipe_name = "recipe_test"
    out = tmp_path / "recipe_parameters.json"

    path = save_model_parameters_to_json(
        str(out),
        AuxModel(),          # aux_model        -> contributes ccdc_layer, ...
        _AoiDateStub(),      # aoi_date_model   -> contributes aoi_method, aoi_admin
        None,                # alert_filter_model -> intentionally unused
        SelectedAlertsModel(),  # selected_alerts_model
        AlertAnalysisModel(),   # analyzed_alerts_model
        app,                 # app_tile_model   -> contributes recipe_name, ...
    )

    data = json.loads(Path(path).read_text())
    # one representative key from each contributing model
    assert "ccdc_layer" in data                # AuxModel
    assert data["aoi_method"] == "ADMIN0"      # AoiDate stub
    assert "selected_alert_sources" in data    # SelectedAlertsModel
    assert data["actual_alert_id"] == 0        # AlertAnalysisModel (clamped)
    assert data["recipe_name"] == "recipe_test"  # AppTileModel
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_recipe_io.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 3: Commit**

```bash
git add tests/test_recipe_io.py
git commit -m "test: characterize recipe-merge writer save_model_parameters_to_json"
```

---

### Task 6: Wire unit tests into CI

**Files:**
- Modify: `.github/workflows/ci.yaml`

- [ ] **Step 1: Add a unit-test step before the notebook test**

In `.github/workflows/ci.yaml`, insert this step immediately **before** the
existing `- name: Verify notebook kernelspec` step (it reuses the micromamba env;
pytest is already installed by the later "Install test deps" step, so move that
install up or add pytest here). Concretely, add:

```yaml
      - name: Run unit tests
        shell: micromamba-shell {0}
        run: |
          pip install pytest
          pytest tests/ -v
```

- [ ] **Step 2: Validate the YAML locally**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Run the full suite once more locally**

Run: `pytest tests/ -v`
Expected: PASS (every test from Tasks 2–5).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: run unit test suite in CI"
```

---

# PHASE 1 — pysepal rename (no behavior change)

> Phase 0 tests are the guard. They MUST stay green after every task below.

### Task 7: Guard test for pysepal's compatibility surface

`AoiDateModel` subclasses `AoiModel` and reuses its **private** setters
(`_from_admin/_from_vector/_from_geo_json/_from_asset`), and `aux_tile`/`recipe_tile`
use `PlanetView`. None of these are guaranteed public API. This guard test fails
loudly if pysepal dropped them — surfacing the risk before we touch any imports.

**Files:**
- Create: `tests/test_pysepal_api.py`

- [ ] **Step 1: Write the guard test**

```python
"""Guard tests: pysepal must expose the (semi-private) surface this app relies on.

If any of these fail after the dependency bump, the rename is NOT a drop-in and the
affected code (AoiDateModel / aux_tile / recipe_tile) needs explicit porting.
"""


def test_pysepal_aoi_model_exposes_private_setters():
    from pysepal.aoi.aoi_model import AoiModel

    for attr in ("_from_admin", "_from_vector", "_from_geo_json", "_from_asset"):
        assert hasattr(AoiModel, attr), f"AoiModel is missing {attr}"


def test_pysepal_planetview_importable():
    from pysepal.planetapi import PlanetView  # noqa: F401


def test_pysepal_core_imports():
    from pysepal import model, color  # noqa: F401
    from pysepal import sepalwidgets as sw  # noqa: F401
    from pysepal.scripts import utils as su  # noqa: F401
    from pysepal.scripts.utils import init_ee  # noqa: F401
    from pysepal.mapping import SepalMap  # noqa: F401
    from pysepal.translator import Translator  # noqa: F401
    from pysepal.frontend.resize_trigger import rt  # noqa: F401
```

- [ ] **Step 2: Run it — expect FAIL (pysepal not installed yet)**

Run: `pytest tests/test_pysepal_api.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'pysepal'`. This confirms the guard is wired; Task 8 installs pysepal and makes it pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pysepal_api.py
git commit -m "test: guard pysepal compatibility surface (AoiModel setters, PlanetView)"
```

---

### Task 8: Bump the dependency and verify the shim runs the app unchanged

We do NOT change imports yet. pysepal ships a `sepal_ui` shim, so the app must run
as-is (with a DeprecationWarning). This isolates "does pysepal work here" from "did
the import rename break something."

**Files:**
- Modify: `requirements.txt` (line 21), `sepal_environment.yml` (line 37)

- [ ] **Step 1: Edit `requirements.txt`**

Change line 21 from:

```
sepal_ui==2.22.1
```

to:

```
pysepal==3.6.1
```

- [ ] **Step 2: Edit `sepal_environment.yml`**

Change line 37 from `      - sepal_ui==2.22.1` to `      - pysepal==3.6.1`.

- [ ] **Step 3: Install the new dependency**

Run: `pip install pysepal==3.6.1`
Expected: installs successfully.

> **Risk to check now (earthengine fork):** `requirements.txt` pins a custom
> `earthengine-api` fork (CI asserts `ee.__version__ == '1.1.5rc0'`). If pysepal's
> install downgraded/replaced `ee`, re-pin the fork after installing pysepal and
> re-run the check below. If pysepal 3.6.1 hard-conflicts, fall back to
> `pysepal==3.5.0` (the version se.plan pins) and re-run Task 7's guard test.

- [ ] **Step 4: Confirm the earthengine fork is intact**

Run: `python -c "import ee; print(ee.__version__)"`
Expected: `1.1.5rc0` (the pinned fork). If different, reinstall the fork line from `requirements.txt` and re-check.

- [ ] **Step 5: Make the Task 7 guard test pass**

Run: `pytest tests/test_pysepal_api.py -v`
Expected: PASS. If `test_pysepal_aoi_model_exposes_private_setters` or
`test_pysepal_planetview_importable` fails, STOP — record which symbol is gone; that
code needs explicit porting and should be raised before proceeding.

- [ ] **Step 6: Confirm the shim still imports the app's modules unchanged**

Run: `python -c "import warnings; warnings.simplefilter('ignore'); from component.model.app_model import AppTileModel; print('shim import ok')"`
Expected: `shim import ok` (a DeprecationWarning is emitted and suppressed).

- [ ] **Step 7: Run the full Phase 0 suite under pysepal**

Run: `pytest tests/ -v`
Expected: PASS (Phase 0 tests + the 3 guard tests).

- [ ] **Step 8: Commit**

```bash
git add requirements.txt sepal_environment.yml
git commit -m "build: bump sepal_ui 2.22.1 -> pysepal 3.6.1 (shim verified)"
```

---

### Task 9: Replace `sepal_ui` imports with `pysepal` in the package

All 16 `.py` files import `sepal_ui` only in import statements (verified: no string
/ config-key usages of `sepal_ui` elsewhere in `component/`). A scoped text
replacement is safe; the verification grep at the end proves it.

**Files:**
- Modify: every `*.py` under `component/` that matches `sepal_ui` (16 files:
  `component/message/__init__.py`, `component/model/{alerts,analysis,aoi_date,app,aux}_model.py`,
  `component/scripts/{alert_filter_helper,aoi_helper,overview_helper}.py`,
  `component/tile/{alerts_filter_tile,analysis_tile,aoi_tile,aux_tile,overview_tile,recipe_tile}.py`,
  `component/widget/custom_sw.py`).

- [ ] **Step 1: Replace the imports across the package**

Run:

```bash
grep -rlE 'sepal_ui' component/ --include='*.py' \
  | xargs sed -i 's/\bsepal_ui\b/pysepal/g'
```

- [ ] **Step 2: Verify zero `sepal_ui` references remain in the package**

Run: `grep -rn 'sepal_ui' component/ --include='*.py'`
Expected: **no output** (exit code 1). If anything prints, inspect and fix it by hand.

- [ ] **Step 3: Verify the rewritten imports parse and resolve**

Run: `python -c "import warnings; warnings.simplefilter('error', DeprecationWarning); from component.tile import *; from component.widget import *; print('pysepal imports ok')"`
Expected: `pysepal imports ok` with **no** DeprecationWarning raised (proves nothing still routes through the shim). If a DeprecationWarning is raised, a `sepal_ui` import was missed — grep again.

- [ ] **Step 4: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS (no behavior change).

- [ ] **Step 5: Commit**

```bash
git add component/
git commit -m "refactor: import pysepal instead of sepal_ui across component package"
```

---

### Task 10: Replace `sepal_ui` imports in the notebooks + final smoke test

**Files:**
- Modify: `ui.ipynb`, `no_ui.ipynb`, `about_ui.ipynb`

- [ ] **Step 1: Replace imports inside the notebook JSON**

`.ipynb` files are JSON; the import strings live in cell `source` arrays. A raw
text replace is safe (the only `sepal_ui` occurrences are import lines and a
harmless `sepal_ui/issues/893` comment URL):

```bash
for nb in ui.ipynb no_ui.ipynb about_ui.ipynb; do
  [ -f "$nb" ] && sed -i 's/sepal_ui/pysepal/g' "$nb"
done
```

- [ ] **Step 2: Verify notebooks are still valid JSON and free of `sepal_ui`**

Run:

```bash
python -c "import json; [json.load(open(f)) for f in ['ui.ipynb','no_ui.ipynb','about_ui.ipynb']]; print('notebooks valid json')"
grep -l 'sepal_ui' ui.ipynb no_ui.ipynb about_ui.ipynb || echo "no sepal_ui left in notebooks"
```
Expected: `notebooks valid json` then `no sepal_ui left in notebooks`.

- [ ] **Step 3: Confirm the CI kernelspec invariant still holds**

Run:

```bash
python3 -c "import json; nb=json.load(open('ui.ipynb')); print(nb['metadata']['kernelspec']['name'])"
```
Expected: `venv-deforestation-alerts-module` (unchanged — the sed must not have altered it; if it did, restore that metadata value).

- [ ] **Step 4: Smoke-test the entry notebook end-to-end (requires GEE creds)**

Run: `pytest --nbmake ui.ipynb --nbmake-timeout=600`
Expected: PASS (the app notebook executes top to bottom under pysepal). If credentials are unavailable locally, defer this to CI and note it; do not skip it silently.

- [ ] **Step 5: Run the unit suite a final time**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add ui.ipynb no_ui.ipynb about_ui.ipynb
git commit -m "refactor: import pysepal instead of sepal_ui in notebooks"
```

---

## Definition of done (Phases 0–1)

- [ ] `pytest tests/ -v` passes (Phase 0 characterization + Phase 1 guard tests).
- [ ] `grep -rn 'sepal_ui' component/ --include='*.py'` returns nothing.
- [ ] No `sepal_ui` references remain in the three notebooks.
- [ ] `ee.__version__ == '1.1.5rc0'` (earthengine fork preserved).
- [ ] CI runs the unit suite + the notebook smoke test.
- [ ] App behavior is unchanged (no feature added or removed).

---

## Self-review notes (author)

- **Spec coverage:** Implements spec §7 Tier-1 testing (Phase 0) and §8 Phase 1
  (rename), including the §5 verify-early risks (AoiModel private setters, PlanetView)
  as the Task 7 guard, and the earthengine-fork conflict risk (Task 8). Phase 1's
  GAUL-code/recipe-compat risk is acknowledged but belongs to Phase 3 (recipe loader)
  — not in scope here; no saved-recipe loading path is exercised by these tests.
- **Out of scope (correct):** folder restructure (Phase 2), raster pipeline (Phase 3),
  Solara (Phase 4). The `pyproject.toml [sepal-ui]` table and `noxfile.py` keep their
  names until Phase 2.
- **Known quirks preserved, not fixed:** `AlertAnalysisModel.export_dictionary`
  derives `max_alert_id` from `actual_alert_id`; `aux_model`/`app_model`
  `import_from_dictionary` reference `json` without importing it (latent, untriggered
  on the happy path). Phase 0 pins current behavior; fixes belong to later phases.
