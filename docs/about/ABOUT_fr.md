Module d'analyse des alertes est une application SEPAL pour suivre les alertes de perte de couverture forestière sur une zone d'intérêt, période après période.

Elle télécharge les alertes — GLAD-L et GLAD-S2 (Global Land Analysis & Discovery) et RADD (RAdar for Detecting Deforestation) depuis Google Earth Engine, et jusqu'à six sources personnalisées depuis un asset Earth Engine, un GeoTIFF ou un fichier de points ou de polygones datés de votre dossier SEPAL —, les harmonise sur une même grille sur votre propre disque et regroupe les pixels d'alerte qui se touchent en alertes, chacune listant toutes les sources qui l'ont détectée.

Chaque alerte peut ensuite être examinée sur des images optiques et radar avant/après, affinée avec des outils de dessin et deux modèles de réseaux de neurones convolutifs (CNN), et renseignée selon vos propres questions, seul ou répartie entre plusieurs évaluateurs. L'Aperçu résume toutes les périodes, et les résultats s'exportent en fichiers GeoPackage, en tableaux CSV, en rapports Word à partir de votre modèle et en animations GIF.

Votre travail est conservé dans des projets, enregistrés automatiquement dans `module_results/deforestation_alerts`. Les recettes enregistrées avec la version précédente de l'application s'ouvrent aussi comme des projets, pour terminer et exporter leurs revues.
