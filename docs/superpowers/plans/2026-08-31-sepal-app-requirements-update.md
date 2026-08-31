# SEPAL App Requirements Update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `deforestation-alerts-module` into compliance with the current SEPAL app requirements defined by the `pysepal` and `pysepal-app` skills (`~/Desktop/projects/pysepal/skills/`), serving BOTH deployment targets like spatial_risk — a voila entry for the classic SEPAL path and Solara serving (dev + app-launcher container) — plus one user-requested functional fix (browser-side two-map sync).

**Architecture:** The app already has the target shell (MapApp.element, session-backed GEE, theme_state, STEPS registry, `solara.reactive` AppState, i18n, Tier-1 widgets) and its own Dockerfile/supervisord deployment. This plan closes the remaining gaps: dependency/CI spec compliance, pysepal 3.6.1→3.8.0, GDAL/CWD hardening, centralized notifications built on a corrected task lifecycle, GEE-pattern conformance, browser-side map sync, and the container-family SepalClient user-file migration. It does NOT rewrite the working raw-ipyvuetify tile layouts.

**Tech Stack:** pysepal 3.8.0, Solara 1.57.4, ipyvuetify <3, GEE (openforis earthengine-api build), voila (classic SEPAL path) + Docker/supervisord (app-launcher), micromamba/conda-forge, pytest.

**Spec:** The requirement sources travel with this plan:
- `~/Desktop/projects/pysepal/skills/pysepal/SKILL.md` (component rules, GEE patterns, audit checklist)
- `~/Desktop/projects/pysepal/skills/pysepal-app/SKILL.md` + `references/` (`defaults.md`, `file-matrix.md`, `validation-checklist.md`, `sepal-deployment.md`, `existing-repo-rules.md`)
- Reference implementation (user-approved for code reuse): `~/Desktop/projects/spatial_risk/spatial-risk-module/`
- pysepal checkout for guides + discovery: `~/Desktop/projects/pysepal/`
- Codex review of the plan draft: findings incorporated 2026-09-01 (`/tmp/.../scratchpad/codex-review-full.txt` during the session; the substance is folded into the tasks below).

## Decisions (locked before task work — each cites its evidence)

1. **Deployment: dual-target, like spatial_risk (user decision, 2026-09-01) — voila AND Solara.** The app keeps BOTH entry paths: a thin voila `ui.ipynb` for the classic SEPAL module path (restored in Task 4b — the changelog shows it was removed in the Solara migration) and the existing Solara serving (`run_solara.sh` for dev, `gui/Dockerfile` + `gui/supervisord.conf` for the app-launcher, which run `solara run gui/solara_app.py --root-path=/api/app-launcher/deforestation-alerts --production`). pysepal 3.8's `with_sepal_sessions`/`create_session` are runtime-aware (solara-server: auth headers; voila: sandbox credentials), so one `Page` serves both. Consequences:
   - ALL of `sepal-deployment.md` applies: env-spec/pinning rules, read-only-CWD rules, AND the voila-entry/kernelspec rules (Task 4b).
   - The container-family **User File Rules apply to the container target**: runtime user-file IO goes through the session `SepalClient` (Phase 6) — in the app-launcher container, `Path.home()` is not the user's SEPAL workspace. The same `SepalClient` code path works under voila-in-sandbox too (that is exactly what the skill's "no local/dev branching" rule wants), so Phase 6 keeps one correct code path for both targets. See the Phase 6 gate note.
   - AOI SHAPE/POINTS methods are excluded — the lowest common denominator: SHAPE works in a sandbox but reads container-local paths in the app-launcher target, and one codebase must serve both.
   - `docker-compose.yml` is added (file-matrix default for the container family).
2. **Existing raw-ipyvuetify tile layouts stay.** `aux_tile`, `recipe_tile`, `analysis/*` build raw widgets for documented MapApp dialog-survival reasons. The "no traitlets architectures" rule governs *new* scaffolding; rewriting these violates `existing-repo-rules.md`. Feedback in them uses the notification escape hatches (`notify()` / `track_task()`), which work outside Solara components.
3. **GEE transport: sync API only, reached through `use_task(prefer_threaded=False)` async wrappers + `asyncio.to_thread`.** The app correctly avoids the `*_async` API (no mixed loops), but today it uses `prefer_threaded=True` sync tasks, blocking render-time `.getInfo()` calls, and `add_ee_layer` from effects/observers — all flagged by the pysepal audit. The skill's verified pattern is: async task on Solara's kernel loop (`prefer_threaded=False`), ALL eeclient work inside `await asyncio.to_thread(sync_fn)`. Task 6 performs this refactor; Task 8 locks it with guard checks.
4. **pysepal target is stock `pysepal==3.8.0` from PyPI, and discovery must match the installed artifact.** Run the discovery script against a `v3.8.0` checkout (git worktree at that tag), not the checkout's HEAD, and confirm each import in the freshly built env. Fallback if stock 3.8.0 is missing something the app needs: pin the user's fork at an **immutable commit SHA** (`pysepal @ git+https://github.com/SerafiniJose/pysepal.git@<sha>`) and record in the YAML comment exactly which behavior required it.
5. **Recorded deviation — `pyproject.toml` stays dependency-free.** `defaults.md`/`validation-checklist.md` want pyproject to mirror the YAML's Python deps; this repo's pyproject deliberately declares `dependencies = []` with a comment naming `sepal_environment.yml` as authority (existing user decision). We keep it and record the deviation here; the final checklist (Task 12) marks this item "deviation, user-approved" rather than claiming full compliance. Flag to the user at handoff in case they'd rather mirror.
6. **`LogConsole` is diagnostics-only and stays.** The notification system owns user feedback (toasts + task pill); the log console remains a developer/power-user view of the logger stream, not a feedback channel. This is the enumerated boundary Task 7 enforces: after it, the only inline feedback left is `CreationDialog` field validation.

## Global Constraints

- `sepal_environment.yml` is the deploy authority (Decision 5 records the pyproject deviation).
- **No `-e .`** in the YAML; **every pip dep pinned exactly** (`==`, or a git URL at an immutable tag/SHA).
- Conda `python`, `gdal`, `numpy`, `tensorflow` pinned to the tested dev env: python 3.11, gdal 3.12, numpy 1.26, tensorflow 2.17.
- All user outputs go to the SEPAL user workspace under `module_results/deforestation_alerts` (via `SepalClient.results_path` once Phase 6 lands); container-local writes only for scratch and packaged assets. Data dir override `DEFORESTATION_ALERTS_DATA_DIR` must be an absolute path (validated).
- No path may resolve relative to the CWD; GDAL scratch configured **at import time in Python** (env var + `gdal.SetConfigOption`), fail-fast if nothing writable.
- All user feedback via the pysepal notification system; sole inline exception: `CreationDialog` field validation (Decision 6 covers LogConsole).
- GEE calls: sync API only, inside `asyncio.to_thread`, from `use_task(prefer_threaded=False)` coroutines; no GEE work in render bodies, `use_memo`, or sync effects/observers.
- Component existence confirmed against discovery output from a `v3.8.0` checkout + runtime import in the built env (Decision 4).
- Git commits: plain messages, no Co-Authored-By trailers (user preference).
- Repo root for all paths below: `~/Desktop/projects/deforestation_alerts/deforestation-alerts-module/`.

## Execution model per task

Dispatch each task's subagent with the model below (each task also carries its own `**Model:**` line). Rationale: mechanical/verification-heavy work → sonnet; refactors with concurrency semantics, API discovery, or broad call-site threading → opus. The between-task review stays with the coordinating session regardless.

| Task | Model | Why |
|---|---|---|
| 1 — env rewrite | sonnet | pin bookkeeping + scripted verification |
| 2 — CI/nox/docker align | sonnet | mechanical config edits |
| 3 — 3.8 rebuild + discovery | sonnet | scripted checks; escalate to opus only if upgrade fallout is non-trivial |
| 4 — GDAL/data-dir hardening | sonnet | code + tests are written in the plan |
| 4b — voila entry | sonnet | copy-and-adapt from the reference |
| 5 — provider mount | sonnet | two-line change + ordering test |
| 6 — task-lifecycle refactor | **opus** | concurrency semantics, cancellation safety, GEE loop rules |
| 7 — notifications migration | **opus** | depends on Task 6's lifecycle; judgment calls per call site |
| 8 — audit sweep | sonnet | greps + small fixes |
| 9 — jslink map sync | sonnet | helpers + tests are written in the plan |
| 10 — Workspace adapter | **opus** | live `SepalClient` API discovery shapes the interface |
| 11 — store migration | **opus** | broad call-site threading through recipe/alert stores |
| 12 — exports/settings migration | **opus** | same, plus the regression guard |
| 13 — verification walk | sonnet | checklist execution |

