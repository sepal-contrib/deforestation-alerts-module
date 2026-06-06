# Phase 2: Folder Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-lay the module from the legacy `component/` layout to the modern
`data/ docs/ gui/ deforestation_alerts/` structure, with **no behavior change** and
the app still launchable under Voila.

**Architecture:** A mechanical move. The importable package (models, services,
messages, parameters) becomes `deforestation_alerts/`; the view layer (tiles +
widgets) becomes top-level `gui/`; bundled static inputs move to `data/`;
documentation consolidates under `docs/`. Every `from component.…` import is
rewritten to its new home, and the two relative-path string references
(`utils/report_template.docx`, `utils/{ABOUT}`) are repointed. The Phase 0
characterization suite is the regression guard and must stay green throughout.

**Tech Stack:** Python, pytest, pysepal (ipyvuetify + traitlets), Voila. No Solara
yet (Phase 4), no raster-pipeline change yet (Phase 3).

**Prerequisite:** Phases 0 (test safety net) and 1 (`sepal_ui`→`pysepal` rename)
are complete. This plan assumes imports already say `pysepal`, the `tests/` suite
exists and is green, and `pyproject.toml` already has `[tool.pytest.ini_options]`
with `pythonpath = ["."]`.

**Scope note:** Second of the per-phase plans derived from
`docs/superpowers/specs/2026-06-06-pysepal-solara-migration-design.md` (spec §8
Phase 2, §4.1 target layout, D6/D7). Phase 3 (raster pipeline) and Phase 4 (Solara)
get their own plans.

---

## Design decisions locked here (and why)

| # | Decision | Rationale |
|---|---|---|
| R1 | **Use `git mv`** for every move (the module dir *is* a git repo). | Preserves file history; git still detects renames even with import-line edits because file similarity stays high. |
| R2 | **No top-level `__init__.py`** for `deforestation_alerts/` or `gui/`. | Matches the current layout: `component/` had no `__init__.py` (it worked as an implicit namespace package on `pythonpath="."`). The sub-package `__init__.py` files travel with their directories. |
| R3 | **Keep the `[sepal-ui]` table, `ui.ipynb` entry, and `noxfile.py` as-is.** | `init-notebook` under `[sepal-ui]` is a SEPAL *platform* deployment contract (and `noxfile.py` reads `["sepal-ui"]["init-notebook"]`). Renaming it risks breaking deployment. The Voila entry stays live through Phase 3; Phase 4 replaces it with Solara. |
| R4 | **`data/` holds only `report_template.docx` this phase.** | D6: `data/` is bundled *static* inputs. `recipe_schema.json` belongs to the recipe-validation work in Phase 3 (D10); do not create it here. Runtime outputs stay under `~/module_results/deforestation_alerts/` via `parameter/directory.py` (unchanged). |
| R5 | **The view layer moves now but stays traitlets classes.** | Spec §8: Phase 2 moves tiles/widgets to `gui/` *and* stays Voila-runnable. The Solara rewrite of those files is Phase 4; this phase only changes their location and imports. |

---

## Target layout after this phase

```
deforestation-alerts-module/            # repo root (the git repo)
├── data/
│   └── report_template.docx            # moved from utils/
├── docs/
│   ├── en.rst                          # moved from doc/en.rst
│   ├── about/
│   │   ├── ABOUT_en.md  ABOUT_es.md  ABOUT_fr.md   # moved from utils/
│   └── superpowers/                    # unchanged (specs + plans)
├── gui/                                # view layer (traitlets for now)
│   ├── tile/                           # moved from component/tile/
│   │   ├── __init__.py  aoi_tile.py  aux_tile.py  recipe_tile.py
│   │   ├── alerts_filter_tile.py  overview_tile.py  analysis_tile.py
│   └── widget/                         # moved from component/widget/
│       ├── __init__.py  custom_sw.py
├── deforestation_alerts/               # importable package (moved from component/)
│   ├── model/      __init__.py  aoi_date_model.py  aux_model.py  alerts_model.py  analysis_model.py  app_model.py
│   ├── scripts/    __init__.py  aoi_helper.py  alert_filter_helper.py  mosaics_helper.py  overview_helper.py  recipe_helper.py  report_builder.py  model_worker.py
│   ├── message/    __init__.py  en/  es-ES/  fr/
│   └── parameter/  __init__.py  directory.py
├── tests/                              # Phase 0 suite (imports repointed here)
├── ui.ipynb  no_ui.ipynb  about_ui.ipynb
├── pyproject.toml  requirements.txt  noxfile.py  sepal_environment.yml  CHANGELOG.md  README.md
```

