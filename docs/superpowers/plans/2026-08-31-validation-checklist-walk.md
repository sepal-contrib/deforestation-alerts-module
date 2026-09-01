# Validation checklist walk — Task 13

Walked against `~/Desktop/projects/pysepal/skills/pysepal-app/references/validation-checklist.md`
on branch `feat/phase4-scaffolding-alignment` at commit `d8f54cd` (pre-Task-13 tree). Every
bullet from that file is reproduced below with a verdict and evidence.

## Required Checks

- **The user explicitly chose the app type.** PASS — this is a pre-existing app (deforestation
  alerts) undergoing a requirements-update migration, not a fresh scaffold; the app-type
  decision was made when the app was first built.

- **The generated structure matches the chosen app family.** PASS — `data/ docs/ gui/
  deforestation_alerts/` layout (CHANGELOG "Restructured the module" entry), matching the
  pysepal Solara-app family layout.

- **New app state uses `solara.reactive()`.** PASS — `gui/store/state_manager.py` (`AppState`)
  and `gui/store/project_writers.py` use `solara.reactive(...)` exclusively for the app state
  introduced/ported in this work; `grep -rl "solara.reactive" gui/` → 7 files.

- **No new traitlets observer architecture was scaffolded.** PASS, with a note. `grep -rn
  "@observe\|\.observe(" gui/ deforestation_alerts/` finds 13 hits, all `Model.observe(...)`
  calls on existing pysepal-style traitlets `Model` objects (`aux_model`, `alert_filter_model`,
  `analyzed_alerts_model`, `theme_state`, `checkbox` widgets, `nav_model`) built once inside
  `solara.use_memo` layouts — the pre-existing widget-binding pattern documented in
  `gui/tile/aux_tile.py`'s module docstring, not a new observer architecture.

- **Every referenced pysepal component exists in the live discovery output.** Not re-verified in
  this task — this was Task 3's job ("discovery at the installed tag"), already landed at
  `pysepal==3.8.0` pinned by SHA `054db183`. Not re-walked here; no new pysepal component
  references were added in Tasks 9–12 beyond what Task 3 already validated.

- **GEE flows inside Solara components avoid blocking sync calls.** PASS — `grep -rln
  "use_task\|solara.lab.use_task" gui/tile/analysis/*.py` hits `imagery.py`,
  `analysis_tile.py`, `edit_tools.py`, `widgets.py`; also `overview_tile.py`,
  `recipe_tile.py`, `aoi_tile.py`, `alerts_filter_tile.py`, `gui/solara_app.py`,
  `gui/scripts/solara_threads.py`.

- **GEE/container user-file operations use `SepalClient`, not `Path`, `os`, `shutil`, `glob`,
  `open()`, or container-local filesystem writes.** PASS for the migrated surface — Phase 6
  (Tasks 10–12) routed recipe/alert store, exports, reports, and persisted settings through
  `deforestation_alerts/scripts/workspace.py` (`Workspace`/`SepalClient`). **Known/deferred, not
  compliant**: pysepal's stock `FileInput` widget (`pysepal.sepalwidgets.file_input.FileInput`,
  used in `gui/tile/aux_tile.py:66` and `gui/tile/recipe_tile.py:191`) lists remote files via
  its own constructor logic inside the memoized recipe/aux layouts — that listing path is
  pysepal library code, not this repo's, and was not touched. Recorded as a known parked item
  per the task brief, not reported as compliant.

