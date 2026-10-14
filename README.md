# Forest Cover Loss Alerts

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/sepal-contrib/deforestation-alerts-module/blob/main/LICENSE)

A [SEPAL](https://sepal.io) app for monitoring forest cover loss alerts. It downloads alerts from Google Earth Engine (GLAD-L, RADD, GLAD-S2, and up to six custom sources), combines them on one grid and turns them into polygons on the user's own disk. A team can then review every alert on a before/after imagery screen, answer questions about each one, and export the results as GeoPackage files, Word reports and GIFs.

The app is built with [pysepal](https://github.com/sepal-contrib/pysepal) and [Solara](https://solara.dev).

## How it works

Work happens in a **project**: a folder under `~/module_results/deforestation_alerts/<project>/` that is saved automatically. The Workflow panel takes a project through these steps, and each step unlocks when the step before it is done:

1. **AOI**: the area of interest (an administrative area, a drawing, a vector file or an Earth Engine asset).
2. **Alerts**: the predefined GLAD-L, RADD and GLAD-S2 sources, plus custom sources from an Earth Engine image or collection or from a GeoTIFF. Each source shows its coverage of the AOI.
3. **Optional layers**: visualization layers (Earth Engine, GeoTIFF or Planet NICFI) for the map and the review screen.
4. **Period**: one or more monitoring periods, with a look-back window. An alert reported in one period is never reported again in a later one.
5. **Download & harmonize**: each source is downloaded and aligned onto one projected grid for the project, with optional masks.
6. **Integrate & vectorize**: the sources are combined and turned into an **alert set**. Each connected cluster of alert pixels becomes one alert, which lists every source that flagged it. Optional filters: minimum size, a drawn area, sorting, a maximum count and an attribute raster.
7. **Questionnaire**: the questions a reviewer answers for each alert (the loss driver by default), and the report template.
8. **Evaluation**: review alone, or split the set among several evaluators. Each evaluator gets a review package, and their reviewed copies are merged back into the set.
9. **Analysis**: a screen that steps through the alerts. For each alert the reviewer records a verdict and answers the questions, can refine the polygon with drawing tools and two CNN models, and can export the files.

**Overview** maps the active set and earlier sets, with their status and a breakdown per question. The app is available in English, Spanish and French.

## Running the app

### On SEPAL

Open **Forest Cover Loss Alerts** from the SEPAL apps page. That page serves the `release` branch.

To run another branch, clone it in a sandbox terminal. `docs/sepal-gate.md` has the full runbook. In short:

```bash
git clone -b main https://github.com/sepal-contrib/deforestation-alerts-module
cd deforestation-alerts-module
micromamba create -y -n defo-alerts -f sepal_environment.yml   # once
micromamba activate defo-alerts
./run_ui.sh --port 8911
```

### Locally

`sepal_environment.yml` (micromamba or conda) is the only definition of the runtime environment. `pyproject.toml` declares no runtime dependencies. You also need Earth Engine credentials (`earthengine authenticate`).

There are two ways to start the app:

- `./run_ui.sh [--port 8911]`: the SEPAL launch path. It runs Voila inside a Jupyter server, so `ui.ipynb` and the map tiles go through `/proxy/{port}`. Open `http://127.0.0.1:8911/voila/render/ui.ipynb`.
- `nox -s app`, or `solara run gui/solara_app.py --port 8900 --no-open`: plain Solara, which is quicker for UI work.

`run_ui.sh` reads a `.env` file in the repository root, if there is one. These variables configure the app:

| Variable | Default | What it sets |
| --- | --- | --- |
| `DEFORESTATION_ALERTS_DATA_DIR` | `~/module_results/deforestation_alerts` | Where projects live. |
| `DEFORESTATION_ALERTS_DOWNLOAD_BASE` | `/api/sandbox/jupyter/files` | The URL that download links start with. The default is root-relative, so it works on sepal.io and test.sepal.io unchanged. |
| `DEFORESTATION_ALERTS_DOWNLOAD_ROOT` | `$HOME` | The directory that the download base serves. A file outside it cannot be downloaded, and the export task fails rather than offering a link that doesn't work. |
| `DEFORESTATION_ALERTS_GDAL_TMPDIR` | a per-user directory under `/tmp` | GDAL's scratch directory. On SEPAL the module folder is read-only. |
| `DEFORESTATION_ALERTS_LOCAL_SCRATCH`, `DEFORESTATION_ALERTS_NUM_THREADS` | (see `deforestation_alerts/gdal_env.py`) | Local scratch space and thread count for raster work. |

`run_ui.sh` points both download variables at its own Jupyter server, which serves the checkout at `/files`. For downloads to work locally, keep the project data inside the checkout, for example `DEFORESTATION_ALERTS_DATA_DIR="$PWD/.local_data"`.

## Repository layout

| Path | What it holds |
| --- | --- |
| `deforestation_alerts/` | The library, with no UI code. `engine/` downloads, harmonizes, integrates and vectorizes. `project/` holds the project model, paths, sharing and splitting. `scripts/` holds Earth Engine requests, stores and reports. `message/` holds the translations. |
| `gui/` | The Solara app. `solara_app.py` is the entry point. `tile/` holds the workflow steps and the Analysis screen. `widget/` holds the components. `scripts/` holds UI services, jobs and map layers. `store/` holds the session state. |
| `js/` | `leaflet-multiview`, the synced before/after map package, and `app/`, which builds it into `gui/widget/static/multiview.{js,css}`. |
| `tests/` | The pytest suite. |
| `tools/` | Browser walks (`tools/walk/`) and translation tools (`tools/i18n/`). |
| `docs/` | The in-app About pages (`docs/about/`) and the SEPAL gate runbook. |
| `ui.ipynb`, `run_ui.sh` | The Voila entry point that SEPAL launches. |

## Development

### Tests

Run the tests from the repository root, inside the runtime environment:

```bash
python -m pytest -q                         # offline tier: no Earth Engine, no network
DEFO_ALERTS_EE_TESTS=1 python -m pytest -q  # everything, including the live Earth Engine tier
python -m pytest -q -m ee                   # the live Earth Engine tier only
```

The live Earth Engine tests are opt-in:

- Every test that uses the `gee` fixture is marked `ee` and skipped unless `DEFO_ALERTS_EE_TESTS=1` is set or `-m ee` selects it.
- They need Earth Engine credentials, and they only read from Earth Engine.
- A test that is not marked `ee` but still calls Earth Engine fails.

Two groups of tests also skip unless their tools are installed:

- **Browser tests** (`tests/test_multiview_*_browser.py`) need Playwright: `pip install playwright && playwright install chromium`.
- **The bundle check** (`tests/test_bundle_drift.py`) needs Node and `npm ci` in `js/app`.

### Lint

[ruff](https://docs.astral.sh/ruff/) checks for errors such as unused or undefined names. It is not used as a formatter.

```bash
nox -s lint            # or: pre-commit run --all-files
```

### The JavaScript bundle

The built bundle `gui/widget/static/multiview.{js,css}` is committed. After changing anything under `js/`, rebuild it and commit the result:

```bash
cd js/app && npm ci && npm run build          # rebuilds the bundle
cd js/leaflet-multiview && npm ci && npm test # the package's own tests
```

`tests/test_bundle_drift.py` fails if the committed bundle doesn't match a fresh build. Never hand-merge the bundle: rebuild it.

### Browser walks

`tools/walk/` drives the app in a real browser for end-to-end checks, for example:

```bash
python -m tools.walk.slice6_walk --port 8986 --out /tmp/slice6_walk
```

The walks need Playwright.

## License

[MIT](LICENSE) © FAO