---

## Phase 1 — Dependency, CI, and packaging compliance

### Task 1: Audit direct deps, rewrite `sepal_environment.yml`

**Model:** sonnet

**Files:**
- Modify: `sepal_environment.yml`
- Test: throwaway env build + expanded import smoke (Step 4)

**Interfaces:**
- Produces: the deploy env every later task runs against (`pysepal==3.8.0`, notifications available).

Verified drift this task fixes: YAML says python 3.10 / openforis EE `v1.1.5rc0` / `matplotlib-scalebar==0.8.1` / `python-docx==1.1.2`; the working `defo-alerts` env runs python 3.11.10 / stock EE 1.7.29 / scalebar 0.9.0 / python-docx 1.2.0; `huggingface_hub` and `dateutils` are declared but **not installed** locally; `tensorflow-probability`/`tf-keras` are declared but have **no repository imports**. This is the "local env masks a broken deploy spec" failure class from `sepal-deployment.md`.

- [ ] **Step 1: Dependency audit — imports vs declarations**

```bash
cd ~/Desktop/projects/deforestation_alerts/deforestation-alerts-module
for m in dateutils tensorflow_probability tf_keras huggingface_hub cartopy geedim geemap docx matplotlib_scalebar MightyMosaic pygaul; do
  echo "== $m =="; grep -rn "import $m\|from $m" deforestation_alerts/ gui/ tests/ --include='*.py' | head -3
done
```
Known from planning: `huggingface_hub` is imported **lazily in `gui/tile/analysis/edit_tools.py:128`** (not `cnn_worker.py`); `tensorflow_probability`/`tf_keras`/`dateutils` are expected to have zero hits — drop them from the YAML unless a hit appears (if the CNN model file requires tf-keras at *load* time despite no import, the Step 4 smoke test will expose it; re-add with a comment naming the model). For every dep that stays, decide its exact pin.

- [ ] **Step 2: Choose and verify the `huggingface_hub` pin in a clean env**

Do not "pin whatever latest resolves". Pick the newest version, verify it in the throwaway env of Step 4 by exercising the real call path:
```bash
grep -n -A5 "huggingface" gui/tile/analysis/edit_tools.py | head -20   # see which API is used (e.g. hf_hub_download)
```
The Step 4 smoke test imports `gui.tile.analysis.edit_tools` so a broken pin fails there, and additionally calls the specific `huggingface_hub` function name used (import-level check only, no network).

- [ ] **Step 3: Write the new `sepal_environment.yml`**

```yaml
name: deforestation-alerts-module
channels:
  - conda-forge

dependencies:
  # Match the tested dev env (python 3.11.10, gdal 3.12.3, numpy 1.26.4,
  # tensorflow 2.17.0)
  - python=3.11
  - gdal=3.12
  - numpy=1.26
  - tensorflow=2.17
  # Geospatial binaries — conda handles these correctly
  - cartopy>=0.24.0
  - fiona>=1.10
  - geopandas>=1.0
  - rasterio>=1.3
  - shapely>=2.0
  - pyproj
  # Scientific
  - matplotlib>=3.9
  - pandas>=2.2
  - pyarrow
  # GUI / widgets
  - geemap>=0.35.0
  - ipyevents>=2.0
  - ipyleaflet>=0.19
  - ipython>=8.12
  - ipyvuetify<3
  - ipywidgets>=8.1
  - traitlets>=5.14
  # Serving (Decision 1: dual-target — voila for the classic SEPAL path,
  # solara-server for dev + the app-launcher container)
  - voila
  # Env plumbing
  - ipykernel
  - setuptools
  - wheel
  - pip
  - pip:
      # Earth Engine — SEPAL's custom build. v1.1.5rc0 conflicts with
      # pysepal 3.8's earthengine-api>1.6.0 requirement; v1.6.14 is the
      # build spatial_risk deploys with.
      - "earthengine-api @ git+https://github.com/openforis/earthengine-api.git@v1.6.14#subdirectory=python"
      - geedim==2.0.0
      - huggingface_hub==<PIN FROM STEP 2>   # CNN model download (gui/tile/analysis/edit_tools.py)
      - matplotlib-scalebar==0.9.0
      - MightyMosaic==1.2.3
      - python-docx==1.2.0
      - pygaul==0.4.2
      - pysepal==3.8.0
      - solara==1.57.4
      # NOTE: no `-e .` — the app runs in place (supervisord launches
      # `solara run gui/solara_app.py`; gui/solara_app.py shims sys.path,
      # pytest uses pyproject's pythonpath=["."]).
```
Notes: `tensorflow-probability`/`tf-keras`/`dateutils` deleted per Step 1 (re-add pinned iff imports were found). `defaults` channel removed. Do NOT touch `tests/conftest.py` — `pyproject.toml` already sets `[tool.pytest.ini_options] pythonpath = ["."]`, which covers test imports; adding a redundant conftest shim would mask packaging errors.

- [ ] **Step 4: Throwaway env build + expanded import smoke (this is how SEPAL/Docker consume the file)**

```bash
cd ~/Desktop/projects/deforestation_alerts/deforestation-alerts-module
micromamba env create -n defo-alerts-deploy-test -f sepal_environment.yml -y
micromamba run -n defo-alerts-deploy-test python - <<'EOF'
import importlib, sys
sys.path.insert(0, ".")
mods = [
    # geo/EE stack
    "osgeo.gdal_array", "ee", "geedim", "geemap", "rasterio", "fiona",
    # pysepal surface the app uses (incl. the 3.8-only notification system)
    "pysepal", "pysepal.mapping", "pysepal.solara", "pysepal.solara.notifications",
    "solara",
    # runtime deps exercised by the app
    "tensorflow", "MightyMosaic", "docx", "matplotlib_scalebar", "pygaul",
    "huggingface_hub",
    # first-party: the library package AND the GUI modules holding lazy imports
    "deforestation_alerts", "deforestation_alerts.scripts.vectorize",
    "deforestation_alerts.scripts.cnn_worker",
    "deforestation_alerts.scripts.report_builder",
    "gui.tile.analysis.edit_tools",
]
for m in mods:
    importlib.import_module(m)
    print("ok", m)
from huggingface_hub import hf_hub_download  # adjust to the API found in Step 2
print("ok hf api")
EOF
```
Expected: every line prints `ok`. `pysepal.solara.notifications` failing means the pysepal pin is wrong. Keep this env for Task 3's discovery cross-check; delete afterwards.

- [ ] **Step 5: Commit**

```bash
git add sepal_environment.yml
git commit -m "build: pin sepal_environment.yml to tested env, drop -e . and unused deps, bump pysepal to 3.8.0"
```

### Task 2: Align CI, nox, requirements.txt, and the Docker files with the new spec

**Model:** sonnet

**Files:**
- Modify: `.github/workflows/ci.yaml` (EE-version assert at ~line 21), `noxfile.py` (app session at ~line 17), `gui/Dockerfile` (stale note)
- Delete: `requirements.txt`
- Create: `docker-compose.yml`
- Test: CI dry-run reasoning + `nox -s app` boot check

**Interfaces:**
- Consumes: Task 1's YAML.
- Produces: one dependency authority; CI that passes against it.

Why: CI currently **asserts** `ee.__version__ == '1.1.5rc0'` and would certainly fail after Task 1; `noxfile.py` installs a stale `requirements.txt` (still `pysepal==3.6.1`, unpinned `huggingface_hub`) that `defaults.md` says should not exist; the Dockerfile builds from the YAML so it inherits Task 1 automatically but carries a stale "generate requirements.lock" note.

- [ ] **Step 1: Update the CI assert**

In `.github/workflows/ci.yaml`, change the "Verify ee-api fork" run line to:
```yaml
          python -c "import ee; v=ee.__version__; print('ee-api:', v); assert v == '1.6.14', 'Expected openforis fork 1.6.14, got ' + v"
```
(Verify the actual `ee.__version__` string the v1.6.14 build reports using the Task 1 Step 4 env — `micromamba run -n defo-alerts-deploy-test python -c "import ee; print(ee.__version__)"` — and assert exactly that string.)

- [ ] **Step 2: Delete `requirements.txt`; repoint nox**

