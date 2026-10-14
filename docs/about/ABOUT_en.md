Forest Cover Loss Alerts is a SEPAL app for monitoring forest cover loss alerts in an area of interest, period after period.

It downloads alerts from Google Earth Engine — GLAD-L and GLAD-S2 (Global Land Analysis & Discovery), RADD (RAdar for Detecting Deforestation) and up to six custom sources, such as SEPAL CCDC alerts — harmonizes them onto one grid on your own disk and groups touching alert pixels into alerts, each listing every source that flagged it.

Each alert can then be reviewed on before/after satellite imagery, refined with drawing tools and two convolutional neural network (CNN) models, and answered against your own questions, alone or split among several evaluators. The results export as GeoPackage files, Word reports from your template and GIF animations.

Your work is kept in projects, saved automatically under `module_results/deforestation_alerts`.