`component/` no longer exists after this phase.

---

## File-move map (exhaustive)

| From | To | Mechanism |
|---|---|---|
| `component/scripts/` | `deforestation_alerts/scripts/` | `git mv` |
| `component/model/` | `deforestation_alerts/model/` | `git mv` |
| `component/message/` | `deforestation_alerts/message/` | `git mv` |
| `component/parameter/` | `deforestation_alerts/parameter/` | `git mv` |
| `component/tile/` | `gui/tile/` | `git mv` |
| `component/widget/` | `gui/widget/` | `git mv` |
| `utils/report_template.docx` | `data/report_template.docx` | `git mv` |
| `utils/ABOUT_en.md` | `docs/about/ABOUT_en.md` | `git mv` |
| `utils/ABOUT_es.md` | `docs/about/ABOUT_es.md` | `git mv` |
| `utils/ABOUT_fr.md` | `docs/about/ABOUT_fr.md` | `git mv` |
| `doc/en.rst` | `docs/en.rst` | `git mv` |

## Import-rewrite map (exhaustive)

| Old import prefix | New import prefix |
|---|---|
| `from component.widget` / `import component.widget` | `gui.widget` |
| `from component.tile` / `import component.tile` | `gui.tile` |
| `from component import parameter` | `from deforestation_alerts import parameter` |
| `from component.message` | `deforestation_alerts.message` |
| `from component.model` | `deforestation_alerts.model` |
| `from component.scripts` | `deforestation_alerts.scripts` |
| `from component.parameter` | `deforestation_alerts.parameter` |

Applies to every `.py` under the moved trees **and** `tests/*.py` **and** the three
notebooks.

---

# TASKS

### Task 1: Move the importable package and the view layer

This is the structural move. We do moves and import-rewrites in one task so the
repo is internally consistent at the single commit. All commands run from the
module root: `cd deforestation-alerts-module`.

**Files:**
- Move: `component/{scripts,model,message,parameter}/` → `deforestation_alerts/…`
- Move: `component/{tile,widget}/` → `gui/…`
- Modify (imports): every `.py` under `deforestation_alerts/`, `gui/`, and `tests/`

- [ ] **Step 1: Confirm a clean starting point (Phase 0/1 green)**

Run: `pytest tests/ -v && git status --short`
Expected: all tests PASS; working tree clean. If tests are red, STOP — fix Phase 0/1 first; this plan depends on a green baseline.

- [ ] **Step 2: Create the new top-level directories and move the package subdirs**

```bash
mkdir -p deforestation_alerts gui
git mv component/scripts   deforestation_alerts/scripts
git mv component/model     deforestation_alerts/model
git mv component/message   deforestation_alerts/message
git mv component/parameter deforestation_alerts/parameter
git mv component/tile      gui/tile
git mv component/widget    gui/widget
```

- [ ] **Step 3: Remove the now-empty `component/`**

Run: `rmdir component 2>/dev/null; ls -d component 2>&1`
Expected: `ls: cannot access 'component': No such file or directory` (it had no top-level `__init__.py` and all subdirs moved, so it is empty). If `rmdir` fails because something remains, inspect with `ls -la component/` and move/remove the straggler.

- [ ] **Step 4: Rewrite imports across the moved Python and the tests**

This `sed` targets only `from …`/`import …` lines, so comments/docstrings that
contain the word "component" are left alone. Order matters: `widget`/`tile` map to
`gui`, everything else to `deforestation_alerts`.