- **`sepal_environment.yml` is the deploy authority; `pyproject.toml` mirrors it.**
  **DEVIATION, user-approved (Decision 5)** — `pyproject.toml` keeps `dependencies = []` by
  design (comment at `pyproject.toml:10-13`: "Runtime dependencies are NOT declared here:
  sepal_environment.yml is the packaging authority"). `sepal_environment.yml` is the sole deploy
  authority; `pyproject.toml` does not mirror it. This is Decision 5 from the plan, not a defect.

- **`requirements.txt` was not generated.** PASS — `find . -iname "requirements*.txt"` (excluding
  `.git/`) returns no hits.

- **`sepal_environment.yml` does NOT contain `-e .`, and every pip dep is pinned.** PASS — no
  `-e .` in the file (only a comment noting its deliberate absence at line 65). Every pip dep is
  pinned: `geedim==2.0.0`, `huggingface_hub==1.29.0`, `matplotlib-scalebar==0.9.0`,
  `MightyMosaic==1.2.3`, `python-docx==1.2.0`, `pygaul==0.4.2`, `solara==1.57.4`,
  `tensorflow==2.17.0` are `==` pins; the two git deps are pinned to immutable references —
  `earthengine-api @ git+...@v1.6.14` (tag) and `pysepal @ git+...@054db183` (commit SHA, per
  Decision 4's documented fallback).

- **No path resolves relative to the CWD (read-only on SEPAL); user outputs go under
  `~/module_results/<module_name>/`.** PASS — `tests/test_gdal_readonly_cwd.py` (GDAL scratch
  read-only-CWD regression test) green; `tests/test_data_dir.py` was intentionally deleted in
  commit `1ad83e9` ("refactor: exports/reports/settings through the workspace adapter +
  regression guard") because Phase 6 removed the local `~/module_results` data-dir concept
  entirely in favor of `SepalClient`-backed remote writes (`deforestation_alerts/parameter/
  directory.py` deleted in the same commit) — user outputs now go through the session
  `Workspace`, which resolves against the user's SEPAL home, never the CWD.

- **If the app runs under voila: `ui.ipynb` kernelspec is `venv-<repo-name>` and the
  `references/sepal-deployment.md` checklist was walked.** PASS — `ui.ipynb` kernelspec name is
  `venv-deforestation-alerts-module` (`ui.ipynb:29`); the local kernel is registered
  (`jupyter kernelspec list` → `venv-deforestation-alerts-module
  /home/jserafini/micromamba/envs/defo-alerts/share/jupyter/kernels/venv-deforestation-alerts-module`);
  bare `voila ui.ipynb --port=8911 --no-browser --show_tracebacks=True` booted successfully
  (`HTTP 200` from `curl localhost:8911/`, log in `/tmp/voila-boot.log`); `grep -i "Could not
  find a kernel" /tmp/voila-boot.log` → no match. The `sepal-deployment.md` checklist itself is
  walked line-by-line in the Task 13 report.

- **`component/message/` exists.** DEVIATION (repo-layout, pre-existing, not a defect) —
  this repo does not use the `component/` prefix; message catalogs live at
  `deforestation_alerts/message/` (per the CHANGELOG's "Restructured the module" entry, the repo
  moved off `component/` to the `data/ docs/ gui/ deforestation_alerts/` layout). The directory
  exists and holds the translation catalogs (`en`, `es-ES`, `fr`); only the path prefix differs
  from the generic pysepal-scaffold convention.

- **Existing repo edits, if any, do not silently replace user-owned architecture.** PASS — the
  task brief's "Deferred / out of scope" section documents the guardrails observed
  (raw-ipyvuetify tiles kept for MapApp dialog-survival fixes, not rewritten to pure Solara);
  no user-owned architecture was replaced in Tasks 9–12.

## GEE-Specific Checks

- **`with_sepal_sessions` is used where session-bound behavior is required.** PASS —
  `grep -rln with_sepal_sessions gui/ deforestation_alerts/` → `gui/solara_app.py`,
  `deforestation_alerts/scripts/workspace.py`.

- **`get_current_gee_interface()` is called inside components, not at module level.** PASS — the
  single call site is `gui/solara_app.py:237`, inside the `Page()` component body (indented
  under the component function, not at module top level).

- **`solara.lab.use_task` is used for non-blocking GEE flows.** PASS — see "GEE flows ... avoid
  blocking sync calls" above; same evidence.

- **direct UI-sized results use async getters.** PASS (consistent with Tasks 6/9 lifecycle
  work) — not independently re-audited line-by-line in Task 13; no regressions found by the full
  suite or the smoke boot.

- **long-running work uses export submission methods.** PASS (consistent with Task 6's
  task-lifecycle refactor) — same basis as above.

## Handoff Checks

- **The user summary clearly distinguishes ready code from stubs.** N/A for this task — Task 13
  is verification-only; the plan's own "Deferred / out of scope" section (both at the top level
  and per-task) already does this distinguishing for the whole migration.

- **Missing mappings to pysepal components are stated explicitly.** N/A — no new pysepal
  component mappings were introduced in Task 13.
