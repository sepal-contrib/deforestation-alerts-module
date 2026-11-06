"""Resolve an attribute-raster config to local GeoTIFFs: a GeoTIFF is read
where it is; an Earth Engine image is downloaded, one included band at a time,
into a private scratch folder for the run. Also estimates the size of that
download for Integrate's free-space gate, and ties config -> materialize ->
attribute.
"""

from pathlib import Path

import ee
import rasterio

from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.naming import safe_token
from deforestation_alerts.scripts.raster_export import download_ee_image
from deforestation_alerts.scripts.raster_attribute import ALERT_RESERVED_COLUMNS, attribute_alerts, resolve_column_names


def _set_band_descriptions(tif_path, names):
    """Write band names as GeoTIFF descriptions so downstream name->index
    resolution works identically for downloaded GEE rasters and local files."""
    with rasterio.open(tif_path, "r+") as dst:
        dst.descriptions = tuple(names)


def materialize_attribute_bands(config, region, folder):
    """``[(local GeoTIFF, [band specs])]`` for the attribute raster.

    A GeoTIFF is read where it is, all its included bands from that one file.
    An Earth Engine image is downloaded one included band at a time, each in
    its own native projection (no ``crs``/``scale``: geedim uses the selected
    band's), so an image whose bands mix projections (Sentinel-2, Landsat TOA)
    works and no band that is not included is fetched (spec §7.4). A band with
    several statistics is downloaded once, with all its specs.
    """
    if config["kind"] == "local":
        return [(str(config["source"]), list(config["bands"]))]
    out = []
    for i, (band, specs) in enumerate(_by_band(config["bands"]).items()):
        tif = str(Path(folder) / f"attribute_{i}_{safe_token(band)}.tif")
        download_ee_image(ee.Image(config["source"]).select([band]), tif, region=region, resampling="near")
        _set_band_descriptions(tif, [band])
        out.append((tif, specs))
    return out


def _by_band(specs) -> dict:
    """``{band: [its specs]}``, in the order the bands first appear."""
    out = {}
    for spec in specs or []:
        out.setdefault(spec["band"], []).append(spec)
    return out


def attribute_alerts_from_config(gdf, config, region, materialize=None):
    """No-op without bands; else attribute every included band (fail loud).

    The column names are resolved again here: a layer saved before a column
    was reserved (``alert_id``, ``set_id``) would otherwise overwrite it."""
    if not config or not config.get("bands"):
        return gdf
    config = {**config, "bands": resolve_column_names(config["bands"], reserved=ALERT_RESERVED_COLUMNS)}
    materialize = materialize or materialize_attribute_bands
    with local_files.scratch_dir() as folder:
        for tif, specs in materialize(config, region, folder):
            gdf = attribute_alerts(gdf, tif, specs)
    return gdf


def download_estimate(config, aoi_bounds) -> int:
    """Uncompressed bytes an Earth Engine attribute raster downloads (one band
    per distinct included band, 4 bytes a pixel at the band's own scale)."""
    from deforestation_alerts.engine.download import estimate_native_bytes

    if not config or config.get("kind") != "gee":
        return 0
    total = 0
    for spec in (specs[0] for specs in _by_band(config.get("bands")).values()):
        native = (spec.get("crs") or "EPSG:4326", float(spec.get("scale") or 10.0))
        total += estimate_native_bytes(aoi_bounds, native) // 2  # one band, not two
    return total
