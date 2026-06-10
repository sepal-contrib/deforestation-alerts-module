# Phase 4 — Task 11: Finalize the Solara migration & remove Voila (updated plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Solara the one and only entrypoint — delete the dead Voila notebook/runtime/CI/config, drop the leftover legacy `analysis_tile.py` god-class, point the run-skill + docs at Solara, and confirm the Phase-4 Definition of Done holds.

**Architecture:** This is the final Task 11 of [the Phase-4 plan](2026-06-06-phase4-solara-migration.md) (Part C). It is almost entirely *removal* + config rewiring, with one parity-verification gate up front and one DoD-reconciliation pass at the end. No new app behavior is added.

**Tech Stack:** Solara (entry `gui/solara_app.py`, served via `gui/run_solara.sh` / `gui/supervisord.conf` / `gui/Dockerfile`), pysepal `MapApp`, nox, GitHub Actions, pytest.

---

## Reality check — how the codebase differs from the original Task 11

The original Task 11 (lines 930–1002 of the phase-4 plan) assumed Voila was still a *live fallback* that had to be verified for parity before removal. **That is no longer true.** Verified state on `2026-06-10` (last commit `7bd29b4`):

1. **Voila is already non-functional.** The tile modules were rewritten *in place* into Solara components/builders: `gui/tile/recipe_tile.py` now exports `build_recipe_layout` (not `RecipeTile`), `aoi_tile.py`/`overview_tile.py`/etc. export `@solara.component`s. But `ui.ipynb` still does `from gui.tile.recipe_tile import RecipeTile`, `from gui.tile.aoi_tile import AoiTile`, … — imports that now fail. So `ui.ipynb` can no longer execute. The original "verify Voila parity first, keep it live as fallback" gate is **moot**; the only live app is Solara.
2. **The new analysis tile is a package** at `gui/tile/analysis/` (`AnalysisTile`, wired into `solara_app.py`). The old `gui/tile/analysis_tile.py` (≈77 KB god-class) survives only as dead code — its **sole importer is `ui.ipynb`** (`from gui.tile.analysis_tile import AnalysisTile`). It dies with `ui.ipynb`.
3. **Task 10 Step 3 is in flight.** Working tree (uncommitted at time of writing): `D deforestation_alerts/scripts/model_worker.py` (the shim, deleted) and `M gui/tile/analysis_tile.py` (its CNN import re-pointed from `model_worker` → `cnn_worker`). Task 1 below lands this cleanly. (The `analysis_tile.py` edit is throwaway — that file is deleted in Task 4.)
4. **The "clean grep" DoD lines need interpretation, not zeroing.** After the legacy file is gone, `grep -rn '\.observe(\|link(' gui/` and `grep -rn 'threading' gui/` will *still* return hits — but every remaining one is a **deliberate, documented bridge**, not leftover work (enumerated in Task 7). The DoD's intent — *no GEE/CNN worker-thread + queue machinery, no imperative `.observe` sprinkled in widget `__init__`s* — is already met.
5. **Downstream consumers still say "Voila":** the project run-skill `.claude/skills/run-deforestation-alerts/` (launches Voila + `ui.ipynb`), `README.md`, the `docs/about/ABOUT_*.md` prose (rendered in the About tile), and a stale `.gitignore` pattern. Task 6 updates them.

**Paths:** all paths below are relative to the module root `deforestation-alerts-module/` unless noted.

---

## File structure (this task)

