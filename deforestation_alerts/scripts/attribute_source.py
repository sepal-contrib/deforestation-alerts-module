"""Resolve an attribution-raster config to a local GeoTIFF. GEE-aware (unlike the
pure raster_attribute module): a GEE asset is downloaded once to the recipe folder
and stamped with its band names; a local file is used as-is. Also enumerates GEE
band names for the Aux-tile config UI, and ties config -> materialize -> attribute."""

from pathlib import Path

import ee
import rasterio

from deforestation_alerts.parameter import module_dir
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


def materialize_attribute_raster(config, region, recipe_folder):
    """Return a local GeoTIFF path for the attribution raster.

    local -> config["source"] unchanged. gee -> download ee.Image(source) clipped
    to `region` at its native scale into `recipe_folder` (or module_dir), stamping
    band descriptions. `region` may be an ee.Geometry or ee.FeatureCollection (a FeatureCollection is reduced to its geometry before download).
    """
    if config["kind"] == "local":
        return config["source"]

    asset_id = config["source"]
    img = ee.Image(asset_id)
    names = gee_band_names(asset_id)
    # geedim's prepareForExport (called inside download_ee_image) expects an
    # ee.Geometry, so unwrap a FeatureCollection's geometry up front.
    region_geom = (
        region.geometry() if isinstance(region, ee.FeatureCollection) else region
    )
    base = Path(recipe_folder) if recipe_folder else module_dir
    out_tif = str(base / f"attribute_{safe_token(asset_id)}.tif")
    # Native scale keeps categorical values intact; near resampling avoids blending.
    native_scale = img.projection().nominalScale().getInfo()
    download_ee_image(
        img, out_tif, region=region_geom, crs="EPSG:4326",
        scale=native_scale, resampling="near",
    )
    _set_band_descriptions(out_tif, names)
    return out_tif


def attribute_alerts_from_config(gdf, config, region, recipe_folder,
                                 materialize=materialize_attribute_raster):
    """No-op when `config` has no bands; else materialize the raster and attribute.

    Returns `gdf` unchanged (same object) when there is nothing to do, so callers
    can assign unconditionally. Raises (fail loud) if attribution is configured but
    the raster can't be materialized/read or a band is missing.
    """
    if not config or not config.get("bands"):
        return gdf
    tif = materialize(config, region, recipe_folder)
    return attribute_alerts(gdf, tif, config["bands"])
