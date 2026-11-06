"""Mask rasters and the AOI raster on the project grid (spec §7.2).

The AOI raster: the AOI polygon rasterized onto the grid, Int32 0/1, used to
clip GeoTIFF sources at harmonize (and GeoTIFF masks at alignment), as Earth
Engine's download clips an Earth Engine raster. Built window by window; never
a whole-grid rasterize.

Masks: an Earth Engine mask is reduced to its band and ``neq(0)`` and
downloaded in its native projection as uint8 0/1 (no-data -> 0; a quarter of
the geedim tiles an Int32 download takes). Alignment reads one chosen band of
the raw download or of a GeoTIFF, decides each source pixel before any cast
(``mask_on_pixels``: a float32 0.7 is on, NaN is off, -5 is on), and writes
Int32 0/1 on the grid with nearest overviews, its on-pixels counted.
"""

from pathlib import Path

import ee
import geopandas as gpd
import numpy as np
import rasterio

from deforestation_alerts.engine.align import reproject_to_grid
from deforestation_alerts.engine.download import download_checked, estimate_native_bytes
from deforestation_alerts.engine.raster_io import (
    build_nearest_overviews,
    check_header,
    part_file,
    rasterize_footprint,
    tile_windows,
)
from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.models import GridSpec
from deforestation_alerts.scripts.raster_export import export_bounds


class MaskRasterError(KeyedError, RuntimeError):
    """A raw mask download is not the 1-band uint8 raster in the native CRS that was asked for."""

    key = "errors.mask_not_as_asked"


def build_aoi_raster(sidecar_path, grid: GridSpec, out_path) -> Path:
    return rasterize_footprint(gpd.read_file(sidecar_path).to_crs(grid.epsg), grid, out_path)


def mask_on_pixels(values, nodata) -> np.ndarray:
    """The mask's pixel rule (spec §7.2): on where the value is non-zero
    (negative included); zero, NaN and the file's no-data are off."""
    v = np.asarray(values, dtype=np.float64)
    on = np.isfinite(v) & (v != 0)
    if nodata is not None and not np.isnan(nodata):
        on &= v != nodata
    return on.astype(np.int32)


def build_mask_image(mask, aoi) -> ee.Image:
    """``mask``'s band over ``aoi`` as one band, ``neq(0)``; masked pixels stay
    masked (the download turns them into 0, off).

    A collection is filtered to the AOI bounds and to the images that carry
    the band, sorted by ``system:time_start`` when every image has it (newest
    on top) and mosaicked, as ``catalogue.custom_image`` does for a source.
    """
    band = str(mask.band)
    if mask.asset_type == "IMAGE":
        img = ee.Image(mask.ref)
    else:
        col = (ee.ImageCollection(mask.ref).filterBounds(export_bounds(aoi))
               .filter(ee.Filter.listContains("system:band_names", band)).select([band]))
        timed = col.filter(ee.Filter.notNull(["system:time_start"]))
        img = ee.ImageCollection(ee.Algorithms.If(timed.size().eq(col.size()), col.sort("system:time_start"),
                                                  col)).mosaic()
    return img.select([band]).neq(0).rename("mask")


def mask_raw_estimate(mask, aoi_bounds) -> int:
    """Uncompressed size of the native download of the WGS84 ``aoi_bounds``:
    one uint8 band (``estimate_native_bytes`` counts two Int32 bands)."""
    return estimate_native_bytes(aoi_bounds, (mask.native.crs, mask.native.scale)) // 8


def export_mask_native(image, native, aoi, out_path, *, on_tiles=None, on_wait=None) -> Path:
    """Download the one-band mask ``image`` over ``aoi`` to ``out_path`` as
    uint8 0/1 in ``native``'s CRS and scale (no-data -> 0). Written as
    ``<out_path>.part`` and renamed once checked; on any failure nothing is
    left behind. Run it on a worker thread (``download.export_native``'s
    pattern)."""
    return download_checked(image.toUint8(), aoi, out_path, crs=native.crs, scale=native.scale,
                            dtypes=("uint8",), error=MaskRasterError, on_tiles=on_tiles, on_wait=on_wait)


def align_mask(src_path, grid: GridSpec, out_path, *, band: int = 1, aoi_raster=None) -> dict:
    """``src_path``'s ``band`` on the grid as Int32 0/1 (spec §7.2), nearest
    overviews, header-checked; cleared outside ``aoi_raster`` when given (a
    GeoTIFF mask: an Earth Engine download is clipped already). Returns
    ``{"on_pixels": n}``, the on-pixels left on the grid. An ``aoi_raster``
    off the grid raises ``raster_io.GridMismatchError`` before anything is
    written (a different shape would not broadcast; a shifted origin would
    clip in the wrong place)."""
    if aoi_raster is not None:
        check_header(aoi_raster, grid)
    on_pixels = 0
    with part_file(Path(out_path), grid) as part:
        reproject_to_grid(src_path, grid, part, band=band, pixel_hook=mask_on_pixels)
        with rasterio.open(part, "r+") as dst:
            aoi = rasterio.open(aoi_raster) if aoi_raster is not None else None
            try:
                for w in tile_windows(grid):
                    m = dst.read(1, window=w)
                    if aoi is not None:
                        inside = aoi.read(1, window=w) != 0
                        if (m[~inside] != 0).any():
                            m = np.where(inside, m, 0).astype("int32")
                            dst.write(m, 1, window=w)
                    on_pixels += int(np.count_nonzero(m))
            finally:
                if aoi is not None:
                    aoi.close()
            build_nearest_overviews(dst)
    return {"on_pixels": on_pixels}