| File | Action | Why |
|---|---|---|
| `deforestation_alerts/scripts/model_worker.py` | **Delete** (commit staged deletion) | Folded into `cnn_worker` (Task 3/10); no live importer remains. |
| `noxfile.py` | Modify | `app` session → `solara run`; drop the `jupyter` session (used deleted `no_ui.ipynb`) + the now-unused `import tomli`. |
| `pyproject.toml` | Modify | Remove the `[sepal-ui] init-notebook` table (Solara deploys via supervisord, not an init-notebook). |
| `requirements.txt` | Modify | Drop `voila`, `jupyter`, and (if unused) `tomli`. Keep `solara`. |
| `sepal_environment.yml` | Modify | Drop `voila==0.5.8` from the `pip:` block. Keep `solara`. |
| `.github/workflows/ci.yaml` | Modify | Remove the 4 notebook/kernel steps; add a Solara compile/import health-check. |
| `ui.ipynb`, `about_ui.ipynb`, `no_ui.ipynb` | **Delete** | Voila entry + unused notebooks. |
| `gui/tile/analysis_tile.py` | **Delete** | Legacy god-class; only `ui.ipynb` imported it. |
| `run_solara.sh` (module root) | **Delete** | Stale duplicate of the canonical `gui/run_solara.sh`. |
| `gui/widget/custom_sw.py` | Modify (optional) | Prune `CustomApp`/`CustomDrawerItem`/`CustomAppBar` (only `ui.ipynb` used them). **Keep** `CustomDrawControl`/`CustomSlideGroup` (used by `gui/tile/analysis/widgets.py`). |
| `.claude/skills/run-deforestation-alerts/SKILL.md` + `driver.py` | Modify | Point the run-skill at Solara (`solara run gui/solara_app.py`, port 8900). |
| `README.md`, `docs/about/ABOUT_{en,es,fr}.md` | Modify | Replace "… and Voila" with Solara in the prose. |
| `.gitignore` | Modify | Drop the stale `*nox_ui.ipynb` line. |
| `CHANGELOG.md` | Modify | Note Voila removal under `## Unreleased`. |

---

## Task 1: Land the in-flight Task 10 carryover (clean baseline)

Get to a clean, committed working tree before the removals begin, finishing Task 10 Step 3 (delete the `model_worker.py` shim).

**Files:**
- Delete: `deforestation_alerts/scripts/model_worker.py`

- [ ] **Step 1: Confirm `model_worker` has no live importer**

Run:
```bash
grep -rn "model_worker" deforestation_alerts gui tests --include="*.py" | grep -v "__pycache__"
```
Expected: only the docstring mention in `deforestation_alerts/scripts/cnn_worker.py` (a comment, not an import). If `gui/tile/analysis_tile.py` still shows an *import* of `model_worker`, the in-flight re-point to `cnn_worker` is not saved — that's fine, the file is deleted in Task 4; you can ignore it (it's not run by the Solara app).

- [ ] **Step 2: Run the suite on the current tree**

Run: `pytest tests/ -v`
Expected: PASS (the CNN tests live in `tests/test_cnn_worker.py` and cover what `model_worker` used to).

- [ ] **Step 3: Stage the deletion and commit**

```bash
git rm -f deforestation_alerts/scripts/model_worker.py 2>/dev/null || git add -A deforestation_alerts/scripts/model_worker.py
git add deforestation_alerts/scripts/
git commit -m "refactor: delete model_worker.py shim (folded into cnn_worker) — Task 10 Step 3"
```

- [ ] **Step 4: Verify the tree is clean except for intended in-flight edits**

Run: `git status --short`
Expected: at most ` M gui/tile/analysis_tile.py` (a legacy file deleted in Task 4) — nothing else. Leave that edit; do not invest in it.

---

## Task 2: Verify full Solara parity (the gate)

This is the **acceptance gate for the whole phase**. Do not remove anything Voila-related until the Solara app passes end-to-end. (Original Task 11 Step 1 — unchanged in intent; the only difference is there is no Voila fallback to compare against, so this is the sole reference.)

**Files:** none (manual verification).

- [ ] **Step 1: Launch the Solara app**

Run: `./gui/run_solara.sh` (or `solara run gui/solara_app.py --port 8900 --no-open`), open `http://127.0.0.1:8900/`.
Expected: the MapApp shell renders with the drawer/steps.

- [ ] **Step 2: Exercise every tile, in order**

Walk the full workflow and confirm each works (no console traceback, state flows between tiles):
- **About** dialog renders the localized markdown.
- **Recipe**: New recipe → jumps to Aux; Save (after a folder exists) writes `recipe_parameters.json`; Load round-trips a saved recipe (alerts repopulate Overview).
- **AOI/Date**: select an admin AOI + dates; models update (watch GAUL-2024 admin-code resolution).
- **Aux**: set CCDC/mask/aux layers; upload a custom report template.
- **Filter**: filter by source/size/area, run Analyze → polygon alerts publish to `app_state.alerts_gdf`.
- **Analysis**: navigate alerts, load S2/Landsat/Planet imagery, run **both** CNN models, edit a polygon, save attributes, download the Word/gpkg report.
- **Overview**: the summary table + colored layers reflect the loaded alerts.

- [ ] **Step 3: Record the result**

