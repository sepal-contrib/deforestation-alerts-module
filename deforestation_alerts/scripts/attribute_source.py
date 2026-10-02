"""Resolve an attribute-raster config to a local GeoTIFF: a GeoTIFF is read
where it is; an Earth Engine image is downloaded into a private scratch folder
for the run. Also enumerates GEE band names for the Aux-tile config UI, and
ties config -> materialize -> attribute.
"""

from pathlib import Path

import ee
import rasterio

from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.naming import safe_token
from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.raster_attribute import attribute_alerts


def gee_band_names(asset_id):
    """Band names of a GEE image asset (drives the Aux config UI)."""
    return ee.Image(asset_id).bandNames().getInfo()


def _set_band_descriptions(tif_path, names):
    """Write band names as GeoTIFF descriptions so downstream name->index
    resolution works identically for downloaded GEE rasters and local files."""
    with rasterio.open(tif_path, "r+") as dst:
        dst.descriptions = tuple(names)


def materialize_attribute_raster(config, region, folder):
    """A local GeoTIFF path for the attribute raster: the user's own file for
    ``local``; for ``gee`` a download into ``folder`` at the image's native
    scale with band names stamped as descriptions."""
    if config["kind"] == "local":
        return str(config["source"])
    asset_id = config["source"]
    img = ee.Image(asset_id)
    names = gee_band_names(asset_id)
    out_tif = str(Path(folder) / f"attribute_{safe_token(asset_id)}.tif")
    native_scale = img.projection().nominalScale().getInfo()
    download_ee_image(img, out_tif, region=region, crs="EPSG:4326", scale=native_scale, resampling="near")
    _set_band_descriptions(out_tif, names)
    return out_tif


def attribute_alerts_from_config(gdf, config, region, materialize=materialize_attribute_raster):
    """No-op without bands; else materialize the raster and attribute (fail loud)."""
    if not config or not config.get("bands"):
        return gdf
    with local_files.scratch_dir() as folder:
        tif = materialize(config, region, folder)
        return attribute_alerts(gdf, tif, config["bands"])
