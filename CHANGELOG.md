## Unreleased

### Changed

- Migrated the UI from Voila/traitlets to **Solara** (`solara run gui/solara_app.py`).
  Removed the Voila entry (`ui.ipynb`), the `voila`/`jupyter` deps, the notebook CI
  checks, and the legacy `analysis_tile.py` god-class (decomposed into the
  `gui/tile/analysis/` package over the `cnn_worker`/`imagery_service`/
  `alert_repository` services).
- Restructured the module to the `data/ docs/ gui/ deforestation_alerts/` layout
  (was `component/` + `doc/` + `utils/`). No functional change.

## v_1.0 (2025-03-25)

### Feature

- Initial release