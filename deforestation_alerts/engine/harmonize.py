"""Put a raw alert raster on the project grid, clip it, apply masks, count (spec §7.3).

Purely local. The raw file is in its source's native projection: it is
aligned onto the grid (``align.reproject_to_grid``, nearest) straight into
``<out>.part``; then, tile by tile (2048 x 2048, never full-width strips), a
GeoTIFF source is cleared outside the AOI raster, the masks are applied (a
keep mask drops alerts where it is 0, an exclude mask where it is non-zero;
negative values are non-zero) and the alert pixels are counted before and
after the masks; nearest internal overviews are built, and
``raster_io.part_file`` checks the header and renames the file.
"""

from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio

from deforestation_alerts.engine.align import reproject_to_grid
from deforestation_alerts.engine.raster_io import build_nearest_overviews, check_header, part_file, tile_windows
from deforestation_alerts.project.models import GridSpec

MODES = ("keep", "exclude")


def harmonize(raw_path, masks, out_path, grid: GridSpec, *, aoi_raster=None) -> dict:
    """Write ``out_path`` and return its stats (spec §5.1)."""
    for path, mode in masks:
        if mode not in MODES:
            raise ValueError(f"mask mode must be one of {MODES}, not {mode!r}")
        check_header(path, grid)
    if aoi_raster is not None:
        check_header(aoi_raster, grid)
    stats = {"alert_pixels": 0, "alert_pixels_unmasked": 0, "date_min": None, "date_max": None}
    with part_file(Path(out_path), grid) as part:
        reproject_to_grid(raw_path, grid, part)
        with ExitStack() as stack:
            dst = stack.enter_context(rasterio.open(part, "r+"))
            opened = [(stack.enter_context(rasterio.open(path)), mode) for path, mode in masks]
            aoi = stack.enter_context(rasterio.open(aoi_raster)) if aoi_raster is not None else None
            for w in tile_windows(grid):
                alert = dst.read(1, window=w)
                if not alert.any():
                    continue  # nothing to clip, mask or count here
                has = alert != 0
                if aoi is not None:
                    has &= aoi.read(1, window=w) != 0
                stats["alert_pixels_unmasked"] += int(has.sum())
                keep = has.copy()
                for src, mode in opened:
                    on = src.read(1, window=w) != 0
                    keep &= on if mode == "keep" else ~on
                n = int(keep.sum())
                stats["alert_pixels"] += n
                date = dst.read(2, window=w)
                if n:
                    kept = date[keep]
                    lo, hi = int(kept.min()), int(kept.max())
                    stats["date_min"] = lo if stats["date_min"] is None else min(stats["date_min"], lo)
                    stats["date_max"] = hi if stats["date_max"] is None else max(stats["date_max"], hi)
                if not np.array_equal(keep, alert != 0):
                    dst.write(np.where(keep, alert, 0).astype("int32"), 1, window=w)
                    dst.write(np.where(keep, date, 0).astype("int32"), 2, window=w)
            build_nearest_overviews(dst)
    return stats
