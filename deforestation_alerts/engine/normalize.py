"""GeoTIFF alert sources -> the native raw raster (spec §7.2).

The user's file is read window by window, only inside the AOI bounds
transformed to its CRS; the mapped bands go through the source's preset in
float64 (``presets.apply_preset``); the 2-band Int32 raw file is written in
the file's own CRS and pixel grid as ``<out>.part`` and renamed once checked.
Harmonize aligns it like an Earth Engine raw file and clips it to the AOI
polygon. A file that misses the AOI gives a 1 x 1 empty raster ("0 alerts
found" after harmonizing), never an error.
"""

import math
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from affine import Affine
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from deforestation_alerts.engine import presets
from deforestation_alerts.engine.catalogue import encoding_for, yyyyddd
from deforestation_alerts.engine.download import RawRasterError
from deforestation_alerts.engine.raster_io import BLOCK, TILE

_PROFILE = {"driver": "GTiff", "dtype": "int32", "count": 2, "nodata": 0, "tiled": True,
            "blockxsize": BLOCK, "blockysize": BLOCK, "compress": "deflate", "predictor": 2,
            "bigtiff": "if_safer"}


def aoi_window(ds, aoi_bounds) -> Optional[Window]:
    left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *aoi_bounds, densify_pts=21)
    win = rasterio.windows.from_bounds(left, bottom, right, top, transform=ds.transform)
    col0 = max(0, math.floor(win.col_off))
    row0 = max(0, math.floor(win.row_off))
    col1 = min(ds.width, math.ceil(win.col_off + win.width))
    row1 = min(ds.height, math.ceil(win.row_off + win.height))
    if col1 <= col0 or row1 <= row0:
        return None
    return Window(col0, row0, col1 - col0, row1 - row0)


def estimate_bytes(src, aoi_bounds) -> int:
    with rasterio.open(src.ref) as ds:
        win = aoi_window(ds, aoi_bounds)
    return 8 if win is None else int(win.width) * int(win.height) * 8


def _read(ds, band, window) -> Optional[np.ndarray]:
    if band is None:
        return None
    data = ds.read(int(band), window=window, masked=True)
    return np.ma.filled(data.astype(np.float64), np.nan)


def normalize_file(src, aoi_bounds, win_start: date, win_end: date, out_path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    part.unlink(missing_ok=True)
    encoding = encoding_for(src)
    lo, hi = yyyyddd(win_start), yyyyddd(win_end)
    try:
        with rasterio.open(src.ref) as ds:
            win = aoi_window(ds, aoi_bounds)
            empty = win is None
            if empty:
                win = Window(0, 0, 1, 1)
            profile = dict(_PROFILE, crs=ds.crs, transform=ds.transform @ Affine.translation(win.col_off, win.row_off),
                           width=int(win.width), height=int(win.height))
            with rasterio.open(part, "w", **profile) as dst:
                if not empty:
                    for row in range(0, int(win.height), TILE):
                        for col in range(0, int(win.width), TILE):
                            h = min(TILE, int(win.height) - row)
                            w = min(TILE, int(win.width) - col)
                            read_win = Window(win.col_off + col, win.row_off + row, w, h)
                            alert, dates = presets.apply_preset(
                                _read(ds, src.band_map.alert, read_win), _read(ds, src.band_map.date, read_win),
                                src.format, encoding, lo, hi)
                            out_win = Window(col, row, w, h)
                            dst.write(alert, 1, window=out_win)
                            dst.write(dates, 2, window=out_win)
                crs = ds.crs
        with rasterio.open(part) as check:
            if check.crs != crs or check.count != 2 or check.dtypes != ("int32", "int32"):
                raise RawRasterError(f"{part} is not a 2-band Int32 raster in {crs}")
        part.replace(out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return out_path
