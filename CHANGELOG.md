## Unreleased

### Changed

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