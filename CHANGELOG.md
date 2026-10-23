## v2.0.0 (unreleased)

### Added

- **Projects replace recipes.** A project is a folder under
  `~/module_results/deforestation_alerts/<project>/` (or under
  `$DEFORESTATION_ALERTS_DATA_DIR`) on the sandbox disk, with a `project.json`
  saved automatically after every change and every finished job: there is no
  Save button. The Project rail item opens a dialog to create, open, duplicate
  and delete projects (delete asks for the typed name, and is refused while a
  task still writes into the project). Picking an area of interest with no
  project open starts one, and the app title names the open project.
- **The monitoring workflow**: AOI → Alerts → Optional layers → Period →
  Download & harmonize → Integrate & vectorize → Analysis, in the Workflow
  panel. Each step unlocks when the one before it has produced what it needs,
  and the pipeline header shows the lock reasons and each step's output count.
  Every step opens with a one-line description and an info button.
  - Alerts: the predefined GLAD-L, RADD and GLAD-S2 sources, with map
    previews, and up to six **custom sources** from an Earth Engine image or
    image collection or a GeoTIFF in the user's SEPAL folder, read through
    one of three format presets (RADD, SEPAL CCDC alerts, date-only) over one
    band mapping. Custom sources are downloaded in their native projection
    (a collection that mixes CRSs included), can be edited and deleted, and
    are refused while a job writes into the project.
  - **Coverage** per source and AOI, from one Earth Engine request: chips,
    first and last dates and reasons in Alerts; rows that are not covered, or
    have no data for the period, are explained in Download & harmonize, are
    skipped by Run all and are left out of Integrate with their reason.
  - Optional layers: **visualization layers** (an Earth Engine image or a
    GeoTIFF), shown on the shared map with their legend and, when marked,
    on both Analysis maps.
    - **Visualization styles**: one band is *Continuous* (a colour ramp
      between a minimum and a maximum) or *Categorical* (integer classes
      found over the AOI, at most 50, each with its colour and label and a
      hide toggle; values not listed and hidden classes are transparent);
      an image of three bands or more can be drawn as *RGB* from a standard
      combination (true colour, false-colour infrared, SWIR / NIR / red,
      from the band roles detected) or from bands picked by hand. **Stretch
      to the AOI** fills the minimum and maximum from the 2–98 % percentiles over
      the AOI's bounds (for RGB, one range across the three bands). Layers
      saved before this version keep their exact look.
    - **The layer form** reads Name, Source, the image, then its bands, and
      every choice is a dropdown. A **bands helper** asks only what the
      image's band count allows: one band is drawn on its own, two are picked
      from, three or more are drawn as one band or as RGB (suggested when red,
      green and blue are recognized from the band names); the Band select
      starts on the first band. One band takes a **Raster type** (Continuous
      or Categorical, "suggested" when its name looks like classes). RGB
      takes a **Composite** that names each combination's bands; without
      recognized names it starts on the first three bands, and bands picked
      by hand that match a combination select it. A band of only 0 and 1
      found in Categorical names 1 after the band and starts 0 hidden.
  - Period: **several monitoring periods** (typically one a month, dates
    included at both ends), each with its own alert sets, listed with their
    window, sets and out-of-date badges; new, edit and delete follow the
    period rules (dates freeze once a period has sets; only a period without
    sets, or the latest one, can be deleted). A project-wide **look-back**
    widens every period's search to `[start - look-back, end]`, and pixels
    already reported by an earlier period's sets are removed before
    vectorizing, so a late alert lands in the next set and is never reported
    twice.
  - Download & harmonize: one projected pixel grid per project (EPSG with an
    auto-UTM default, and a resolution; a geographic CRS is refused). Each
    source is downloaded from Earth Engine on its own and harmonized locally
    onto that grid as a tiled Int32 GeoTIFF (alert class, and the date as
    `yyyyddd`), with Run, Re-download, Stop and a map toggle per row.
    Fingerprints of every input mean a reopened project downloads or
    harmonizes again only what is out of date; a file deleted from disk shows
    as missing and can be run again. GeoTIFF sources are clipped to the AOI;
    the rows show results (alert pixels) and sizes.
  - **Masks** in Download & harmonize: defined once per project (an Earth
    Engine image or collection, or a GeoTIFF, and one band; a pixel is on
    where it is non-zero) and chosen per period, each kept or excluded. A new
    period copies the previous period's choice, and a mask switch makes only
    that period's sources and sets out of date. Run all aligns the period's
    masks first, then re-harmonizes without downloading the sources again.
  - Integrate & vectorize: combines the harmonized sources locally, one
    decimal digit per source, vectorizes block by block, applies the minimum
    size, the drawn area, the sorting, the maximum count and the optional
    attribute raster (from an Earth Engine image or a GeoTIFF, each band at
    its own projection), and records an **alert set** (alert store, combined
    raster and footprint). Re-running a set that has reviewed alerts asks
    first and lists them. Analysis reviews the active set.
  - One **set selector**, newest first ("period · sources · N alerts · M
    reviewed"), in Overview and Analysis.
  - Overview shows the active set as PMTiles (tippecanoe), with alert
    centroids as the fallback, and can show earlier sets lightened with their
    legends; an alert's popup has a Review button that opens it in Analysis,
    in whichever shown set it belongs to.
- **Legends** for every source, mask, visualization layer and Overview set
  drawn on the shared map: a legend shows while its layer is on, and all
  legends are hidden while the Analysis screen shows.
- **Reports from a template**: the project's report template (a `.docx` in
  the user's files) is picked in the export card; a template gone from disk
  refuses the download with a message.
- **Confirm dialogs** (spatial_risk's `ConfirmDialog`) before a destructive
  action, listing what it removes: a set replace lists the reviewed alerts,
  a period delete lists its sets.
- **Translations**: the app reads in English, Spanish (es-ES) and French
  (fr), switched from the language selector and applied in place, except
  that an open Analysis screen keeps the language it was opened in until the
  app is reloaded. Strings
  come from one catalogue (`deforestation_alerts/message/<locale>/*.json`)
  through pysepal's `catalog()`, with plural forms for counts; engine
  errors carry message keys, so their toasts are in the user's language.
- The Project dialog's Open list can be searched by name, and the open
  project reads "saved X ago", following every save.
- An AOI change, a source or mask delete, a period delete or replacement
  delete the files of every artifact they drop, through one helper.
- `run_ui.sh`, which serves `ui.ipynb` with Voila inside a Jupyter server as
  SEPAL does, with map tiles through `/proxy/{port}`; `localtileserver` and
  `vectortileserver` for raster and vector tiles; GDAL display guards.
- `scripts/measure_prepare.py`, which measures one national-scale download,
  harmonize and integrate run (time, file sizes, peak memory).
- **Growth alerts**: Integrate looks back at the alerts of earlier periods
  that were reviewed as Confirmed or Need revision (the reviewer's drawn shape
  where there is one, else the detected cluster). A new cluster that touches
  one is linked to it; a cluster under the minimum area that touches one is
  kept as a growth alert instead of being dropped. Growth alerts count toward
  the alert limit and are reviewed like any other alert.
  - Analysis: the facts show the alert's Kind (New, Growth, under minimum
    area) and its History (alerts, confirmed hectares and the first month).
    The "Earlier alerts" section lists the earlier alerts nearby, grouped and
    coloured by period (solid = confirmed, dashed = need revision), with a
    checkbox to link or unlink each one, saved with the review; the map
    shows them under the current alert and the timeline marks their dates.
    A Kind filter finds growth alerts.
  - A link breaks when the earlier set is re-run (the Replace dialog warns
    first) and greys out when its parent is later reviewed as a false
    positive; neither counts in histories.
  - Review copies carry a snapshot of the earlier alerts, so evaluators link
    as in the main project, and links merge back with their reviews.
  - Overview: a History card with the set's growth alerts and its largest
    growing clearings, each with a Review button. Exports add `kind`,
    `grows_from` and `history_area_ha`.

### Fixed

- **The Analysis screen follows the language selector.** It was built once in
  English and only its title and back button changed language, so a reviewer
  whose browser or saved choice was Spanish, French or Portuguese saw most of
  Analysis in English. Every label, button, tooltip, list and badge on the
  screen now changes with the selector, without losing the alert, the
  answers, an open drawing or a running save.

### Changed

- **Download links follow the SEPAL the app runs on.** They were built by
  pysepal's `create_download_link`, which hardcodes `https://sepal.io/...`, so
  a test.sepal.io user was handed a production link and a local run had no
  working link at all. The URL now comes from two environment variables —
  `DEFORESTATION_ALERTS_DOWNLOAD_BASE` (default `/api/sandbox/jupyter/files`,
  root-relative, so the browser resolves it against whichever host serves the
  app) and `DEFORESTATION_ALERTS_DOWNLOAD_ROOT` (default `$HOME`). `run_ui.sh`
  points both at its own jupyter for local runs. Link segments are now
  percent-escaped, so a project name with a space downloads.
- **One alert per cluster.** Integrate & vectorize makes one alert of each
  connected cluster of alert pixels, whatever sources flagged them: a cluster
  where GLAD-L and RADD touch is one alert listing both sources, reviewed as
  one, as in the previous version of the module. It used to split each cluster
  into one alert per source combination (a 25-pixel GLAD-L alert beside a
  1-pixel RADD one). The minimum alert size keeps or drops whole clusters.
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
  notification system in place of ad hoc inline widgets; and added browser-side
  `jslink` sync between the Analysis workspace's paired maps.
- User files (projects, alert stores, exports, reports) are read and written
  directly on the sandbox disk (`deforestation_alerts/scripts/local_files.py`, with `.part` files
  renamed into place). The `Workspace` adapter, which went through the SEPAL files
  API for the old app-launcher container, and its scratch registry are gone.

### Removed

- **Recipes**: the Recipes dialog and the recipe save and load. Old recipes are not
  converted; start a project instead.
- The **Optional files** dialog. Its inputs moved into the workflow: custom alert
  sources (CCDC is now one of their format presets) into Alerts, masks into
  Download & harmonize, NICFI and other visualization layers into Optional layers,
  and the attribute raster into Integrate & vectorize. Only CCDC as a dedicated
  source, the reference layer and the Planet API login are not available in this
  release (NICFI is read through Earth Engine).
- The **Filter** step and the old **AOI/Date** step with its source search: the AOI,
  Alerts, Period, Download & harmonize and Integrate & vectorize steps replace them.
- The Docker deployment for SEPAL's app launcher: `gui/Dockerfile`,
  `gui/supervisord.conf`, `docker-compose.yml` and `.dockerignore`. The app runs as
  Voila in the user's sandbox (`ui.ipynb`, `run_ui.sh`).
- The `deforestation_alerts.parameter` package. Its container-local `module_dir` gave
  way to the data directory on the sandbox disk, and the GDAL temp-directory
  configuration that rode along on its import now runs explicitly in
  `gui/solara_app.py`.
- The legacy `cm` translator and the translation notebook, replaced by the catalogue
  above; the `deforestation_alerts/model/analysis_model.py` traitlets model (Analysis state is Solara
  reactives).
- Code nothing used any more: the `alert_repository` service named under Changed
  above, the `ArtifactNameField` widget with its `artifact_names` helpers, and
  unused helpers in `mosaics_helper`, `imagery_service` and `report_builder`.

## v_1.0 (2025-03-25)

### Feature

- Initial release