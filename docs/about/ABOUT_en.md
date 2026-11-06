Forest Cover Loss Alerts is a SEPAL app for monitoring forest cover loss alerts in an area of interest, period after period.

It downloads alerts — GLAD-L and GLAD-S2 (Global Land Analysis & Discovery) and RADD (RAdar for Detecting Deforestation) from Google Earth Engine, and up to six custom sources from an Earth Engine asset or a GeoTIFF in your SEPAL folder, such as SEPAL CCDC alerts — harmonizes them onto one grid on your own disk and groups touching alert pixels into alerts, each listing every source that flagged it.

Each alert can then be reviewed on before/after satellite imagery, refined with drawing tools and two convolutional neural network (CNN) models, and answered against your own questions, alone or split among several evaluators. The results export as GeoPackage files, CSV tables, Word reports from your template and GIF animations.

Your work is kept in projects, saved automatically under `module_results/deforestation_alerts`.
