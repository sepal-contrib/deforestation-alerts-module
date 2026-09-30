## Unreleased

### Changed

- Optional files: add up to five custom alert sources (RADD-format Earth Engine assets), each with its own name in the Filter step and in the alert list. Recipes with the previous single custom source still load.
- The app adopts the Spatial Risk workflow side panel: AOI/Date → Filter →
  Analysis live in a "Workflow" right-panel view under a pipeline header
  (segment strip, Back/Next, step picker with lock reasons and output counts).
  The left drawer is now an icon rail holding About, Recipes, Overview and
  Optional files.
- Overview has its own rail button and its own right-panel view outside the
  workflow, keeping the status summary, exports and status clusters on the map.
- The Analysis two-map stage is hosted by the app (`StageHost`) instead of
  MapApp's step slot.
- Each workflow step now opens with a one-line description and an info button.
- Fixed: the pipeline header's accent colour followed Solara's own dark mode
  rather than the app's pysepal `ThemeState`, so the strip and step badge stayed
  gold in the light theme and never repainted on a theme toggle.
- Migrated the UI from Voila/traitlets to **Solara** (`solara run gui/solara_app.py`).
  Removed the Voila entry (`ui.ipynb`), the `voila`/`jupyter` deps, the notebook CI
  checks, and the legacy `analysis_tile.py` god-class (decomposed into the
  `gui/tile/analysis/` package over the `cnn_worker`/`imagery_service`/
  `alert_repository` services). A thin voila wrapper (`ui.ipynb`) was later restored
  around the Solara `Page` component so the classic SEPAL launch path keeps working
  alongside `solara run` — the entry was removed then restored, not permanently
  dropped.
- Restructured the module to the `data/ docs/ gui/ deforestation_alerts/` layout
  (was `component/` + `doc/` + `utils/`). No functional change.
- SEPAL app requirements update: bumped `sepal_environment.yml` to pysepal 3.8 (pinned
  to a fork commit SHA to pick up the voila-local-sessions fix), rewrote the deploy
  spec so every pip dependency is exactly pinned (git deps pinned to an immutable
  tag/SHA) with no `-e .`; added fail-fast GDAL scratch-directory configuration for
  SEPAL's read-only module mount; centralized user-facing feedback through pysepal's
  notification system in place of ad hoc inline widgets; migrated all user-file I/O
  (recipe/alert store, exports, reports, persisted settings) to the `SepalClient`-backed
  `Workspace` adapter (`deforestation_alerts/scripts/workspace.py`) so nothing writes to
  the container's local disk; and added browser-side `jslink` sync between the Analysis
  workspace's paired maps.

### Removed

- The `DEFORESTATION_ALERTS_DATA_DIR` environment variable. User data now lives in the
  SEPAL workspace resolved from the session `SepalClient` (`Workspace`), so there is no
  container-local data directory left to override.
- The `deforestation_alerts.parameter` package. Its container-local `module_dir` was
  replaced by the `Workspace` adapter, and the GDAL temp-directory configuration that
  rode along on its import now runs explicitly in `gui/solara_app.py`.

## v_1.0 (2025-03-25)

### Feature

- Initial release