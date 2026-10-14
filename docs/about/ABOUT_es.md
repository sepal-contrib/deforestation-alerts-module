Alertas de pérdida de cobertura forestal es una aplicación de SEPAL para monitorear las alertas de pérdida de cobertura forestal en un área de interés, período tras período.

Descarga alertas desde Google Earth Engine — GLAD-L y GLAD-S2 (Global Land Analysis & Discovery), RADD (Radar para Detectar Deforestación) y hasta seis fuentes personalizadas, como las alertas CCDC de SEPAL —, las armoniza en una misma cuadrícula en su propio disco y agrupa los píxeles de alerta que se tocan en alertas, cada una con todas las fuentes que la detectaron.

Luego cada alerta puede revisarse sobre imágenes satelitales de antes y después, ajustarse con herramientas de dibujo y dos modelos de redes neuronales convolucionales (CNN), y responderse según sus propias preguntas, en solitario o repartida entre varios evaluadores. Los resultados se exportan como archivos GeoPackage, informes de Word a partir de su plantilla y animaciones GIF.

Su trabajo se guarda en proyectos, de forma automática, en `module_results/deforestation_alerts`.
