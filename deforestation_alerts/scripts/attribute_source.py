"""Resolve an attribution-raster config to a local GeoTIFF. GEE-aware (unlike the
pure raster_attribute module): a GEE asset is downloaded once and stamped with its
band names; a user-picked raster is staged down from their SEPAL workspace. Also
enumerates GEE band names for the Aux-tile config UI, and ties
config -> materialize -> attribute.

Both artifacts are container-local **scratch** (inventory rows a31/a32). The GEE
download used to land in the recipe folder, but nothing ever re-read it -- every
call downloads afresh -- so it was write-only user clutter; it is now built in
``workspace.scratch_dir()`` and never uploaded. The ``kind == "local"`` source is
a path the user picked in the Aux tile's remote ``FileInput``, i.e. a REMOTE
workspace path, so it is staged down before rasterio opens it.
"""

import ee
import rasterio

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


def materialize_attribute_raster(config, region, workspace):
    """Return a LOCAL GeoTIFF path for the attribution raster.

    local -> `config["source"]` is a remote workspace path, staged down. gee ->
    download ee.Image(source), bounded by `region`, at its native scale into
    `workspace` scratch, stamping band descriptions. `region` may be an
    ee.Geometry or ee.FeatureCollection, passed through as-is to
    `download_ee_image` (see its docstring for how the region is used).

    The caller owns the returned path and should ``workspace.discard_scratch`` it
    once the attribution pass has read it.
    """
    if config["kind"] == "local":
        return str(workspace.download_to_scratch(config["source"]))

    asset_id = config["source"]
    img = ee.Image(asset_id)
    names = gee_band_names(asset_id)
    out_tif = str(
        workspace.scratch_file(name=f"attribute_{safe_token(asset_id)}.tif")
    )
    # Native scale keeps categorical values intact; near resampling avoids blending.
    native_scale = img.projection().nominalScale().getInfo()
    download_ee_image(
        img, out_tif, region=region, crs="EPSG:4326",
        scale=native_scale, resampling="near",
    )
    _set_band_descriptions(out_tif, names)
    return out_tif


def attribute_alerts_from_config(gdf, config, region, workspace,
                                 materialize=materialize_attribute_raster):
    """No-op when `config` has no bands; else materialize the raster and attribute.

    Returns `gdf` unchanged (same object) when there is nothing to do, so callers
    can assign unconditionally. Raises (fail loud) if attribution is configured but
    the raster can't be materialized/read or a band is missing. The staged raster
    is released either way.
    """
    if not config or not config.get("bands"):
        return gdf
    tif = materialize(config, region, workspace)
    try:
        return attribute_alerts(gdf, tif, config["bands"])
    finally:
        workspace.discard_scratch(tif)
