## Unreleased

### Added

- **Projects replace recipes.** A project is a folder under
  `~/module_results/deforestation_alerts/<project>/` (or under
  `$DEFORESTATION_ALERTS_DATA_DIR`) on the sandbox disk, with a `project.json`
  saved automatically after every change and every finished job: there is no
  Save button. The Project rail item opens a dialog to create, open, duplicate
  and delete projects (delete asks for the typed name, and is refused while a
  task still writes into the project). Picking an area of interest with no
  project open starts one, and the app title names the open project.
- **The monitoring workflow**: AOI → Alerts → Period → Download & harmonize →
  Integrate & vectorize → Analysis, in the Workflow panel. Each step unlocks
  when the one before it has produced what it needs, and the pipeline header
  shows the lock reasons and each step's output count.
  - Alerts: the predefined GLAD-L, RADD and GLAD-S2 sources, with map previews.
  - Period: one monitoring period (dates included at both ends) and a
    look-back window.
  - Download & harmonize: one projected pixel grid per project (EPSG with an
    auto-UTM default, and a resolution; a geographic CRS is refused). Each
    source is downloaded from Earth Engine on its own and harmonized locally
    onto that grid as a tiled Int32 GeoTIFF (alert class, and the date as
    `yyyyddd`), with Run, Re-download, Stop and a map toggle per row.
    Fingerprints of every input mean a reopened project downloads or
    harmonizes again only what is out of date; a file deleted from disk shows
    as missing and can be run again.
  - Integrate & vectorize: combines the harmonized sources locally, one
    decimal digit per source, vectorizes block by block, applies the minimum
    size, the drawn area, the sorting, the maximum count and the optional
    attribute raster, and records an **alert set** (alert store, combined
    raster and footprint). Analysis reviews the active set.
  - Overview shows the active set as PMTiles (tippecanoe), with alert
    centroids as the fallback; an alert's popup has a Review button that
    opens it in Analysis.
- `run_ui.sh`, which serves `ui.ipynb` with Voila inside a Jupyter server as
  SEPAL does, with map tiles through `/proxy/{port}`; `localtileserver` and
  `vectortileserver` for raster and vector tiles; GDAL display guards.
- `scripts/measure_prepare.py`, which measures one national-scale download,
  harmonize and integrate run (time, file sizes, peak memory).

### Changed

- The free-space checks before a download, a harmonize and an Integrate &
  vectorize run use a **compressed** size estimate (5 % of the uncompressed
  raster size, and twice that for the room a run needs). At 10 m over
  Paraguay a source row asks for about 7 GB and Integrate for about 13 GB,
  instead of the 130 to 300 GB the uncompressed size asked for. The grid
  form's warning above 2 GB per source still reports the uncompressed size.
- Moved to the official **pysepal 4.1.0** release (with solara 1.62.0 and
  ee-client >= 3.2.1), replacing the pysepal 3.8 fork-SHA pin described further
  down: pysepal 4 resolves Voila sessions from runtime topology, so the fork's
  voila-local-sessions patch is no longer needed. The app now mounts pysepal's
  `NotificationProvider` and passes the session `gee_interface` to every
  Earth Engine-backed widget.
- Removed the on-map `LogConsole` (and its log bridge) in favour of pysepal
  notifications: toasts and the task pill are now the only feedback channel.
- Exports, the Analysis CNN run, and the Download & harmonize and Integrate &
  vectorize runs each run as a tracked job with its own entry in the pysepal
  task pill; the app's INFO log lines emitted by that job surface there as step
  milestones, and a failure fails the task with a single error toast.
- Earth Engine downloads report tile-by-tile progress, show a one-time "queued"
  notice when another download is already running, and export only the AOI's
  bounding box as the request region (the footprint is still applied by the
  clip), so very detailed AOIs no longer fail with "Description length exceeds
  maximum".
- Fixed: selecting a drawn AOI failed under pysepal 4 with a `TasksResponse`
  validation error, because ee-client rejected Earth Engine's empty task list.
- The app adopts the Spatial Risk workflow side panel: the workflow steps live
  in a "Workflow" right-panel view under a pipeline header (segment strip,
  Back/Next, step picker with lock reasons and output counts). The left
  drawer is now an icon rail holding About, Project and Overview.
- Overview has its own rail button and its own right-panel view outside the
  workflow, keeping the status summary, exports and status clusters on the map.
- The Analysis two-map stage is hosted by the app (`StageHost`) instead of
  MapApp's step slot.
- Each workflow step now opens with a one-line description (Analysis also with an
  info button).
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
  notification system in place of ad hoc inline widgets; routed all user-file I/O
  (alert store, exports, reports, persisted settings) through the `Workspace` adapter
  (`deforestation_alerts/scripts/workspace.py`); and added browser-side `jslink` sync
  between the Analysis workspace's paired maps. `Workspace` first went through the
  SEPAL files API (`SepalClient`) for the app-launcher container; the app now runs in
  the user's sandbox, so it reads and writes the sandbox disk directly.

### Removed

- **Recipes**: the Recipes dialog and the recipe save and load. Old recipes are not
  converted; start a project instead.
- The **Optional files** dialog, with its CCDC and custom alert sources, alert mask,
  reference layer and Planet login. The attribute raster moved into Integrate &
  vectorize; the other inputs are not available in this release.
- The **Filter** step and the old **AOI/Date** step with its source search: the AOI,
  Alerts, Period, Download & harmonize and Integrate & vectorize steps replace them.
- The Docker deployment for SEPAL's app launcher: `gui/Dockerfile`,
  `gui/supervisord.conf`, `docker-compose.yml` and `.dockerignore`. The app runs as
  Voila in the user's sandbox (`ui.ipynb`, `run_ui.sh`).
- The `deforestation_alerts.parameter` package. Its container-local `module_dir` was
  replaced by the `Workspace` adapter, and the GDAL temp-directory configuration that
  rode along on its import now runs explicitly in `gui/solara_app.py`.

## v_1.0 (2025-03-25)

### Feature

- Initial release