"""Put a raster that is off the project grid onto it, streamed (spec §7.2, §8).

Earth Engine sources are downloaded in their native projection and aligned here
(user decision 2026-09-30); slice 3's GeoTIFF sources will be too. Ported from
spatial_risk's ``geo_utils.xr_reproject`` (odc-geo on dask), keeping what keeps
its memory from growing with the grid's area, all measured there on a
country-scale project that had killed a 16 GB kernel (spatial_risk memory
harmonize-all-oom-root-cause):

* ``lock=True`` in ``rio.to_raster``. rioxarray's default ``lock=None`` takes
  its non-dask branch, which evaluates the whole grid into one numpy array
  before the first write. With a lock it streams through ``dask.array.store``,
  one destination tile at a time.
* source and destination chunks of 2048 px (see ``DST_CHUNK``/``SRC_CHUNK``);
* dask's threaded scheduler capped at ``gdal_env.worker_threads()`` (half the
  cores): every worker holds its own source window and destination tile;
* GDAL's block cache capped at 64 MiB during the warp.

What is left grows with the grid's *width*: dask keeps a strip of source tiles
in flight. Measured here (2026-09-30, 2-band Int32 rasters rotated onto UTM, 2
workers, traced numpy peak): 507 MB for a 6000 px square source, 670 MB for
8000 px; tests/test_engine_align.py guards the difference from ``lock=None``.

Nearest-neighbour only: the bands hold alert codes and integer dates, and any
other resampling would invent values.
"""

import warnings
from pathlib import Path

import dask
import rasterio
import rioxarray
from odc.geo import xr as odc_xr
from odc.geo.geobox import GeoBox
from rasterio.errors import NotGeoreferencedWarning

from deforestation_alerts.engine.raster_io import BLOCK
from deforestation_alerts.gdal_env import worker_threads
from deforestation_alerts.project.models import GridSpec

# Destination tile size of the warp. odc-geo defaults the output chunks to the
# input's, so a coarse source would otherwise tile the grid into thousands of
# tiny blocks, one GDAL warp setup each (spatial_risk: ~100x slower).
DST_CHUNK = 2048
# Source tile size, matched to DST_CHUNK. rioxarray's "auto" reads 128 MiB
# tiles, and every destination tile copies all the source tiles it overlaps
# (spatial_risk: 6.8 GB peak with "auto", 1.0 GB with 2048).
SRC_CHUNK = DST_CHUNK
# GDAL block cache during the warp; its 5 %-of-RAM default buys nothing on a
# single read-then-write pass but that much extra RSS.
WARP_CACHEMAX_BYTES = 64 * 1024 * 1024


def _geobox(grid: GridSpec) -> GeoBox:
    return GeoBox((grid.height, grid.width), grid.affine, grid.epsg)


def reproject_to_grid(src_path, grid: GridSpec, out_part_path, *, resampling: str = "nearest") -> Path:
    """Write ``src_path`` aligned onto ``grid`` as ``out_part_path``.

    The output is on the engine raster contract except for overviews: Int32,
    no-data 0 (source no-data and everything outside the source become 0),
    tiled 512 px, DEFLATE with predictor 2. The caller builds the overviews,
    checks the header and renames the ``.part`` (see ``harmonize``).
    """
    if resampling != "nearest":
        raise ValueError(f"only nearest resampling keeps codes and dates exact, not {resampling!r}")
    out_part_path = Path(out_part_path)
    geobox = _geobox(grid)
    ny, nx = geobox.shape.yx
    with (
        warnings.catch_warnings(),
        rasterio.Env(GDAL_CACHEMAX=WARP_CACHEMAX_BYTES),
        dask.config.set(scheduler="threads", num_workers=worker_threads()),
    ):
        # odc-geo warps one numpy block at a time and GDAL warns that the block
        # dataset it wraps them in "has no geotransform": noise about a
        # temporary, never about the input (which has a CRS and transform to
        # get here). odc-geo and rioxarray still multiply Affines with ``*``,
        # which affine 3 deprecates, and rioxarray tries every tag that looks
        # like a list as numbers (geedim's JSON "system-footprint" is not one):
        # nothing to act on here either.
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        warnings.filterwarnings("ignore", "Use `@` matmul", PendingDeprecationWarning)
        warnings.filterwarnings("ignore", "string or file could not be read to its end", DeprecationWarning)
        # Lazy, tiled read: nothing is decoded until the store below pulls a
        # tile. lock=False gives every dask thread its own file handle.
        opened = source = rioxarray.open_rasterio(
            src_path, chunks={"band": 1, "x": SRC_CHUNK, "y": SRC_CHUNK}, cache=False, lock=False
        )
        nodata = source.rio.nodata
        if nodata is not None and nodata != 0:
            # Asked for a destination no-data of 0, rasterio's warp writes the
            # *source* no-data value where there is no data: make it 0 first.
            source = source.where(source != nodata, 0).rio.write_nodata(0)
        try:
            aligned = odc_xr.xr_reproject(
                source,
                geobox,
                resampling=resampling,
                dst_nodata=0,
                dtype="int32",
                chunks=(min(DST_CHUNK, ny), min(DST_CHUNK, nx)),
            )
            aligned.rio.to_raster(
                out_part_path,
                driver="GTiff",
                dtype="int32",
                compress="DEFLATE",
                predictor=2,
                bigtiff="IF_SAFER",
                tiled=True,
                blockxsize=BLOCK,
                blockysize=BLOCK,
                lock=True,  # stream: never lock=None (see the module docstring)
            )
        finally:
            opened.close()
    return out_part_path
