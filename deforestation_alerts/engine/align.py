"""Put a raster that is off the project grid onto it, streamed (spec §7.2, §8).

Earth Engine sources are downloaded in their native projection and aligned here
(user decision 2026-09-30); slice 3's GeoTIFF sources will be too. Ported from
spatial_risk's ``geo_utils.xr_reproject`` (odc-geo on dask), keeping what keeps
its memory from growing with the grid's area, all measured there on a
country-scale project that had killed a 16 GB kernel (spatial_risk memory
harmonize-all-oom-root-cause), with one change to how it writes:

* The warp is stored one destination row band (``DST_CHUNK`` rows) at a time:
  one ``dask.array.store`` per band into one open dataset, under a lock.
  spatial_risk writes with ``rio.to_raster(lock=True)``, a single store over
  the whole grid (rioxarray's default ``lock=None`` is worse: it evaluates the
  whole grid into one numpy array before the first write). Here that single
  store's peak still grew with the grid's area, in held numpy arrays: measured
  2026-09-30 (2-band Int32 rotated onto UTM, 2 workers, VmRSS), 1.15, 2.16 and
  3.87 GB for 12k, 24k and 32k px grids, against 0.85, 0.99 and 1.19 GB band by
  band (2.56 GB with 8 workers), pixel-identical and faster (32k: 48 s, not 60).
* source and destination chunks of 2048 px (see ``DST_CHUNK``/``SRC_CHUNK``);
* dask's threaded scheduler capped at ``gdal_env.worker_threads()`` (half the
  cores): every worker holds its own source window and destination tile;
* GDAL's block cache capped at 64 MiB during the warp.

What is left grows with the grid's *width* (a band's strip of source tiles) and
with the workers: each holds a source window of up to 3 x 3 16 MiB tiles and its
assembled copy, which sets the peak on small grids (traced numpy, 2 workers:
591 and 504 MB for 6000 and 8000 px, 507 and 670 MB with the single store).
tests/test_engine_align.py guards both: the width against ``lock=None``, the
height against the single store.

Nearest-neighbour only: the bands hold alert codes and integer dates, and any
other resampling would invent values.
"""

import threading
import warnings
from pathlib import Path
from typing import Callable

import dask
import dask.array
import numpy as np
import rasterio
import rioxarray
from odc.geo import xr as odc_xr
from odc.geo.geobox import GeoBox
from rasterio.errors import NotGeoreferencedWarning
from rasterio.windows import Window

from deforestation_alerts.engine.raster_io import grid_profile
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


class _WindowWriter:
    """``dask.array.store`` target that writes each (band, y, x) block into an
    open dataset at its window. The store's lock serialises the calls: a
    rasterio dataset must not be written from two threads at once."""

    def __init__(self, dst):
        self._dst = dst

    def __setitem__(self, key, block):
        bands, ys, xs = key
        indexes = [i + 1 for i in range(*bands.indices(self._dst.count))]
        window = Window.from_slices(ys, xs, height=self._dst.height, width=self._dst.width)
        self._dst.write(block, indexes=indexes, window=window)


def reproject_to_grid(
    src_path,
    grid: GridSpec,
    out_part_path,
    *,
    resampling: str = "nearest",
    band: int | None = None,
    pixel_hook: Callable[[np.ndarray, float | None], np.ndarray] | None = None,
) -> Path:
    """Write ``src_path`` aligned onto ``grid`` as ``out_part_path``.

    The output is on the engine raster contract except for overviews: Int32,
    no-data 0 (source no-data and everything outside the source become 0),
    tiled 512 px, DEFLATE with predictor 2. The caller builds the overviews,
    checks the header and renames the ``.part`` (see ``harmonize``).

    Every band is aligned, or one band when ``band`` (1-based) is given.
    ``pixel_hook(values, nodata)`` runs on each source block before the Int32
    cast and returns it as Int32 (0 is no-data): a float raster, which odc-geo
    will not cast to Int32 (``same_kind``), goes through it (``masks``).
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
        if band is not None:
            source = source.isel(band=[int(band) - 1])  # one chosen band, 1-based
        if pixel_hook is not None:
            # Decide each source pixel before any cast (a float32 0.7 must not
            # become 0, NaN no-data must not become anything): the hook turns a
            # block into Int32 0/1, whose no-data is 0.
            hooked = source.data.map_blocks(lambda block: pixel_hook(block, nodata), dtype="int32")
            source = source.copy(data=hooked).rio.write_nodata(0)
        elif nodata is not None and nodata != 0:
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
            # One store per destination row band (see the module docstring):
            # dask only ever sees one band's graph, so what it holds cannot
            # grow with the grid's height. The lock serialises the writes;
            # never lock=None.
            blocks, write_lock = aligned.data, threading.Lock()
            with rasterio.open(out_part_path, "w", **grid_profile(grid, count=blocks.shape[0])) as dst:
                writer = _WindowWriter(dst)
                for y0 in range(0, ny, DST_CHUNK):
                    y1 = min(y0 + DST_CHUNK, ny)
                    dask.array.store(
                        blocks[:, y0:y1, :],
                        writer,
                        regions=(slice(None), slice(y0, y1), slice(None)),
                        lock=write_lock,
                    )
        finally:
            opened.close()
    return out_part_path