Optionally drive it headlessly via the run-skill (`driver.py smoke`, still Voila-based until Task 6 — so for now smoke-test manually or with `solara run` + a screenshot). Note PASS/FAIL per tile.
**If any tile fails: STOP.** Finish that tile (Phase-4 Tasks 7–9) before continuing — removing Voila is irreversible-ish and there is no fallback.

---

## Task 3: Remove Voila from build/run config

**Files:**
- Modify: `noxfile.py`, `pyproject.toml`, `requirements.txt`, `sepal_environment.yml`

- [ ] **Step 1: Repoint `noxfile.py` at Solara; drop the Voila/jupyter sessions**

Replace the entire file with:
```python
"""All the process that can be run using nox.

The nox run are build in isolated environment that will be stored in .nox. to force the venv update, remove the .nox/xxx folder.
"""

import nox


@nox.session(reuse_venv=True)
def lint(session):
    """Apply the pre-commits."""
    session.install("pre-commit")
    session.run("pre-commit", "run", "--a", *session.posargs)


@nox.session(reuse_venv=True)
def app(session):
    """Run the Solara application."""
    session.install("-r", "requirements.txt")
    session.run("solara", "run", "gui/solara_app.py", "--port", "8900", "--no-open")
```
(Removed: `import tomli`, the `tomli.load(...)["sepal-ui"]["init-notebook"]` + `voila` body, and the `jupyter` session — it ran the now-deleted `no_ui.ipynb`.)

- [ ] **Step 2: Remove the `[sepal-ui]` table from `pyproject.toml`**

Delete these two lines (lines 4–5):
```toml
[sepal-ui]
init-notebook = "ui.ipynb"
```
The file should start `[project]` → `version = "1.1.1"` → `[tool.ruff]`. (SEPAL deploys this Solara module via `gui/supervisord.conf` running `solara run`, not via an `init-notebook` key — confirmed by the se.plan deployment pattern. Keep `[tool.pytest…]`, `[tool.ruff…]`, `[tool.codespell…]`.)

- [ ] **Step 3: Drop Voila + jupyter from `requirements.txt`**

Remove the `jupyter` line and the `voila==0.5.8` line (lines 2–3). Keep `solara==1.57.4`. Then check whether `tomli` is still referenced anywhere:
```bash
grep -rn "import tomli\|tomli\." . --include="*.py" | grep -v "__pycache__"
```
If that prints nothing (it should, after Step 1), also remove `tomli==2.0.2` from `requirements.txt`.

- [ ] **Step 4: Drop Voila from `sepal_environment.yml`**

Remove the line `      - voila==0.5.8` from the `pip:` block (line 39). Keep `      - solara==1.57.4`.

- [ ] **Step 5: Sanity-check the config files parse**

Run:
```bash
python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('pyproject ok')"
python -c "import yaml; yaml.safe_load(open('sepal_environment.yml')); print('env yaml ok')"
python -c "import ast; ast.parse(open('noxfile.py').read()); print('noxfile ok')"
```
Expected: `pyproject ok`, `env yaml ok`, `noxfile ok`.

- [ ] **Step 6: Commit**

```bash
git add noxfile.py pyproject.toml requirements.txt sepal_environment.yml
git commit -m "build: drop Voila/jupyter from run config; nox app session runs Solara"
```

---

## Task 4: Delete the Voila notebooks + the legacy god-class

**Files:**
- Delete: `ui.ipynb`, `about_ui.ipynb`, `no_ui.ipynb`, `gui/tile/analysis_tile.py`, `run_solara.sh` (module root)

- [ ] **Step 1: Confirm the legacy analysis tile has no live importer**

Run:
```bash
grep -rln "from gui.tile.analysis_tile\|gui.tile.analysis_tile import\|tile\.analysis_tile" . --include="*.py" --include="*.ipynb" | grep -v "__pycache__"
```
Expected: only `ui.ipynb` (which we delete in the same task). The live app imports `from gui.tile.analysis import AnalysisTile` (the package) — verify it does **not** appear here.

- [ ] **Step 2: Delete the notebooks + legacy tile + stale root script**

```bash
git rm ui.ipynb about_ui.ipynb no_ui.ipynb
git rm gui/tile/analysis_tile.py
git rm run_solara.sh        # stale duplicate; canonical is gui/run_solara.sh
```

- [ ] **Step 3: Confirm the Solara app still imports cleanly (no dangling refs)**

