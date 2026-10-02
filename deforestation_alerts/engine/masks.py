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
from affine import Affine
from rasterio import features
from rasterio.crs import CRS
from shapely.geometry import box

from deforestation_alerts.engine.align import reproject_to_grid
from deforestation_alerts.engine.download import estimate_native_bytes
from deforestation_alerts.engine.raster_io import build_nearest_overviews, part_file, tile_windows, write_grid_raster
from deforestation_alerts.project.models import GridSpec
from deforestation_alerts.scripts.gee_progress import geedim_download_wait, geedim_tile_progress
from deforestation_alerts.scripts.raster_export import download_ee_image, export_bounds


class MaskRasterError(RuntimeError):
    """A raw mask download is not the 1-band uint8 raster in the native CRS that was asked for."""


def _window_transform(grid: GridSpec, w) -> Affine:
    return grid.affine @ Affine.translation(w.col_off, w.row_off)


def build_aoi_raster(sidecar_path, grid: GridSpec, out_path) -> Path:
    gdf = gpd.read_file(sidecar_path).to_crs(grid.epsg)
    geoms = list(gdf.geometry)
    sindex = gdf.sindex if len(gdf) else None
    with write_grid_raster(Path(out_path), grid, count=1) as dst:
        for w in tile_windows(grid):
            shape = (int(w.height), int(w.width))
            t = _window_transform(grid, w)
            arr = np.zeros(shape, np.int32)
            if sindex is not None:
                idx = sindex.query(box(t.c, t.f + t.e * shape[0], t.c + t.a * shape[1], t.f))
                if len(idx):
                    arr = features.rasterize(((geoms[i], 1) for i in idx), out_shape=shape, transform=t,
                                             fill=0, dtype="int32")
            dst.write(arr, 1, window=w)
    return Path(out_path)


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


def _check_raw(path: Path, crs: str) -> None:
    with rasterio.open(path) as src:
        same_crs = src.crs is not None and src.crs == CRS.from_user_input(crs)
        dtypes = src.dtypes
    if not (same_crs and dtypes == ("uint8",)):
        raise MaskRasterError(f"{path} is not a 1-band uint8 raster in {crs} (crs={same_crs}, dtypes={dtypes})")


def export_mask_native(image, native, aoi, out_path, *, on_tiles=None, on_wait=None) -> Path:
    """Download the one-band mask ``image`` over ``aoi`` to ``out_path`` as
    uint8 0/1 in ``native``'s CRS and scale (no-data -> 0). Written as
    ``<out_path>.part`` and renamed once checked; on any failure nothing is
    left behind. Run it on a worker thread (``download.export_native``'s
    pattern)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with geedim_tile_progress(on_tiles), geedim_download_wait(on_wait):
            download_ee_image(image.toUint8(), str(part), region=aoi, crs=native.crs, scale=native.scale,
                              dtype="uint8", unmask_value=0, nodata_value=0)
        _check_raw(part, native.crs)
        part.replace(out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out_path


def align_mask(src_path, grid: GridSpec, out_path, *, band: int = 1, aoi_raster=None) -> dict:
    """``src_path``'s ``band`` on the grid as Int32 0/1 (spec §7.2), nearest
    overviews, header-checked; cleared outside ``aoi_raster`` when given (a
    GeoTIFF mask: an Earth Engine download is clipped already). Returns
    ``{"on_pixels": n}``, the on-pixels left on the grid."""
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
