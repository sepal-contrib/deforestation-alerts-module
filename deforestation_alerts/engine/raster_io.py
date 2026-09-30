"""Writing and checking rasters on the project grid.

Every engine raster is Int32 with no-data 0 (spec D16), tiled 512 px, DEFLATE,
with *internal* overviews built with nearest-neighbour (averaging would invent
alert codes). Files are written as ``<name>.part`` in the destination folder and
renamed when complete, so a reader never opens half a file (spec §5.2).
"""

import contextlib
import math
import shutil
from pathlib import Path
from typing import Iterator

import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.windows import Window

from deforestation_alerts.project.models import GridSpec

BLOCK = 512


class GridMismatchError(RuntimeError):
    """A written raster is not on the project grid."""


class NotEnoughSpaceError(RuntimeError):
    """The project disk cannot hold the file about to be written."""


def grid_profile(grid: GridSpec, count: int) -> dict:
    return {
        "driver": "GTiff",
        "dtype": "int32",
        "nodata": 0,
        "count": count,
        "width": grid.width,
        "height": grid.height,
        "crs": CRS.from_user_input(grid.epsg),
        "transform": grid.affine,
        "tiled": True,
        "blockxsize": BLOCK,
        "blockysize": BLOCK,
        "compress": "deflate",
        "predictor": 2,
        "bigtiff": "if_safer",
    }


def row_windows(grid: GridSpec, rows: int = 2048) -> Iterator[Window]:
    for row0 in range(0, grid.height, rows):
        yield Window(0, row0, grid.width, min(rows, grid.height - row0))


def _overview_levels(width: int, height: int) -> list[int]:
    levels, f = [], 2
    while max(width, height) / f >= 256:
        levels.append(f)
        f *= 2
    return levels


@contextlib.contextmanager
def write_grid_raster(path: Path, grid: GridSpec, count: int):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with rasterio.open(part, "w", **grid_profile(grid, count)) as dst:
            yield dst
            levels = _overview_levels(grid.width, grid.height)
            if levels:
                dst.build_overviews(levels, Resampling.nearest)
                dst.update_tags(ns="rio_overview", resampling="nearest")
        check_header(part, grid)
        part.replace(path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def check_header(path, grid: GridSpec) -> None:
    with rasterio.open(path) as src:
        same_crs = src.crs is not None and src.crs == CRS.from_user_input(grid.epsg)
        same_shape = (src.width, src.height) == (grid.width, grid.height)
        same_transform = src.transform.almost_equals(grid.affine, precision=1e-6)
    if not (same_crs and same_shape and same_transform):
        raise GridMismatchError(
            f"{path} is not on the project grid "
            f"(crs={same_crs}, shape={same_shape}, transform={same_transform})"
        )


def ensure_free_space(folder: Path, needed_bytes: int) -> None:
    folder = Path(folder)
    probe = folder
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < needed_bytes:
        raise NotEnoughSpaceError(
            f"needs {math.ceil(needed_bytes / 1e9)} GB free in {folder}, "
            f"has {free // 10**9} GB"
        )


def ensure_overviews(path, resampling: str = "nearest") -> bool:
    """External ``.ovr`` for a user GeoTIFF we must not modify (spatial_risk
    ``overviews.ensure_overviews``, nearest by default). Rebuilt when older than
    the raster. Returns True when a pyramid was built.

    ``gdal.ExceptionMgr()`` scopes GDAL's exception state to this call instead
    of emitting a "neither UseExceptions() nor DontUseExceptions() called"
    FutureWarning on every ``gdal.Open`` (ruling #43).
    """
    from osgeo import gdal

    path = str(path)
    sidecar = Path(path + ".ovr")
    if sidecar.exists() and sidecar.stat().st_mtime < Path(path).stat().st_mtime:
        sidecar.unlink()
    with gdal.ExceptionMgr(useExceptions=True):
        ds = gdal.Open(path, gdal.GA_ReadOnly)
        try:
            if ds.GetRasterBand(1).GetOverviewCount() > 0:
                return False
            levels = _overview_levels(ds.RasterXSize, ds.RasterYSize)
            if not levels:
                return False
            ds.BuildOverviews(resampling.upper(), levels, options=["COMPRESS_OVERVIEW=DEFLATE"])
            return True
        finally:
            ds = None
