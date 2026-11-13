Alert analysis module is a SEPAL app for monitoring forest cover loss alerts in an area of interest, period after period.

It downloads alerts — GLAD-L and GLAD-S2 (Global Land Analysis & Discovery) and RADD (RAdar for Detecting Deforestation) from Google Earth Engine, and up to six custom sources from an Earth Engine asset, a GeoTIFF or a file of dated points or polygons in your SEPAL folder — harmonizes them onto one grid on your own disk and groups touching alert pixels into alerts, each listing every source that flagged it.

Each alert can then be reviewed on before/after optical and radar imagery, refined with drawing tools and two convolutional neural network (CNN) models, and answered against your own questions, alone or split among several evaluators. The Overview sums up every period, and the results export as GeoPackage files, CSV tables, Word reports from your template and GIF animations.

Your work is kept in projects, saved automatically under `module_results/deforestation_alerts`. Recipes saved with the previous version of the app open as projects too, so their reviews can be finished and exported.