```bash
grep -rlE '(from|import) +component' deforestation_alerts/ gui/ tests/ --include='*.py' \
  | xargs sed -i -E \
    -e 's/(from |import )component\.widget/\1gui.widget/g' \
    -e 's/(from |import )component\.tile/\1gui.tile/g' \
    -e 's/(from )component( import parameter)/\1deforestation_alerts\2/g' \
    -e 's/(from |import )component\.(message|model|scripts|parameter)/\1deforestation_alerts.\2/g'
```

- [ ] **Step 5: Verify zero `component` imports remain in code/tests**

Run: `grep -rnE '(from|import) +component' deforestation_alerts/ gui/ tests/ --include='*.py'`
Expected: **no output** (exit code 1). If anything prints, fix it by hand and re-run.

- [ ] **Step 6: Verify the rewritten package imports resolve**

```bash
python -c "import warnings; warnings.simplefilter('ignore'); \
from deforestation_alerts.scripts import report_builder, recipe_helper, alert_filter_helper, mosaics_helper, aoi_helper, overview_helper; \
from deforestation_alerts.model import app_model, aux_model, alerts_model, analysis_model; \
from gui.widget.custom_sw import CustomApp, CustomDrawerItem, CustomAppBar; \
from gui.tile.aoi_tile import AoiTile; \
from gui.tile.analysis_tile import AnalysisTile; \
print('restructured imports ok')"
```
Expected: `restructured imports ok`. (Importing the tiles also exercises the `gui.widget` ↔ `deforestation_alerts.scripts/model` cross-imports.)

- [ ] **Step 7: Run the Phase 0 suite against the new layout**