```bash
git rm requirements.txt
```
In `noxfile.py`, replace the `app` session body so it stops installing from the deleted file — the env is the conda one, so the session becomes a thin runner:
```python
@nox.session(reuse_venv=True, venv_backend="none")
def app(session):
    """Run the Solara application in the active (conda) environment."""
    session.run("solara", "run", "gui/solara_app.py", "--port", "8900", "--no-open")
```
Check nothing else references the file: `grep -rn "requirements.txt" . --include='*.py' --include='*.yaml' --include='*.yml' --include='*.toml' --include='Dockerfile' --include='*.md' | grep -v docs/superpowers` — fix every hit (the `gui/Dockerfile` note from the header comment is expected; rewrite that note to say the YAML pins are the lock).

- [ ] **Step 3: Add `docker-compose.yml`** (file-matrix default for this family)

```yaml
services:
  app:
    build:
      context: .
      dockerfile: gui/Dockerfile
    ports:
      - "8900:8900"
    # SEPAL's app-launcher provides these in production; for local runs
    # mount your EE credentials and set the module data dir.
    volumes:
      - ~/.config/earthengine:/root/.config/earthengine:ro
    environment:
      - DEFORESTATION_ALERTS_DATA_DIR=/tmp/module_results/deforestation_alerts
```

- [ ] **Step 4: Verify**

```bash
micromamba run -n defo-alerts nox -s app   # boots the app, Ctrl+C after it serves
docker compose build 2>&1 | tail -5        # optional if docker available locally
```

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yaml noxfile.py docker-compose.yml gui/Dockerfile
git rm --cached requirements.txt 2>/dev/null; git commit -m "build: align CI/nox/docker with sepal_environment.yml as sole authority"
```

### Task 3: Rebuild the dev env on pysepal 3.8, discovery at the installed tag, suite green

**Model:** sonnet (escalate to opus if Step 4 surfaces non-trivial upgrade fallout)

**Files:**
- Modify: none expected; fix upgrade fallout only where tests fail.
- Test: full `pytest` suite + app smoke.

**Interfaces:**
- Produces: green baseline on 3.8.0 + a discovery listing matched to the installed artifact (`/tmp/pysepal-components-3.8.0.txt`) that later tasks cite.

- [ ] **Step 1: Recreate the dev env**

```bash
micromamba env remove -n defo-alerts -y
micromamba env create -n defo-alerts -f sepal_environment.yml -y
```

- [ ] **Step 2: Discovery against the installed version, not HEAD** (Decision 4)

```bash
cd ~/Desktop/projects/pysepal
git worktree add /tmp/pysepal-v380 v3.8.0
micromamba run -n defo-alerts python skills/pysepal-app/scripts/discover_pysepal_components.py \
  --repo-root /tmp/pysepal-v380 > /tmp/pysepal-components-3.8.0.txt
```
Then cross-check the runtime artifact for the components later phases import:
```bash
micromamba run -n defo-alerts python -c "
from pysepal.solara.notifications import NotificationProvider, use_notifications, notify, track_task
from pysepal.solara.components.task_button import TaskButtonComponent
print('ok 3.8.0 runtime surface')"
```
(If the `TaskButtonComponent` path differs in the discovery output, the discovery output wins — update the import here and wherever Task 6 uses it.) Remove the worktree when done: `git worktree remove /tmp/pysepal-v380`.

- [ ] **Step 3: Diff the app's pysepal surface against migration notes**

Read `/tmp/pysepal-v380`'s `docs/guides/migration-notes-v3.4.md` (before removing the worktree). Then:
```bash
cd ~/Desktop/projects/deforestation_alerts/deforestation-alerts-module
grep -rhn "from pysepal\|import pysepal" gui/ deforestation_alerts/ | sort -u
```
Every import must appear in the discovery output. Known-good from planning: `init_ee`, `SepalMap`, `MapApp`, `LocaleSelect`, `get_current_gee_interface`, `get_current_sepal_client`, `get_current_theme_state`, `setup_sessions`, `setup_solara_server`, `setup_theme_colors`, `with_sepal_sessions`.

- [ ] **Step 4: Suite + smoke**

```bash
micromamba run -n defo-alerts pytest -q
```
Expected: green (fix fallout per migration notes; only fall back to the fork pin per Decision 4 if stock 3.8.0 lacks something, pinning a SHA). Then boot via the `run-deforestation-alerts` skill or `micromamba run -n defo-alerts nox -s app`: app renders, About opens, no tracebacks.

- [ ] **Step 5: Commit (only if fixes were needed)**

```bash
git add -A && git commit -m "fix: pysepal 3.8 upgrade fallout"
```

---

## Phase 2 — Filesystem and GDAL hardening (one atomic task)

### Task 4: GDAL scratch dir + validated data-dir override + CWD-relative sweep

**Model:** sonnet

**Files:**
- Create: `deforestation_alerts/gdal_env.py` (adapted from `~/Desktop/projects/spatial_risk/spatial-risk-module/spatialrisk/gdal_env.py`)
- Modify: `deforestation_alerts/parameter/directory.py`, `gui/tile/analysis/export.py`
- Create: `tests/test_data_dir.py`, `tests/test_gdal_readonly_cwd.py`
- Test: both new test files + full suite

**Interfaces:**
- Produces: `configure_gdal_tmpdir() -> Path` (fail-fast: raises `RuntimeError` if no candidate is writable); `deforestation_alerts.parameter.directory.module_dir: Path` (same name every script already imports), env-overridable with absolute-path validation.

This merges the previously separate GDAL-copy and wiring tasks so no unwired module/test is committed alone (Codex finding 15), and diverges from the spatial_risk copy in two reviewed ways (finding 9): fail-fast instead of log-and-return-None, and full renaming of app-specific identifiers.

- [ ] **Step 1: Write the failing tests**

`tests/test_gdal_readonly_cwd.py` — self-contained (the spatial_risk test imports `spatialrisk.processing.*`, which has no equivalent here — do NOT copy it):
```python
"""GDAL allocates scratch files via CPLGenerateTempFilename, which falls back
to "." when CPL_TMPDIR/TMPDIR are unset. On SEPAL the CWD can be read-only, so
ComputeProximity with a non-Float32 destination dies with 'Read-only file
system' — only on the platform, never in dev. configure_gdal_tmpdir() must
make this class of operation survive a read-only CWD."""

import numpy as np
import pytest
from osgeo import gdal

gdal.UseExceptions()


def test_compute_proximity_survives_readonly_cwd(tmp_path, monkeypatch, request):
    # Importing the directory module wires configure_gdal_tmpdir()
    import deforestation_alerts.parameter.directory  # noqa: F401

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    request.addfinalizer(lambda: ro.chmod(0o755))
    monkeypatch.chdir(ro)

    drv = gdal.GetDriverByName("MEM")
    src = drv.Create("", 64, 64, 1, gdal.GDT_Byte)
    src.GetRasterBand(1).WriteArray(
        (np.arange(64 * 64).reshape(64, 64) % 7 == 0).astype("uint8")
    )
    # A non-Float32 destination forces GDAL to allocate a Float32 scratch band
    dst = drv.Create("", 64, 64, 1, gdal.GDT_UInt32)
    gdal.ComputeProximity(src.GetRasterBand(1), dst.GetRasterBand(1), ["VALUES=1"])
    assert dst.GetRasterBand(1).ReadAsArray().max() > 0


def test_configure_is_fail_fast(monkeypatch, tmp_path):
    from deforestation_alerts import gdal_env

    # Force every candidate to an unwritable location -> must raise, not log
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setenv("DEFORESTATION_ALERTS_GDAL_TMPDIR", str(ro / "nope"))
    monkeypatch.setattr(gdal_env, "_fallback_candidates", lambda: [], raising=False)
    with pytest.raises(RuntimeError):
        gdal_env.configure_gdal_tmpdir()
    ro.chmod(0o755)
```

`tests/test_data_dir.py`:
```python
"""Output-path rules: env-overridable data dir (absolute paths only),
GDAL tmpdir configured at import via BOTH channels, no CWD-relative paths."""

import importlib
import os
from pathlib import Path

import pytest
from osgeo import gdal


def _reload_directory():
    import deforestation_alerts.parameter.directory as directory

    return importlib.reload(directory)