Run:
```bash
python -m compileall -q gui deforestation_alerts && echo "compile ok"
python -c "import gui.solara_app; print('solara app import ok')"
```
Expected: `compile ok` and `solara app import ok`. (If `import gui.solara_app` errors on EE/session side effects in your shell — `init_ee()` runs at import — the `compile ok` line is the load-bearing check; the import is re-verified live in Task 2 / CI in Task 5.)

- [ ] **Step 4: Run the suite**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove Voila notebooks (ui/about_ui/no_ui) + legacy analysis_tile.py + dup run_solara.sh"
```

- [ ] **Step 6 (optional): Prune dead widgets from `custom_sw.py`**

Only if you want the DoD greps tidier. `CustomApp`, `CustomDrawerItem`, `CustomAppBar` were imported **only** by the deleted `ui.ipynb`. Confirm, then remove just those classes (and their `.observe(` sites at lines ~30/31/466 if they belong to them):
```bash
grep -rn "CustomApp\|CustomDrawerItem\|CustomAppBar" gui deforestation_alerts --include="*.py" | grep -v "custom_sw.py\|__pycache__"
```
If that prints nothing, delete those three classes from `gui/widget/custom_sw.py`. **Do not touch `CustomDrawControl` or `CustomSlideGroup`** — `gui/tile/analysis/widgets.py` imports them. If the boundaries are unclear, skip this step (dead code in a still-imported module is harmless) and note it in Task 7. Then `pytest tests/ -v` (PASS) and commit:
```bash
git add gui/widget/custom_sw.py
git commit -m "refactor: prune unused CustomApp/CustomDrawerItem/CustomAppBar (Voila-only widgets)"
```

---

## Task 5: Switch CI to a Solara health-check

**Files:**
- Modify: `.github/workflows/ci.yaml`

- [ ] **Step 1: Remove the four notebook/kernel steps**

Delete these steps from the `ci` job (currently lines 36–57): **"Verify notebook kernelspec"**, **"Install test deps"** (nbmake/ipykernel), **"Register kernel"**, **"Test UI notebook"** (nbmake `ui.ipynb`). Keep **"Verify ee-api fork"** and **"Run unit tests"**.

- [ ] **Step 2: Add a Solara health-check step**

Append after the "Run unit tests" step:
```yaml
      - name: Solara app compile + import
        shell: micromamba-shell {0}
        run: |
          python -m compileall -q gui deforestation_alerts
          python -c "import gui.solara_app; print('solara app import ok')"
```
(If `import gui.solara_app` fails in CI because `init_ee()` needs more than `EARTHENGINE_TOKEN`, drop the `python -c …` line and keep `compileall` — the compile check still guards against dangling imports left by the deletions.)

- [ ] **Step 3: Validate the YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 4: Confirm no Voila/ui.ipynb refs remain in build/CI config**

Run:
```bash
grep -rn "voila\|ui\.ipynb\|nbmake\|init-notebook" noxfile.py pyproject.toml requirements.txt sepal_environment.yml .github/ || echo "clean"
```
Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: replace notebook/nbmake checks with a Solara compile+import health-check"
```

---

## Task 6: Update the run-skill + user-facing docs

**Files:**
- Modify: `.claude/skills/run-deforestation-alerts/SKILL.md`, `.claude/skills/run-deforestation-alerts/driver.py`, `README.md`, `docs/about/ABOUT_{en,es,fr}.md`, `.gitignore`

- [ ] **Step 1: Repoint the run-skill `SKILL.md` at Solara**

In `.claude/skills/run-deforestation-alerts/SKILL.md`:
- Frontmatter `description`: change "SEPAL/Voila app (ui.ipynb)" / "interactive Voila UI" → "SEPAL/Solara app (gui/solara_app.py)" / "interactive Solara UI".
- Body: the entrypoint is `gui/solara_app.py` served by **Solara** on **http://127.0.0.1:8900/** (not Voila/`ui.ipynb`/8866). Remove the kernel-registration section and the "wrong-kernel silent fallback" gotcha (Solara has no kernelspec). The "serve" raw command becomes:
  ```bash
  solara run gui/solara_app.py --port 8900 --no-open --reload
  ```
  Stop-a-stray-server becomes `pkill -f 'solara run gui/solara_app.py'`. The "tensorflow/huggingface_hub absent" gotcha now points at `gui/tile/analysis/` (the CNN runs via `deforestation_alerts/scripts/cnn_worker.py`, not the deleted `model_worker.py`).

- [ ] **Step 2: Repoint `driver.py` at Solara**

Read `.claude/skills/run-deforestation-alerts/driver.py` first, then change only the launch + URL plumbing (the Playwright nav logic — waiting on visible drawer text like `text=AOI/Date` — stays):
- the Voila subprocess (`voila ui.ipynb …`) → `solara run gui/solara_app.py --port 8900 --no-open`;
- base URL `http://127.0.0.1:8866/` → `http://127.0.0.1:8900/`;
- drop the `ipykernel install` / kernel-name logic;
- log path `/tmp/voila-defo.log` → `/tmp/solara-defo.log`; pkill pattern as above.

- [ ] **Step 3: Smoke-test the updated run-skill**

Run: `.claude/skills/run-deforestation-alerts/driver.py smoke`
Expected: Exit 0, shell renders, About + AOI screenshots captured. (If Playwright/driver work is out of scope for this session, at minimum confirm `solara run gui/solara_app.py --port 8900 --no-open` binds and serves, then Ctrl-C.)

- [ ] **Step 4: Update the prose docs**

In `README.md` (line 6) and `docs/about/ABOUT_en.md` / `ABOUT_es.md` / `ABOUT_fr.md` (line 1), replace "SEPAL UI framework and Voila" (and the localized equivalents — "et Voila", "y Voila") with the Solara wording, e.g. EN: "the SEPAL UI framework and Solara". These ABOUT files render in the About tile, so keep them user-facing and translated.

- [ ] **Step 5: Drop the stale `.gitignore` line**

Remove `*nox_ui.ipynb` (line 9) from `.gitignore` — it referenced a Voila/nox-generated notebook that no longer exists.

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/run-deforestation-alerts/ README.md docs/about/ .gitignore
git commit -m "docs: point run-skill + README/ABOUT at Solara; drop stale gitignore entry"
```

---

## Task 7: Reconcile the Phase-4 DoD + final validation

Verify the phase exit criteria, recording the **justified exceptions** to the "clean grep" lines so a reviewer doesn't mistake them for leftover work.

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Confirm services stay UI-free (DoD)**

Run: `grep -rn "import solara\|ipyvuetify\|reacton" deforestation_alerts/scripts/`
Expected: nothing.

- [ ] **Step 2: Enumerate the remaining `observe`/`link`/`thread` sites and confirm each is justified**

Run:
```bash
grep -rn "\.observe(\|[^a-zA-Z_]link(" gui/ --include="*.py"
grep -rn "threading\|queue\.Queue\|Thread(" gui/ --include="*.py"
```
Expected — **only** these documented bridges (no GEE/CNN worker-thread + queue machinery, no imperative `.observe` in a component `__init__`):
- `gui/tile/aoi_tile.py`, `gui/tile/analysis/edit_tools.py`, `gui/tile/alerts_filter_tile.py`: `model.observe(...)` registered **inside `solara.use_effect`** with an `.unobserve(...)` cleanup — the correct traitlets-Model → Solara bridge (traitlets mutations don't trigger Solara re-renders; each site has a comment saying so).
- `gui/tile/analysis/widgets.py`: 4 `link((…))` syncing the two ipyleaflet maps' center/zoom, the draw-control data, and the tab `v_model` — widget-to-widget links with no native Solara equivalent (spec §5).
- `gui/solara_app.py`: `nav_model.observe("current_step")` + `theme_toggle.observe("dark")` — the MapApp step + theme bridges.
- `gui/tile/recipe_tile.py`: 2 `threading.Thread(daemon=True)` for recipe load/save — **deliberate**: this tile is a raw ipyvuetify widget (not a `@solara.component`, so `use_task` hooks are unavailable) because MapApp destroys/rebuilds dialog DOM and a `solara.display`'d widget would vanish on reopen (documented at `recipe_tile.py:5-17`).
- Comment-only hits (not code): `gui/tile/overview_tile.py:135-136`, `gui/tile/alerts_filter_tile.py:331`.

If anything **else** appears, it is real leftover work — convert it (`use_effect`/shared reactive/`use_task`) before proceeding.

- [ ] **Step 3: Confirm the god-class and shim are gone**

Run:
```bash
test ! -f gui/tile/analysis_tile.py && echo "legacy analysis_tile gone"
test ! -f deforestation_alerts/scripts/model_worker.py && echo "model_worker gone"
grep -rn "class RepeatElements\|def apply_dl_model" deforestation_alerts/ | grep -v "cnn_worker.py" || echo "CNN code only in cnn_worker"
```
Expected: all three confirmations print.

- [ ] **Step 4: Full validation sweep**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yaml')); print('yaml ok')"
python -m compileall -q gui deforestation_alerts && echo "compile ok"
pytest tests/ -v
grep -rn "voila\|ui\.ipynb\|nbmake" noxfile.py pyproject.toml requirements.txt sepal_environment.yml .github/ || echo "no voila refs in build config"
```
Expected: `yaml ok`, `compile ok`, tests PASS, `no voila refs in build config`.

- [ ] **Step 5: Update the CHANGELOG**

Add under `## Unreleased` → `### Changed` (or a new `### Removed`):
```markdown
- Migrated the UI from Voila/traitlets to **Solara** (`solara run gui/solara_app.py`).
  Removed the Voila entry (`ui.ipynb`), the `voila`/`jupyter` deps, the notebook CI
  checks, and the legacy `analysis_tile.py` god-class (decomposed into the
  `gui/tile/analysis/` package over the `cnn_worker`/`imagery_service`/
  `alert_repository` services).
```

- [ ] **Step 6: Final commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog — Solara is the sole entrypoint; Voila removed"
```

---

## Definition of Done (Task 11)

- [ ] `solara run gui/solara_app.py` serves the whole app; every tile (About, Recipe, AOI, Aux, Filter, Analysis, Overview) works end-to-end (Task 2).
- [ ] No Voila anywhere: `ui.ipynb`/`about_ui.ipynb`/`no_ui.ipynb` deleted; `voila`/`jupyter` dropped from `requirements.txt` + `sepal_environment.yml`; `[sepal-ui] init-notebook` removed; `noxfile.py app` runs Solara; CI has no nbmake/kernel steps.
- [ ] Legacy `gui/tile/analysis_tile.py` and `deforestation_alerts/scripts/model_worker.py` are gone; CNN code lives only in `cnn_worker`.
- [ ] Services contain no `solara`/`ipyvuetify`/`reacton` imports.
- [ ] Every remaining `gui/` `observe`/`link`/`Thread` is one of the enumerated, documented bridges (Task 7 Step 2) — no worker-thread + queue machinery, no imperative `__init__` observers.
- [ ] Run-skill + README + ABOUT prose describe Solara, not Voila.
- [ ] `pytest tests/ -v` green; `compileall` clean; CI YAML valid.

---

## Self-review notes (author)

- **Spec coverage:** implements the original Task 11 (Voila removal + run/CI switch) against the *actual* drifted tree, plus the two genuine carryovers it depended on (Task 10 Step 3 `model_worker` deletion; legacy `analysis_tile.py` deletion). Adds the downstream consumers the original missed (run-skill, README/ABOUT, `.gitignore`).
- **Reframed gate:** the original "verify Voila parity, keep it as fallback" is replaced by "verify *Solara* parity" because the in-place tile rewrites already broke `ui.ipynb` — there is no Voila to fall back to.
- **DoD honesty:** the phase-level "grep returns nothing for `.observe(`/`link(`/`threading`" is reinterpreted, not faked. Each surviving site is a deliberate bridge (use_effect-scoped trait observers with cleanup; widget-level map/draw links; the documented raw-widget recipe threads). Task 7 Step 2 enumerates them so review is unambiguous; anything outside the list is flagged as real work.
- **Sequencing is removal-safe:** parity is verified (Task 2) before anything is deleted; config is rewired (Task 3) before the notebooks it references are removed (Tasks 4–5); the Solara `compileall`/import check runs after each deletion to catch dangling refs.
- **Open item flagged, not guessed:** the exact SEPAL deployment key for a Solara module — se.plan deploys via `supervisord.conf` running `solara run`, so `[sepal-ui] init-notebook` is removed rather than replaced; if the SEPAL platform turns out to need a Solara-specific key, add it in Task 3 Step 2 rather than reinstating the notebook.
- **Optional, low-risk work isolated:** `custom_sw.py` pruning (Task 4 Step 6) and `tomli` removal (Task 3 Step 3) are guarded by greps and skippable without affecting the DoD.