Run: `pytest tests/ -v`
Expected: PASS — same tests as Phase 0, now importing `deforestation_alerts.*`. (The `sed` in Step 4 rewrote the test imports too.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: move component/ to deforestation_alerts/ (package) and gui/ (views)"
```

---

### Task 2: Relocate static assets and repoint their path strings

**Files:**
- Move: `utils/report_template.docx` → `data/report_template.docx`
- Move: `utils/ABOUT_{en,es,fr}.md` → `docs/about/ABOUT_{en,es,fr}.md`
- Move: `doc/en.rst` → `docs/en.rst`
- Modify: `gui/tile/analysis_tile.py` (report-template path string)
- Modify: `pyproject.toml` (codespell skip glob)

- [ ] **Step 1: Move the bundled inputs and docs**

```bash
mkdir -p data docs/about
git mv utils/report_template.docx data/report_template.docx
git mv utils/ABOUT_en.md docs/about/ABOUT_en.md
git mv utils/ABOUT_es.md docs/about/ABOUT_es.md
git mv utils/ABOUT_fr.md docs/about/ABOUT_fr.md
git mv doc/en.rst docs/en.rst
```

- [ ] **Step 2: Remove the emptied legacy dirs**

Run: `rmdir utils doc 2>/dev/null; ls -d utils doc 2>&1`
Expected: both report "No such file or directory". If `rmdir utils` fails, run `ls -la utils/` — there should be nothing left (the four files above were its only contents); move/remove any straggler, then retry.

- [ ] **Step 3: Repoint the report-template default path in `analysis_tile.py`**

In `gui/tile/analysis_tile.py`, change the default template path (currently the
only `utils/` reference in code):

Replace:
```python
        if self.aux_model.custom_report_template == "":
            report_template = "utils/report_template.docx"
```
with:
```python
        if self.aux_model.custom_report_template == "":
            report_template = "data/report_template.docx"
```

(This is a relative path resolved against the working directory — the repo root, where Voila launches — exactly as before; only the folder name changes.)

- [ ] **Step 4: Update the codespell skip glob in `pyproject.toml`**

Replace:
```toml
[tool.codespell]
skip = 'component/message/**/*.json'
```
with:
```toml
[tool.codespell]
skip = 'deforestation_alerts/message/**/*.json'
```

- [ ] **Step 5: Verify there are no remaining `utils/` or `component/` path references in code/config**

```bash
grep -rn "utils/\|component/" --include='*.py' --include='*.toml' deforestation_alerts/ gui/ tests/ pyproject.toml
grep -rn "doc/en.rst" . --include='*.py' --include='*.toml' --include='*.cfg' --include='*.yaml' --include='*.yml' 2>/dev/null
```
Expected: no output for the first command. For the second, if any file references `doc/en.rst` (e.g. a docs-build config), update it to `docs/en.rst`; if there is no output, nothing references it.

- [ ] **Step 6: Confirm assets landed and the suite is still green**

```bash
test -f data/report_template.docx && echo "template ok"
test -f docs/about/ABOUT_en.md && echo "about ok"
pytest tests/ -v
```
Expected: `template ok`, `about ok`, then PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: relocate report template to data/, docs into docs/; repoint paths"
```

---

### Task 3: Repoint the notebooks and smoke-test the app

The three notebooks still import `from component.*` and reference `utils/{ABOUT}`.
Notebooks are JSON; the imports live in cell `source` arrays. Only `ui.ipynb` and
`about_ui.ipynb` reference `component`/`utils` (verified: `no_ui.ipynb` has neither),
but we run the rewrite over all three defensively.

**Files:**
- Modify: `ui.ipynb`, `about_ui.ipynb` (imports + ABOUT path); `no_ui.ipynb` (defensive, no-op expected)

- [ ] **Step 1: Rewrite `component` imports inside the notebook JSON**

```bash
for nb in ui.ipynb about_ui.ipynb no_ui.ipynb; do
  [ -f "$nb" ] && sed -i -E \
    -e 's/(from |import )component\.widget/\1gui.widget/g' \
    -e 's/(from |import )component\.tile/\1gui.tile/g' \
    -e 's/(from )component( import parameter)/\1deforestation_alerts\2/g' \
    -e 's/(from |import )component\.(message|model|scripts|parameter)/\1deforestation_alerts.\2/g' \
    "$nb"
done
```

- [ ] **Step 2: Repoint the ABOUT path in `ui.ipynb` and `about_ui.ipynb`**

Both notebooks build the About tile with `sw.TileAbout(f"utils/{cm.about.pathname}")`.
Repoint to the new `docs/about/` location:

```bash
sed -i 's#utils/{cm.about.pathname}#docs/about/{cm.about.pathname}#g' ui.ipynb about_ui.ipynb
```

- [ ] **Step 3: Verify the notebooks are valid JSON and free of `component`/`utils`**

```bash
python -c "import json; [json.load(open(f)) for f in ['ui.ipynb','no_ui.ipynb','about_ui.ipynb']]; print('notebooks valid json')"
grep -lE '(from|import) +component|utils/' ui.ipynb no_ui.ipynb about_ui.ipynb || echo "no component/utils refs left"
```
Expected: `notebooks valid json` then `no component/utils refs left`.

- [ ] **Step 4: Confirm the CI kernelspec invariant still holds**

Run: `python3 -c "import json; nb=json.load(open('ui.ipynb')); print(nb['metadata']['kernelspec']['name'])"`
Expected: `venv-deforestation-alerts-module` (the `sed` must not have touched metadata; if it did, restore that value — CI asserts it).

- [ ] **Step 5: Smoke-test the entry notebook end-to-end (requires GEE creds)**

Run: `pytest --nbmake ui.ipynb --nbmake-timeout=600`
Expected: PASS — `ui.ipynb` executes top to bottom against the new layout (imports resolve from `gui.*`/`deforestation_alerts.*`, the About tile loads from `docs/about/`). If credentials are unavailable locally, defer to CI and note it; do not skip silently.

- [ ] **Step 6: Run the unit suite one final time**

Run: `pytest tests/ -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add ui.ipynb about_ui.ipynb no_ui.ipynb
git commit -m "refactor: repoint notebook imports and ABOUT path to new layout"
```

---

### Task 4: Update human-facing references and finalize

A light pass to keep README/docs honest about the new paths. No code behavior.

**Files:**
- Modify: `README.md`, `CHANGELOG.md` (and any doc that names `component/`/`utils/`)

- [ ] **Step 1: Find stale path references in prose**

Run: `grep -rn "component/\|utils/\|\bdoc/" README.md CHANGELOG.md docs/ --include='*.md' --include='*.rst' 2>/dev/null | grep -v docs/superpowers/`
Expected: a (possibly empty) list of prose mentions. (Exclude `docs/superpowers/` — the spec/plans intentionally document the old layout.)

- [ ] **Step 2: Update each stale reference**

For every line from Step 1, edit the prose to the new path (`component/` → split into
`deforestation_alerts/` or `gui/` as appropriate; `utils/report_template.docx` →
`data/report_template.docx`; `utils/ABOUT_*` → `docs/about/ABOUT_*`; `doc/` → `docs/`).
If Step 1 produced no output, skip.

- [ ] **Step 3: Add a CHANGELOG entry**

Add under the unreleased section of `CHANGELOG.md`:
```markdown
### Changed
- Restructured the module to the `data/ docs/ gui/ deforestation_alerts/` layout
  (was `component/` + `doc/` + `utils/`). No functional change.
```

- [ ] **Step 4: Full verification sweep**

```bash
pytest tests/ -v
python -c "from gui.tile.analysis_tile import AnalysisTile; from gui.tile.overview_tile import OverviewTile; print('view imports ok')"
grep -rnE '(from|import) +component' . --include='*.py' --include='*.ipynb' | grep -v docs/superpowers/ || echo "no component imports anywhere"
```
Expected: tests PASS; `view imports ok`; `no component imports anywhere`.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md docs/
git commit -m "docs: update path references for the new module layout"
```

---

## Definition of done (Phase 2)

- [ ] `component/` is gone; `deforestation_alerts/` (model+scripts+message+parameter) and `gui/` (tile+widget) exist.
- [ ] `data/report_template.docx`, `docs/about/ABOUT_{en,es,fr}.md`, and `docs/en.rst` exist; `utils/` and `doc/` are gone.
- [ ] `grep -rnE '(from|import) +component' . --include='*.py' --include='*.ipynb'` returns nothing outside `docs/superpowers/`.
- [ ] No `utils/` path strings remain in code; `analysis_tile.py` points at `data/report_template.docx`; notebooks point at `docs/about/`.
- [ ] `pytest tests/ -v` passes (Phase 0 characterization + Phase 1 guard tests, repointed).
- [ ] `ui.ipynb` kernelspec name is unchanged (`venv-deforestation-alerts-module`).
- [ ] `pytest --nbmake ui.ipynb` passes (locally or in CI) — the app still launches under Voila.
- [ ] App behavior is unchanged (no feature added or removed).

---

## Self-review notes (author)

- **Spec coverage:** Implements spec §8 Phase 2 and the §4.1 target layout, honoring
  D6 (`data/` = static inputs only; `recipe_schema.json` deferred to Phase 3) and D7
  (views in `gui/`, package in `deforestation_alerts/`). D5 (Voila runnable) is held
  by Task 3's nbmake smoke test.
- **Deliberately deferred:** `recipe_schema.json` (D10, Phase 3), the `[sepal-ui]`→
  Solara entry switch and `noxfile.py`/CI changes (Phase 4), and any logic change
  (Phases 3–4). This phase only relocates and repoints.
- **CI is untouched:** `.github/workflows/ci.yaml` references `ui.ipynb` (unchanged
  entry), the ee-fork check, the kernelspec check, and the Phase 0 `pytest tests/`
  step — none reference `component/`, so no CI edit is required. The kernelspec
  invariant is explicitly re-verified (Task 3 Step 4).
- **Move + edit in one commit (Task 1):** git records these as renames because only
  import lines change (high file similarity). If a reviewer prefers pure-rename
  commits, split Step 2 (moves) and Steps 4–7 (rewrites) into two commits — the
  intermediate commit will have a red suite, which is acceptable for a single
  logical restructure but noted here as a trade-off.
- **Namespace packages (R2):** no top-level `__init__.py` is added, matching how
  `component/` worked under `pythonpath="."`. If a later phase pip-installs the app,
  add explicit `[tool.setuptools.packages]` discovery then — out of scope now.
