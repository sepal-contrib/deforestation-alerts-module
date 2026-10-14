Alertes de perte de couverture forestière est une application SEPAL pour suivre les alertes de perte de couverture forestière sur une zone d'intérêt, période après période.

Elle télécharge les alertes depuis Google Earth Engine — GLAD-L et GLAD-S2 (Global Land Analysis & Discovery), RADD (RAdar for Detecting Deforestation) et jusqu'à six sources personnalisées, comme les alertes CCDC de SEPAL —, les harmonise sur une même grille sur votre propre disque et regroupe les pixels d'alerte qui se touchent en alertes, chacune listant toutes les sources qui l'ont détectée.

Chaque alerte peut ensuite être examinée sur des images satellite avant/après, affinée avec des outils de dessin et deux modèles de réseaux de neurones convolutifs (CNN), et renseignée selon vos propres questions, seul ou répartie entre plusieurs évaluateurs. Les résultats s'exportent en fichiers GeoPackage, en rapports Word à partir de votre modèle et en animations GIF.

Votre travail est conservé dans des projets, enregistrés automatiquement dans `module_results/deforestation_alerts`.
