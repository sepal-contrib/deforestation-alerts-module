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
6. **Integrate & vectorize**: the sources are combined and turned into an **alert set**. Each connected cluster of alert pixels becomes one alert, which lists every source that flagged it. Options: probable (unconfirmed) alerts, a minimum size, the review order, a maximum count and attribute rasters.
7. **Questionnaire**: the questions a reviewer answers for each alert (the loss driver by default), and the report template.
8. **Evaluation**: review alone, or split the set among several evaluators. Each evaluator gets a review package, and their reviewed copies are merged back into the set.
9. **Analysis**: a screen that steps through the alerts. For each alert the reviewer records a verdict and answers the questions, can refine the polygon with drawing tools and two CNN models, and can export the files.

**Overview** maps the active set and earlier sets, with their status and a breakdown per question. The app is available in English, Spanish, French and Portuguese.
