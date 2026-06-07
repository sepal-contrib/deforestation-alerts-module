"""Tier-2: real EE download -> local vectorize. Skips without credentials."""

import numpy as np
import rasterio

from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.vectorize import vectorize_alert_raster
from deforestation_alerts.scripts.alert_filter_helper import convert_to_geopandas


def test_alert_code_integers_survive_geotiff_export(tiny_aoi, tmp_path):
    """A constant integer alert band must export + read back bit-exact (§6.5)."""
    import ee
    alert = ee.Image.constant(110).toInt().rename("alert")   # RADD+GLAD-S2 code
    date = ee.Image.constant(2023.0).rename("date")
    img = alert.addBands(date)

    out = str(tmp_path / "codes.tif")
    download_ee_image(img, out, region=tiny_aoi, scale=30,
                      crs="EPSG:4326", unmask_value=255, nodata_value=255)

    with rasterio.open(out) as src:
        band = src.read(1)
    present = set(np.unique(band).tolist()) - {255}
    assert present == {110}, f"alert code corrupted on export: {present}"


def test_download_then_vectorize_produces_expected_columns(tiny_aoi, tmp_path):
    import ee
    alert = ee.Image.constant(1).toInt().rename("alert")
    date = ee.Image.constant(2023.123).rename("date")
    out = str(tmp_path / "alerts.tif")
    download_ee_image(alert.addBands(date), out, region=tiny_aoi, scale=30,
                      crs="EPSG:4326", unmask_value=255, nodata_value=255)

    vgdf = vectorize_alert_raster(out, min_cluster_size=1, connectivity=8)
    assert len(vgdf) >= 1
    gdf = convert_to_geopandas(vgdf)
    for col in ("geometry", "bounding_box", "point", "alert_sources",
                "area_ha", "status"):
        assert col in gdf.columns
    assert gdf.geometry.iloc[0].geom_type == "Polygon"
    assert "GLAD-L" in gdf["alert_sources"].iloc[0]
