"""Put a raw alert raster on the project grid and apply the enabled masks (spec §7.3).

Purely local. The raw file is in its source's native projection (user decision
2026-09-30): it is aligned onto the grid (``align.reproject_to_grid``, nearest)
straight into ``<out>.part``, the masks are applied to that file window by
window, nearest internal overviews are built, and ``raster_io.part_file``
checks the header against the grid and renames it. Nothing reads or writes a
whole grid in memory.
"""

from contextlib import ExitStack
from pathlib import Path

import numpy as np
import rasterio

from deforestation_alerts.engine.align import reproject_to_grid
from deforestation_alerts.engine.raster_io import build_nearest_overviews, check_header, part_file, row_windows
from deforestation_alerts.project.models import GridSpec

MODES = ("keep", "exclude")


def harmonize(raw_path, masks, out_path, grid: GridSpec) -> Path:
    """``masks``: [(path, "keep" | "exclude")], each on ``grid``. A keep mask
    drops alerts where it is 0; an exclude mask drops them where it is non-zero.
    A dropped pixel gets alert 0 and date 0."""
    for path, mode in masks:
        if mode not in MODES:
            raise ValueError(f"mask mode must be one of {MODES}, not {mode!r}")
        check_header(path, grid)
    out_path = Path(out_path)
    with part_file(out_path, grid) as part:
        reproject_to_grid(raw_path, grid, part)
        with ExitStack() as stack:
            dst = stack.enter_context(rasterio.open(part, "r+"))
            opened = [(stack.enter_context(rasterio.open(path)), mode) for path, mode in masks]
            if opened:
                for w in row_windows(grid):
                    drop = np.zeros((w.height, w.width), dtype=bool)
                    for src, mode in opened:
                        m = src.read(1, window=w) > 0
                        drop |= ~m if mode == "keep" else m
                    if drop.any():
                        for band in (1, 2):
                            dst.write(np.where(drop, 0, dst.read(band, window=w)).astype("int32"), band, window=w)
            build_nearest_overviews(dst)
    return out_path
