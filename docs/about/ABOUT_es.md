Módulo de análisis de alertas es una aplicación de SEPAL para monitorear las alertas de pérdida de cobertura forestal en un área de interés, período tras período.

Descarga alertas — GLAD-L y GLAD-S2 (Global Land Analysis & Discovery) y RADD (Radar para Detectar Deforestación) desde Google Earth Engine, y hasta seis fuentes personalizadas desde un asset de Earth Engine, un GeoTIFF o un archivo de puntos o polígonos con fecha de su carpeta de SEPAL —, las armoniza en una misma cuadrícula en su propio disco y agrupa los píxeles de alerta que se tocan en alertas, cada una con todas las fuentes que la detectaron.

Luego cada alerta puede revisarse sobre imágenes ópticas y de radar de antes y después, ajustarse con herramientas de dibujo y dos modelos de redes neuronales convolucionales (CNN), y responderse según sus propias preguntas, en solitario o repartida entre varios evaluadores. El Resumen sintetiza todos los períodos, y los resultados se exportan como archivos GeoPackage, tablas CSV, informes de Word a partir de su plantilla y animaciones GIF.

Su trabajo se guarda en proyectos, de forma automática, en `module_results/deforestation_alerts`. Las recetas guardadas con la versión anterior de la aplicación también se abren como proyectos, para terminar y exportar sus revisiones.