def test_module_dir_env_override_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path / "custom"))
    directory = _reload_directory()
    assert directory.module_dir == tmp_path / "custom"
    assert directory.module_dir.is_dir()
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_env_override_tilde(monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", "~/defo_alt_results")
    directory = _reload_directory()
    assert directory.module_dir == Path.home() / "defo_alt_results"
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_env_override_rejects_relative(monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", "relative/dir")
    with pytest.raises(ValueError):
        _reload_directory()
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_default():
    directory = _reload_directory()
    assert directory.module_dir == Path.home() / "module_results" / "deforestation_alerts"


def test_gdal_tmpdir_configured_on_import_both_channels(monkeypatch):
    # Clear BOTH channels so a pre-existing value can't fake a pass
    monkeypatch.delenv("CPL_TMPDIR", raising=False)
    gdal.SetConfigOption("CPL_TMPDIR", None)
    _reload_directory()
    assert os.environ.get("CPL_TMPDIR"), "env channel not set"
    assert gdal.GetConfigOption("CPL_TMPDIR"), "gdal config channel not set"
    assert Path(os.environ["CPL_TMPDIR"]).is_dir()


def test_report_template_not_cwd_relative():
    from gui.tile.analysis import export

    p = Path(export._DEFAULT_REPORT_TEMPLATE)
    assert p.is_absolute(), "report template must not resolve against the CWD"
    assert p.exists()
```

Run both: `micromamba run -n defo-alerts pytest tests/test_data_dir.py tests/test_gdal_readonly_cwd.py -v` → expect FAIL/collection errors (modules don't exist yet).

- [ ] **Step 2: Create `deforestation_alerts/gdal_env.py`**

Start from the spatial_risk file, then apply exactly these changes:
1. `logger = logging.getLogger("spatial_risk")` → `logging.getLogger("SEPALUI.deforestation_alerts")`.
2. Rename **every** app-specific identifier: env var `SPATIAL_RISK_GDAL_TMPDIR` → `DEFORESTATION_ALERTS_GDAL_TMPDIR`, any `SPATIAL_RISK_DATA_DIR` reference → `DEFORESTATION_ALERTS_DATA_DIR`, fallback folder / scratch-prefix strings containing `spatial_risk` → `deforestation_alerts` (grep the copy for `spatial` and `SPATIAL` — zero hits when done).
3. **Fail-fast contract** (deliberate divergence from the copy): where the original logs and returns `None` when no candidate is writable, `raise RuntimeError("No writable GDAL scratch directory found; set DEFORESTATION_ALERTS_GDAL_TMPDIR")` instead. Return type becomes `Path` (not `Optional[Path]`). If the original lacks a single candidate-list function, factor the candidate collection into `_fallback_candidates()` so the fail-fast test can monkeypatch it.
4. Keep: the real write-probe (`_is_usable`), setting **both** `os.environ["CPL_TMPDIR"]` and `gdal.SetConfigOption("CPL_TMPDIR", ...)`, and the env-var override taking precedence.

- [ ] **Step 3: Rewrite `deforestation_alerts/parameter/directory.py`**

```python
"""All directories used in the application.

Outputs live under ``~/module_results/deforestation_alerts`` (the SEPAL-wide
convention), overridable via ``DEFORESTATION_ALERTS_DATA_DIR`` (absolute paths
only — a relative value would resolve against the CWD, which is read-only on
the platform). Importing this module also points GDAL's scratch-file allocator
at a writable directory (see gdal_env.py).
"""

import os
from pathlib import Path

from deforestation_alerts.gdal_env import configure_gdal_tmpdir

_override = os.environ.get("DEFORESTATION_ALERTS_DATA_DIR")
if _override:
    module_dir = Path(_override).expanduser()
    if not module_dir.is_absolute():
        raise ValueError(
            "DEFORESTATION_ALERTS_DATA_DIR must be an absolute path, got: "
            f"{_override!r}"
        )
else:
    module_dir = Path.home() / "module_results" / "deforestation_alerts"

module_dir.mkdir(parents=True, exist_ok=True)

configure_gdal_tmpdir()
```

- [ ] **Step 4: Fix the CWD-relative report template**

In `gui/tile/analysis/export.py` (~line 102), replace:
```python
_DEFAULT_REPORT_TEMPLATE = "data/report_template.docx"
```
with:
```python
# Resolve from the repo root (export.py -> analysis -> tile -> gui -> root):
# the CWD is not guaranteed writable or even stable on the platform.
_DEFAULT_REPORT_TEMPLATE = str(
    Path(__file__).resolve().parents[3] / "data" / "report_template.docx"
)
```
(add `from pathlib import Path` if missing).

- [ ] **Step 5: Sweep for remaining CWD-relative paths**

```bash
grep -rn "Path.cwd()\|os.getcwd()" deforestation_alerts/ gui/ --include='*.py'
grep -rnE "(open|to_file|to_csv|to_parquet|\bsave)\((\"|')[a-zA-Z]" deforestation_alerts/ gui/ --include='*.py' | grep -v tests
```
Expected: no hits. Fix any hit like Step 4 (resolve from `module_dir` or package-relative) and add a matching assertion to `test_data_dir.py`.

- [ ] **Step 6: Run tests, then the full suite**

```bash
micromamba run -n defo-alerts pytest tests/test_data_dir.py tests/test_gdal_readonly_cwd.py -v
micromamba run -n defo-alerts pytest -q
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add deforestation_alerts/gdal_env.py deforestation_alerts/parameter/directory.py \
        gui/tile/analysis/export.py tests/test_data_dir.py tests/test_gdal_readonly_cwd.py
git commit -m "feat: fail-fast GDAL scratch config, validated data-dir override, no CWD-relative paths"
```

### Task 4b: Restore the voila entry (`ui.ipynb`) + kernel registration

**Model:** sonnet

**Files:**
- Create: `ui.ipynb` (repo root — where SEPAL's classic module launcher looks)
- Create: `tests/test_ui_notebook.py`
- Test: nbformat/kernelspec test + bare-voila boot check

**Interfaces:**
- Consumes: `gui.solara_app.Page` (existing component; pysepal 3.8 sessions are runtime-aware, so no header hacks).
- Produces: the artifact `voila ui.ipynb` launches on classic SEPAL.

- [ ] **Step 1: Write the failing notebook test**

`tests/test_ui_notebook.py`:
```python
"""The voila entry must be a valid nbformat notebook whose kernelspec names
SEPAL's module venv. Voila does NOT fail loudly on a missing kernel — it
falls back to any registered kernel with only a WARNING (sepal-deployment.md),
so the name is load-bearing."""

from pathlib import Path

import nbformat

NB = Path(__file__).resolve().parents[1] / "ui.ipynb"


def test_notebook_is_valid_and_targets_module_kernel():
    nb = nbformat.read(NB, as_version=4)
    nbformat.validate(nb)
    assert nb.metadata["kernelspec"]["name"] == "venv-deforestation-alerts-module"


def test_notebook_stays_thin():
    nb = nbformat.read(NB, as_version=4)
    code = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert "from gui.solara_app import Page" in code
    # thin by design: shim + import + display, no business logic
    assert len(nb.cells) <= 3
```
Run → FAIL (no `ui.ipynb`).

- [ ] **Step 2: Create `ui.ipynb` from the approved reference**

Copy `~/Desktop/projects/spatial_risk/spatial-risk-module/ui.ipynb` and adapt — keep its exact cell structure (including nbformat-4.5 cell `id`s and its explicit display idiom, e.g. `element = Page(); display(element)`); change only:
1. the import to `from gui.solara_app import Page`,
2. the kernelspec to `name: "venv-deforestation-alerts-module"`, `display_name: "(venv) deforestation-alerts-module"`.
Do not improvise the notebook structure — the reference is the deployed, working shape.

- [ ] **Step 3: Register the kernel locally, inside the env so it travels with it**

```bash
ENVPREFIX=$(micromamba run -n defo-alerts python -c "import sys; print(sys.prefix)")
micromamba run -n defo-alerts python -m ipykernel install \
  --prefix="$ENVPREFIX" --name venv-deforestation-alerts-module \
  --display-name "(venv) deforestation-alerts-module"
```

- [ ] **Step 4: Boot under bare voila (no shell env exports — mirrors SEPAL)**

```bash
cd ~/Desktop/projects/deforestation_alerts/deforestation-alerts-module
micromamba run -n defo-alerts voila ui.ipynb --port=8911 --no-browser --show_tracebacks=True 2>&1 | tee /tmp/voila-boot.log
```
Open `http://localhost:8911` — the app renders (map + drawer). Then `grep -i "Could not find a kernel" /tmp/voila-boot.log` must print **nothing** (a hit means voila fell back to another env). If the app hangs at "Waiting for authentication headers...", the installed pysepal predates the voila-local-sessions work — that would invoke the fork fallback (Decision 4); do NOT add header-seeding hacks to the notebook.

- [ ] **Step 5: Tests + commit**

```bash
micromamba run -n defo-alerts pytest tests/test_ui_notebook.py -v
git add ui.ipynb tests/test_ui_notebook.py
git commit -m "feat: restore thin voila entry (ui.ipynb, kernelspec venv-deforestation-alerts-module)"
```

---

## Phase 3 — Task lifecycle, then notifications (order matters)

### Task 5: Mount `NotificationProvider` FIRST in the shell

**Model:** sonnet

**Files:**
- Modify: `gui/solara_app.py`
- Test: `tests/test_app_wiring.py` (extend)

**Interfaces:**
- Produces: a provider mounted before any tile constructs, so `use_notifications()` never memoizes a `NoopNotifier` on first render.

- [ ] **Step 1: Write the failing ordering test**

Append to `tests/test_app_wiring.py`, mirroring the file's existing source-inspection style and `Page`-access pattern (it already imports `gui.solara_app`, so reuse whatever fixture/guard it has around credentials — do not invent a new import path):
```python
def test_notification_provider_mounted_before_tiles():
    import inspect

    from gui import solara_app

    src = inspect.getsource(solara_app.Page.f)  # match the file's existing access idiom
    provider_at = src.index("NotificationProvider()")
    first_tile_at = min(src.index("_render_detached"), src.index("MapApp.element"))
    assert provider_at < first_tile_at, "provider must mount before tiles/MapApp"
```

- [ ] **Step 2: Run — expect FAIL** (`micromamba run -n defo-alerts pytest tests/test_app_wiring.py -v -k notification`)

- [ ] **Step 3: Mount the provider early**

In `gui/solara_app.py`: add `from pysepal.solara.notifications import NotificationProvider` to the pysepal imports, and inside `Page()` place the call **immediately after** `theme_state = get_current_theme_state()` (i.e. before `app_state`/model/tile construction and before `MapApp.element`):
```python
    # Centralized user feedback. Mounted BEFORE the tiles are constructed so
    # use_notifications() inside them binds the real kernel-scoped bus (a
    # later mount would hand first renders a silent NoopNotifier). MapApp
    # publishes --sepal-notification-right-offset so the pill tracks the
    # right panel.
    NotificationProvider()
```

- [ ] **Step 4: Test + live check** — `pytest tests/test_app_wiring.py -v` green; boot the app, no console errors.

- [ ] **Step 5: Commit**

```bash
git add gui/solara_app.py tests/test_app_wiring.py
git commit -m "feat: mount pysepal NotificationProvider before tile construction"
```

### Task 6: Task-lifecycle refactor for search/analyze (GEE pattern + safe cancellation)

**Model:** opus

**Files:**
- Modify: `gui/tile/aoi_tile.py` (use_task at ~266, render-time `.getInfo()` calls in `use_memo`, `add_ee_layer` at ~337), `gui/tile/alerts_filter_tile.py` (use_task at ~697, `add_ee_layer` at ~270), `gui/tile/overview_tile.py` (`add_ee_layer` at ~180 from effect/observer)
- Test: existing suite + wiring assertions (Step 5)

**Interfaces:**
- Consumes: Decision 3's transport rule; `TaskButtonComponent` import verified in Task 3 Step 2.
- Produces: per-tile async tasks with immutable request/outcome objects; later Task 7 attaches notifications to THIS lifecycle.

The refactor pattern, applied to each of the two long-running actions (AOI **search**, Filter **analyze**):

```python
# 1) Sync worker: pure — takes an immutable request snapshot, returns an
#    outcome object; NO model/reactive/widget mutation, NO navigation.
def _run_search_sync(request: SearchRequest) -> SearchOutcome:
    ...  # all eeclient calls are the blocking API — this runs in a thread

# 2) Task: async on Solara's kernel loop; ALL blocking work via to_thread,
#    including the map layer add (the skill's verified pattern).
async def _search(request: SearchRequest) -> SearchOutcome:
    outcome = await asyncio.to_thread(_run_search_sync, request)
    await asyncio.to_thread(
        map_widget.add_ee_layer, outcome.feature_collection, {}, "aoi"
    )
    return outcome

search_task = solara.lab.use_task(
    _search, dependencies=None, prefer_threaded=False, raise_error=False
)

# 3) Click handler: synchronous pending guard + snapshot at click time.
def _on_search_click(*_):
    if search_task.pending:          # render-time disabled= cannot prevent
        return                       # the double-click; this guard can
    search_task(SearchRequest(aoi=model.aoi_code, start=model.start, end=model.end))

# 4) Apply results ONLY from a completion effect with the full dependency
#    list; a cancelled task must change nothing.
def _apply_search():
    if search_task.finished and not search_task.cancelled and search_task.value is not None:
        outcome = search_task.value
        ...  # model/reactive mutation + app_state.current_page_view navigation live HERE
solara.use_effect(
    _apply_search,
    [search_task.pending, search_task.finished, search_task.error, search_task.cancelled],
)
```

- [ ] **Step 1: AOI tile** — define `SearchRequest`/`SearchOutcome` as `@dataclasses.dataclass(frozen=True)` in the tile module; move every model/reactive write and the `current_page_view` navigation out of the worker into the completion effect; convert the task per the pattern (`prefer_threaded=False`, async, `to_thread`); move `map_widget.add_ee_layer(model.feature_collection, ...)` (line ~337) into the task coroutine via `to_thread`.

- [ ] **Step 2: AOI render-time GEE** — locate the blocking `.getInfo()` calls executed inside `use_memo`/render (three were flagged in review; `grep -n "getInfo\|get_info" gui/tile/aoi_tile.py`). Move them into a `use_task(prefer_threaded=False)` initialization task (same pattern, fired from a `use_effect` on mount) whose completion effect stores the results in `use_state`/model fields the render reads.

- [ ] **Step 3: Filter tile** — same conversion for `_run_analyze` (~697): frozen request snapshot, async task + `to_thread`, `add_ee_layer` (~270) inside the task, completion effect owns all mutations/navigation.

- [ ] **Step 4: Overview tile** — the `add_ee_layer` at ~180 runs from an effect/observer; wrap that call site in the same task pattern (a tiny `use_task` that does `await asyncio.to_thread(map_.add_ee_layer, ...)`), triggered by the effect instead of calling the widget directly.

- [ ] **Step 5: Buttons + wiring assertions** — replace the search/analyze `solara.Button`s with `TaskButtonComponent` wired to the task (canonical pattern: read `/tmp/pysepal-v380`'s `docs/guides/solara-gee-patterns.md` § "Async Button Convention" — from the worktree while it exists, else the pysepal checkout at the tag). Keep the handler-side pending guard. Add source assertions to `tests/test_app_wiring.py`:
```python
def test_gee_tasks_use_kernel_loop():
    import inspect

    import gui.tile.aoi_tile as aoi
    import gui.tile.alerts_filter_tile as flt

    for mod in (aoi, flt):
        src = inspect.getsource(mod)
        assert "prefer_threaded=True" not in src
        assert "prefer_threaded=False" in src
```

- [ ] **Step 6: Suite + live check** — `pytest -q` green; boot the app: search and analyze still work end-to-end; double-clicking the buttons neither cancels mid-flight work nor double-fires; cancelling (via TaskButton) leaves models/pages unchanged.

- [ ] **Step 7: Commit**

```bash
git add gui/tile/ tests/test_app_wiring.py
git commit -m "refactor: async use_task lifecycle for GEE work (kernel loop, snapshots, safe cancel)"
```

### Task 7: Migrate inline feedback to notifications (against the final lifecycle)

**Model:** opus

**Files:**
- Modify: `gui/tile/aoi_tile.py`, `gui/tile/alerts_filter_tile.py` (Solara components → `use_notifications()`), `gui/tile/recipe_tile.py`, `gui/tile/analysis/edit_tools.py`, `gui/tile/analysis/export.py` (raw layouts/workers → `notify()`/`track_task()`)
- Test: worker-level recording tests (Step 3) + suite

**Interfaces:**
- Consumes: Task 5's provider, Task 6's completion effects.
- Produces: the app's single feedback boundary — after this task the ONLY inline feedback is `CreationDialog` field validation; `LogConsole` is diagnostics-only (Decision 6).

API boundary (per Codex finding 14): **Solara components use the hook** (`notifications = use_notifications()`, `notifications.error/success/track`); **raw-widget layouts and worker-only modules use the globals** (`notify()`, `track_task()`). Do not use globals inside Solara components.

- [ ] **Step 1: Solara tiles** — in `aoi_tile` and `alerts_filter_tile`: call `use_notifications()` at the top of the component; in each Task-6 completion effect publish `notifications.error(str(task.exception))` on error (never `str(task.error)` — it's a bool) and a success toast on completion; wrap the analyze task body in `notifications.track("Analyzing alerts", total_steps=N)` mirroring its existing logged phases (`task.step(...)`); on failure call the tracker's `fail(str(exc))` **before** the error toast (a failed task leaves the pill; the explicit toast keeps it visible). Delete the inline `solara.Error` blocks (~311 aoi, ~725 filter) and any inline `solara.Info` *progress* elements (static form guidance stays).

- [ ] **Step 2: Raw layouts** — `recipe_tile` (sw.Alerts ~143/145), `analysis/edit_tools` (~174), `analysis/export` (~305): replace terminal outcomes with `notify("...", type_="success"/"error")` at the end of the worker functions and long builds with `track_task(...)`; delete each `sw.Alert` once nothing writes to it. No exceptions kept without a written justification comment at the site (only field validation qualifies).

- [ ] **Step 3: Worker-level recording tests** — the hook silently no-ops without a provider, so assert at the module boundary. One per migrated raw module, shaped like:
```python
def test_recipe_save_publishes_outcome(monkeypatch, tmp_path):
    import gui.tile.recipe_tile as recipe_tile

    published = []
    monkeypatch.setattr(
        recipe_tile, "notify",
        lambda msg, type_="info", **kw: published.append((type_, msg)),
    )
    # drive the save worker with a minimal model pointed at tmp_path,
    # reusing the fixtures from tests/test_recipe_io.py
    ...
    assert published and published[-1][0] == "success"
```
For the Solara tiles, spy on the notifier object the same way (monkeypatch `use_notifications` in the module to return a recording stub) if a headless render harness exists in the suite; otherwise cover via the Step 4 live check and leave a comment naming that decision.

- [ ] **Step 4: Suite + live check** — `pytest -q` green; live: recipe save shows a success toast; analyze shows the task pill stepping then a completion toast; a forced failure shows an error toast.

- [ ] **Step 5: Commit**

```bash
git add gui/tile/ tests/
git commit -m "feat: route user feedback through pysepal notifications"
```

---

## Phase 4 — Conformance audit sweep

### Task 8: Audit checklist sweep + AOI methods + transport guard

**Model:** sonnet

**Files:**
- Modify: `gui/tile/aoi_tile.py:357` (methods list)
- Create: `tests/test_gee_transport.py`

- [ ] **Step 1: Transport guard test**

```python
"""One event loop for all eeclient traffic: the app uses the blocking GEE API
inside asyncio.to_thread exclusively (Decision 3). A stray *_async call would
bind eeclient to a second loop and crash far from the call site."""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_no_async_gee_transport_mixed_in():
    result = subprocess.run(
        ["grep", "-rn", "_async(", str(REPO / "gui"),
         str(REPO / "deforestation_alerts"), "--include=*.py"],
        capture_output=True, text=True,
    )
    assert result.stdout == "", (
        "Async GEE calls found — the app is sync-via-thread only:\n" + result.stdout
    )
```
Run → PASS (locks the invariant Task 6 established).

- [ ] **Step 2: AOI methods — exclude SHAPE** (container family, Decision 1): change line ~357 to
```python
        # Dual-target app (voila sandbox + app-launcher container): SHAPE
        # reads server-local files, which in the container are NOT the user's
        # SEPAL workspace (pysepal skill, AOI Method Restrictions) — excluded
        # as the lowest common denominator. DRAW/ADMIN/ASSET cover the cases.
        methods=["ADMIN1", "ADMIN2", "DRAW", "ASSET"],
```

- [ ] **Step 3: Remaining checklist greps** — expected no-hits; fix any per the skill's stated remedy:
```bash
grep -rn "AssetSelector\|PointsSelector\|ShapeSelector\|asset_method" gui/ deforestation_alerts/ --include='*.py'
grep -rn "rv\.Btn(on_click" gui/ --include='*.py'
grep -rn "use_thread" gui/ --include='*.py'
grep -rn "str(task.error)" gui/ --include='*.py'
grep -rn "theme_toggle=" gui/ --include='*.py'
grep -rn "^import ipyvuetify as rv" gui/ --include='*.py'
grep -rnE "if task\.value:" gui/ --include='*.py'
grep -rn "getInfo()\|\.get_info(" gui/ --include='*.py'   # any hit must be inside a to_thread-wrapped worker (Task 6)
```
(`import ipyvuetify as v` in raw-layout modules is fine — the `rv` rule governs `@solara.component` bodies.) Manually confirm AOI/Filter/Overview no longer touch GEE or `add_ee_layer` from render bodies, `use_memo`, sync effects, or trait observers (the Task 6 deliverable).

- [ ] **Step 4: Suite + commit**

```bash
micromamba run -n defo-alerts pytest -q
git add -A && git commit -m "fix: pysepal audit conformance (transport guard, AOI methods)"
```

---

## Phase 5 — Browser-side two-map sync (Analysis workspace, user request)

### Task 9: jslink + synced-mutation helpers

**Model:** sonnet

**Files:**
- Modify: `gui/tile/analysis/widgets.py` (links at 154–155 and 206; new helper methods), `gui/tile/analysis/analysis_tile.py` (~167–169), `gui/tile/analysis/edit_tools.py` (~331–357, ~381, clear/hide paths ~343–356, 415–416, 448–449)
- Create: `tests/test_two_map_sync.py`

**Interfaces:**
- Produces: `AnalysisWidgets.set_synced_view(center, zoom)` and `AnalysisWidgets.set_synced_draw_data(features)` — the ONLY sanctioned ways for Python to move the maps or set draw data.

Why (user request): `traitlets.link` syncs in the **kernel** — every pan/zoom/draw event round-trips browser→python→browser before the second map follows. `ipywidgets.jslink` connects the two widget models **in the frontend**: the mirror is client-side and instantaneous; the kernel still receives the normal trait sync afterwards, so Python observers keep working. The cost: jslink propagates **nothing headless**, so every Python-originated mutation must explicitly write both widgets — hence the helpers, and behavioral tests instead of source-string counting.

- [ ] **Step 1: Behavioral tests first**

`tests/test_two_map_sync.py`:
```python
"""Two-map sync: browser-side jslink for user interactions; explicit dual
writes (helpers) for Python-originated mutations, because jslink propagates
only in a live frontend — never headless."""

import inspect
from types import SimpleNamespace


def _stub_widgets():
    """AnalysisWidgets stand-in exposing only what the helpers touch."""
    from gui.tile.analysis.widgets import AnalysisWidgets

    stub = SimpleNamespace(
        map_before=SimpleNamespace(center=None, zoom=None),
        map_after=SimpleNamespace(center=None, zoom=None),
        draw_before=SimpleNamespace(data=[]),
        draw_after=SimpleNamespace(data=[]),
    )
    stub.set_synced_view = AnalysisWidgets.set_synced_view.__get__(stub)
    stub.set_synced_draw_data = AnalysisWidgets.set_synced_draw_data.__get__(stub)
    return stub


def test_set_synced_view_writes_both_maps():
    w = _stub_widgets()
    w.set_synced_view((12.5, -60.25), 16)
    assert w.map_before.center == w.map_after.center == (12.5, -60.25)
    assert w.map_before.zoom == w.map_after.zoom == 16


def test_set_synced_draw_data_writes_both_controls():
    w = _stub_widgets()
    features = [{"type": "Feature", "geometry": None, "properties": {"id": 1}}]
    w.set_synced_draw_data(features)
    assert w.draw_before.data == features
    assert w.draw_after.data == features
    assert w.draw_before.data is not w.draw_after.data  # no shared mutable list


def test_wiring_uses_jslink_not_kernel_link():
    from gui.tile.analysis import widgets

    src = inspect.getsource(widgets.AnalysisWidgets.__init__)
    assert 'jslink((self.map_before, "center")' in src
    assert 'jslink((self.map_before, "zoom")' in src
    assert 'jslink((self.draw_before, "data")' in src
    for line in src.splitlines():
        assert not line.strip().startswith("link(("), f"kernel link remains: {line.strip()}"


def test_no_single_sided_python_mutations():
    """Python code must go through the helpers; direct one-sided writes to a
    synced trait diverge headlessly under jslink."""
    from gui.tile.analysis import analysis_tile, edit_tools

    for mod in (analysis_tile, edit_tools):
        src = inspect.getsource(mod)
        for needle in ("map_before.center =", "map_before.zoom =",
                       "map_after.center =", "map_after.zoom =",
                       "draw_before.data =", "draw_after.data ="):
            assert needle not in src, (
                f"{mod.__name__} writes a synced trait directly ({needle}); "
                "use set_synced_view/set_synced_draw_data"
            )
```
Run → FAIL (helpers/wiring don't exist yet).

- [ ] **Step 2: `widgets.py` — swap links, add helpers**

Add `from ipywidgets import jslink` to imports. Replace lines 154–155 and 206:
```python
        # Browser-side two-way links: user pans/zooms/draws mirror to the
        # other map client-side, with no kernel round-trip (the old
        # traitlets.link lagged a full browser->python->browser cycle per
        # event). jslink propagates NOTHING headless: Python-originated
        # mutations must use set_synced_view/set_synced_draw_data below.
        jslink((self.map_before, "center"), (self.map_after, "center"))
        jslink((self.map_before, "zoom"), (self.map_after, "zoom"))
```
```python
        jslink((self.draw_before, "data"), (self.draw_after, "data"))
```
Add the helpers as `AnalysisWidgets` methods:
```python
    def set_synced_view(self, center, zoom):
        """Python-originated view change: write BOTH maps explicitly (the
        jslink mirror exists only in a live frontend)."""
        self.map_before.center = center
        self.map_after.center = center
        self.map_before.zoom = zoom
        self.map_after.zoom = zoom

    def set_synced_draw_data(self, features):
        """Python-originated draw change: write BOTH controls, with distinct
        list objects so later in-place edits can't alias."""
        self.draw_before.data = list(features)
        self.draw_after.data = list(features)
```
Update the class docstring (lines ~27–31) to describe the jslink + helper contract.

- [ ] **Step 3: Migrate every Python-originated mutation to the helpers**

- `analysis_tile.py:167-169` (`widgets.map_before.center = ...; widgets.map_before.zoom = 16`) → `widgets.set_synced_view((alerta.point.y, alerta.point.x), 16)`.
- `edit_tools.py` ~341–345 and ~354–357 (clear-then-set relying on the old kernel link) → `widgets.draw_before.clear(); widgets.draw_after.clear(); widgets.set_synced_draw_data(...)`.
- `edit_tools.py:381` (seeds ONLY `draw_after.data` from the prediction raster, while the save path at ~400 reads `draw_before` — this diverges headlessly under jslink) → `widgets.set_synced_draw_data(convertir_formato3(...))`.
- Audit the rest of both modules for any remaining direct write to the six synced traits (`grep -n "map_before\.\|map_after\.\|draw_before\.data\|draw_after\.data" gui/tile/analysis/*.py`); `clear()`/`hide()`/`show()` pairs already call both controls — leave them. Update the stale comment at `edit_tools.py:~331` ("which is .data-linked").

- [ ] **Step 4: Tests + suite**

```bash
micromamba run -n defo-alerts pytest tests/test_two_map_sync.py -v && micromamba run -n defo-alerts pytest -q
```

- [ ] **Step 5: Live verification (jslink cannot be proven headless)** — boot the app, Analysis step: pan/zoom left map → right follows with no lag (and keeps following while a worker blocks the kernel); draw on one map → mirrors instantly; accept/filter/clear edit buttons still update both maps; the navigator's per-alert jump recenters BOTH maps.

- [ ] **Step 6: Commit**

```bash
git add gui/tile/analysis/ tests/test_two_map_sync.py
git commit -m "perf: browser-side two-map sync (jslink) with explicit dual-write helpers"
```

**Optional follow-up (only if per-event mirroring is still not smooth):** ipyleaflet syncs `center` on throttled move events, so jslink mirrors per-event, not per-frame. True per-frame following (leaflet.sync semantics) needs a small `v.VuetifyTemplate` JS shim that locates the two rendered leaflet instances and forwards `move` events directly (`follower.setView(leader.getCenter(), leader.getZoom(), {animate: false})` with a re-entrancy guard). Treat as its own mini-task with the same live verification; it reaches into ipyleaflet DOM internals, so pin-check `ipyleaflet` before shipping.

---

## Phase 6 — Container-family user-file access (SepalClient)

The container's filesystem is NOT the user's SEPAL workspace (`pysepal-app` SKILL.md, User File Rules): runtime user-file reads/writes/listing/mkdir must go through the session `SepalClient` — `list_files`, `get_file`, `set_file`, `get_remote_dir`, `results_path` — with **no** `DEPLOY_ENV`/local-mode branching. Container-local paths remain legitimate only for packaged read-only assets (e.g. `data/report_template.docx`) and transient scratch that is subsequently uploaded.

**Gate note (Decision 1):** this phase is what makes the **container/app-launcher target** correct; under voila-in-sandbox the same `SepalClient` path reaches the same workspace, so it serves both targets with one code path (the skill's no-branching rule). If the team later drops the container target entirely and deploys voila-only (spatial_risk-style Path IO), this phase may be deferred — record that as a new decision before skipping it; do not skip silently.

### Task 10: Workspace adapter + inventory

**Model:** opus

**Files:**
- Create: `deforestation_alerts/scripts/workspace.py`
- Create: `tests/test_workspace.py`
- Create (inventory output): `docs/superpowers/plans/2026-08-31-user-file-inventory.md`

**Interfaces:**
- Produces: `Workspace` — the single boundary all user-file IO flows through in Tasks 11–12:
  - `Workspace(sepal_client)` (constructed in `Page()` from `get_current_sepal_client()` and passed down like `gee_interface` — never fetched at module level)
  - `.results_dir(*parts) -> str` — remote path under `sepal_client.results_path` (module_results/deforestation_alerts), created on demand via `get_remote_dir`
  - `.read_bytes(remote_path) -> bytes` / `.write_bytes(remote_path, data)` — `get_file`/`set_file`
  - `.list(remote_path) -> list[str]`
  - `.scratch_file(suffix="") -> Path` — fresh container-local scratch path (under the GDAL scratch parent from Task 4)
  - `.download_to_scratch(remote_path) -> Path` / `.upload_from_scratch(local_path, remote_path)` — for libraries that only speak local files (rasterio, pyarrow, docx): stage in scratch, then transfer

- [ ] **Step 1: Confirm the `SepalClient` surface in the built env** (names above come from the skill; verify signatures):
```bash
micromamba run -n defo-alerts python -c "
from pysepal.solara import get_current_sepal_client
import inspect, pysepal
from pysepal import sepal_client as sc_mod  # adjust to the real module per discovery output
print([n for n in dir(sc_mod) if not n.startswith('_')])"
```
Adjust the adapter method bodies to the real API (the discovery output from Task 3 and `pysepal`'s source are the truth; do not guess).

- [ ] **Step 2: Write `tests/test_workspace.py`** against a fake client:
```python
class FakeSepalClient:
    def __init__(self):
        self.files = {}
        self.dirs = set()
        self.results_path = "module_results/deforestation_alerts"

    def set_file(self, path, data):  # match real signature from Step 1
        self.files[path] = data

    def get_file(self, path):
        return self.files[path]

    def list_files(self, path):
        return [k for k in self.files if k.startswith(path)]

    def get_remote_dir(self, path):
        self.dirs.add(path)
        return path


def test_results_dir_roots_under_results_path():
    from deforestation_alerts.scripts.workspace import Workspace

    ws = Workspace(FakeSepalClient())
    p = ws.results_dir("recipes", "r1")
    assert p.startswith("module_results/deforestation_alerts")


def test_write_read_roundtrip():
    from deforestation_alerts.scripts.workspace import Workspace

    ws = Workspace(FakeSepalClient())
    ws.write_bytes(ws.results_dir("recipes") + "/x.json", b"{}")
    assert ws.read_bytes(ws.results_dir("recipes") + "/x.json") == b"{}"
```
Then implement `workspace.py` to make it pass (thin delegation; scratch staging via `tempfile.mkdtemp(dir=<gdal scratch parent>)`).

- [ ] **Step 3: Build the user-file inventory** the next two tasks execute against:
```bash
grep -rn "module_dir\|Path.home()\|\.mkdir(\|open(\|write_text\|read_text\|to_parquet\|read_parquet\|to_file\|read_file\|to_csv\|\.save(\|savefig" \
  deforestation_alerts/ gui/ --include='*.py' | grep -v tests | sort > /tmp/userfile-hits.txt
```
Classify every hit into: **(a) user data** (recipes, alert stores, exports, reports — migrate), **(b) packaged asset read** (template docx, message JSONs — keep local), **(c) scratch** (intermediate rasters consumed then discarded/uploaded — keep local via the adapter's scratch staging). Write the classified table to `docs/superpowers/plans/2026-08-31-user-file-inventory.md`; it is the checklist for Tasks 11–12. Known members of (a) from planning: `recipe_helper.py` (recipe folders, parquet store), `alert_repository.py`, `alert_export.py` (CSV/GPKG), `gui/tile/analysis/export.py` (parquet, GPKG, docx report), `report_builder.py` (`doc.save`), `aux_model.py` / `app_model.py` persisted settings.

- [ ] **Step 4: Commit**

```bash
git add deforestation_alerts/scripts/workspace.py tests/test_workspace.py docs/superpowers/plans/2026-08-31-user-file-inventory.md
git commit -m "feat: SepalClient workspace adapter + user-file inventory"
```

### Task 11: Migrate the recipe/alert store to the adapter

**Model:** opus

**Files:**
- Modify: `deforestation_alerts/scripts/recipe_helper.py`, `deforestation_alerts/scripts/alert_repository.py`, their call sites in `gui/tile/recipe_tile.py` / `gui/tile/analysis/export.py` (threading the `Workspace` through — same dependency-injection style the tiles already use for `gee_interface`)
- Test: existing `tests/test_recipe_io.py`, `test_recipe_load_store.py`, `test_parquet_store.py`, `test_save_alerts_store.py` reworked to run against `Workspace(FakeSepalClient())`

Worked example of the transformation (apply the same shape to every function in the two modules):
```python
# BEFORE (container-local; wrong workspace in the container)
def save_gdf_to_parquet(gdf, path):
    gdf.to_parquet(path)

# AFTER (adapter boundary; pyarrow still writes a local file — to scratch —
# and the adapter ships it to the user's workspace)
def save_gdf_to_parquet(gdf, workspace, remote_path):
    local = workspace.scratch_file(suffix=".parquet")
    gdf.to_parquet(local)
    workspace.upload_from_scratch(local, remote_path)
```
Rules: functions take `workspace` as a parameter (no globals); remote paths are built with `workspace.results_dir(...)`; readers use `download_to_scratch` before handing paths to geopandas/pyarrow. The recipe manifest listing (`load` pickers) moves to `workspace.list(...)`.

- [ ] **Step 1:** Rework the four store test files to construct `Workspace(FakeSepalClient())` (import the fake from `tests/test_workspace.py` — move it to `tests/conftest.py` as a fixture) and assert the same round-trip semantics they assert today. Run → FAIL.
- [ ] **Step 2:** Migrate `recipe_helper.py`, then `alert_repository.py`, threading `workspace` parameters through; update `gui` call sites (`recipe_tile`, `analysis/export`) to pass the `Workspace` built in `Page()`.
- [ ] **Step 3:** `pytest -q` green. Live check: create + save + reload a recipe in the running app (dev `SepalClient` reaches the local sandbox, so behavior is unchanged in dev).
- [ ] **Step 4:** Commit: `git commit -m "refactor: recipe/alert stores go through the SepalClient workspace adapter"`.

### Task 12: Migrate exports, reports, and persisted settings; guard against regression

**Model:** opus

**Files:**
- Modify: `deforestation_alerts/scripts/alert_export.py`, `deforestation_alerts/scripts/report_builder.py`, `gui/tile/analysis/export.py`, `deforestation_alerts/model/aux_model.py`, `deforestation_alerts/model/app_model.py` (per the Task 10 inventory — the inventory file is authoritative if it found more)
- Create: `tests/test_no_local_userdata_writes.py`

- [ ] **Step 1:** Apply the Task 11 transformation shape to every remaining category-(a) inventory row (exports: CSV/GPKG via scratch+upload; report: `doc.save(scratch)` then upload; model settings: `read_bytes`/`write_bytes` of their JSON).
- [ ] **Step 2:** Regression guard:
```python
"""User data must flow through the Workspace adapter (SepalClient), never
directly to the container filesystem (pysepal-app User File Rules)."""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# category-(b)/(c) files from the inventory are exempt by design
EXEMPT = {"deforestation_alerts/gdal_env.py", "deforestation_alerts/parameter/directory.py",
          "deforestation_alerts/scripts/workspace.py"}


def test_no_direct_userdata_filesystem_calls():
    result = subprocess.run(
        ["grep", "-rln", r"to_parquet(\|to_file(\|to_csv(\|\.save(\|write_text(",
         str(REPO / "deforestation_alerts"), str(REPO / "gui"), "--include=*.py"],
        capture_output=True, text=True,
    )
    offenders = [
        p for p in result.stdout.splitlines()
        if Path(p).relative_to(REPO).as_posix() not in EXEMPT
        and "/tests/" not in p
        and "workspace.upload_from_scratch" not in Path(p).read_text()
    ]
    assert offenders == [], f"direct user-data writes outside the adapter: {offenders}"
```
(Refine `EXEMPT` from the final inventory — every exemption must cite its inventory category in a comment.)
- [ ] **Step 3:** `pytest -q` green; live check: run an alert export and a report build end-to-end.
- [ ] **Step 4:** Commit: `git commit -m "refactor: exports/reports/settings through the workspace adapter + regression guard"`.

---

## Phase 7 — Final verification

### Task 13: Walk the adapted deploy checklist + fix the changelog

**Model:** sonnet

**Files:** `CHANGELOG.md`; otherwise verification only (fixes loop back into the owning task).

- [ ] Throwaway env build from `sepal_environment.yml` + the Task 1 Step 4 smoke test against the final tree
- [ ] No `-e .`; every pip dep pinned; git deps at immutable tag/SHA; no `requirements.txt` in the tree
- [ ] `docker compose build` succeeds and the container serves the app on :8900 (or record "docker unavailable locally, verified via CI/deploy")
- [ ] Voila target: `ui.ipynb` kernelspec = `venv-deforestation-alerts-module`; local kernel registered; app boots under bare `voila ui.ipynb` (rerun Task 4b Step 4); after the real SEPAL deploy, grep the platform log for `Could not find a kernel`
- [ ] GDAL scratch + data-dir tests green (`pytest tests/test_gdal_readonly_cwd.py tests/test_data_dir.py`)
- [ ] External binaries: `grep -rn "shutil.which" deforestation_alerts/ gui/` — any hit gets the sys.executable-sibling fallback from `sepal-deployment.md` § "Binaries and PATH"
- [ ] Tile layers through the SEPAL proxy: the app serves no local tile server (EE tiles only) → record "N/A" unless Phase 6 changed that
- [ ] Full suite green: `micromamba run -n defo-alerts pytest -q`
- [ ] Validation-checklist walk (`references/validation-checklist.md`): every line pass/N-A/**deviation** — the pyproject-mirror line is recorded as "deviation, user-approved (Decision 5)"; nothing is reported as fully compliant that isn't
- [ ] **Amend** the CHANGELOG Unreleased entry (do not just append): it currently documents the Solara migration; extend that same entry with the requirements update — pysepal 3.8, pinned deploy spec (no `-e .`), fail-fast GDAL scratch config, centralized notifications, SepalClient workspace adapter, browser-side Analysis map sync. **Correct** the "Removed the Voila entry" sentence: a thin voila wrapper (`ui.ipynb`) was restored around the Solara `Page` for the classic SEPAL launch path (Task 4b), so the entry must say removed-then-restored, not removed.
- [ ] Commit: `git commit -m "chore: SEPAL app requirements update — verification walk + changelog"`

---

## Deferred / out of scope (recorded so nobody "helpfully" adds them)

- **ExportLauncher**: the app's exports are file products (CSV/GPKG/parquet/docx) plus geedim *downloads* — none are `ee.batch` exports of GEE objects, so `ExportLauncher` (EE asset / Drive / SEPAL-workspace destinations for `ee.Image`/`ee.FeatureCollection`) does not currently apply. If a "send filtered alerts to EE asset/Drive" feature is requested, follow the skill's Export Rules (`ExportSource` + lazy `resolve`, canonical `"GEO_TIFF"` format strings, `ee-client>=2.5.2` — pysepal 3.8 already requires ≥3.0.0).
- **Rewriting raw-ipyvuetify tiles to pure Solara**: prohibited by existing-repo rules; the layouts encode MapApp dialog-survival fixes.
- **ipecharts**: no in-app charts today (matplotlib appears only inside generated docx reports). Any future in-app chart must use ipecharts per the pysepal skill.
- **pyproject dependency mirror**: recorded deviation (Decision 5) — revisit only on user request.
