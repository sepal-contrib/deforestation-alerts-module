"""The date band of a custom alert source: its lowest and highest date values
over the AOI's WGS84 bounds rectangle, and the encoding they read as
(spec 2026-10-23-source-form, Goal 6).

Only finite values > 0 and < 1e7 are dates, as ``presets.to_yyyyddd`` reads
them: zero, negatives, no-data and a float32 fill (~3.4e38) are not. As in
``vis_probes``, the Earth Engine probe is a request (an ``ee.Dictionary`` the
form runs through ``gee_interface.get_info_async``) and a pure reading of its
info; the GeoTIFF probe is a blocking read (run it through
``asyncio.to_thread``).

Both read every pixel at the band's own resolution: an average or a sample of
dates is not a date the band holds. An AOI too large for Earth Engine fails
the read, and the user picks the encoding.
"""

import math
from typing import Optional, Tuple

import numpy as np

#: values at or above this are not dates (``presets.to_yyyyddd``'s bound)
DATE_CAP = 1e7
DATE_MAX_PIXELS = 1e9
DATE_TILE_SCALE = 4


def guess_encoding(lo: float, hi: float) -> Optional[str]:
    """The encoding both ends of a date range read as, or None.

    Not whole numbers between 1990 and 2100: ``decimal_year``. Whole numbers:
    ``yyyyddd`` (1990001-2100366), ``yyddd`` (10001-99366), each with a day
    of year 1-366 at both ends, or ``yymm`` (1001-9912) with a month 1-12 at
    both ends. Only the two ends are read: a range can fit several rules'
    edges, and the user can change the encoding."""
    ends = (float(lo), float(hi))
    if not all(math.isfinite(v) for v in ends):
        return None
    if not all(v.is_integer() for v in ends):
        return "decimal_year" if all(1990 <= v <= 2100 for v in ends) else None
    lo_i, hi_i = (int(v) for v in ends)
    for encoding, first, last, part, top in (("yyyyddd", 1990001, 2100366, 1000, 366),
                                             ("yyddd", 10001, 99366, 1000, 366),
                                             ("yymm", 1001, 9912, 100, 12)):
        if first <= lo_i and hi_i <= last and all(1 <= v % part <= top for v in (lo_i, hi_i)):
            return encoding
    return None


def date_range_request(ref: str, asset_type: str, band: str, bounds):
    """``{"min", "max"}`` of ``band``'s dates over ``bounds`` (both null when
    there is none), at the band's own nominal scale.

    A collection is read as the mosaic of its images that carry the band,
    newest on top when every image has a ``system:time_start`` (as the
    engine's ``catalogue.custom_image``), at the scale of the first image
    carrying the band: the mosaic's own is EPSG:4326 at 1 degree. With no
    image over ``bounds`` the mosaic is a fully masked band: no date.

    Never ``bestEffort``: a coarser scale averages the dates."""
    import ee

    if asset_type == "IMAGE":
        img = ee.Image(ref)
        scaled = img
    elif asset_type == "IMAGE_COLLECTION":
        col = ee.ImageCollection(ref).filter(ee.Filter.listContains("system:band_names", band))
        scaled = ee.Image(col.first())
        col = col.filterBounds(ee.Geometry.Rectangle(list(bounds))).select([band])
        timed = col.filter(ee.Filter.notNull(["system:time_start"]))
        ordered = ee.ImageCollection(ee.Algorithms.If(timed.size().eq(col.size()), col.sort("system:time_start"), col))
        # an empty collection's mosaic has no band: the masked one under it stands in
        img = ee.Image.constant(0).rename(band).selfMask().addBands(ordered.mosaic(), overwrite=True)
    else:
        raise ValueError(f"not an Earth Engine image or image collection: {asset_type!r}")
    values = img.select([band], ["d"])
    dates = values.updateMask(values.gt(0).And(values.lt(DATE_CAP)))
    stats = dates.reduceRegion(reducer=ee.Reducer.minMax(), geometry=ee.Geometry.Rectangle(list(bounds)),
                               scale=scaled.select(band).projection().nominalScale(),
                               maxPixels=DATE_MAX_PIXELS, tileScale=DATE_TILE_SCALE)
    return stats.rename(["d_min", "d_max"], ["min", "max"])


def date_range_from_info(info: dict) -> Optional[Tuple[float, float]]:
    """``(lowest, highest)`` from a ``date_range_request`` info; None when the band has no date there."""
    lo, hi = (info or {}).get("min"), (info or {}).get("max")
    if lo is None or hi is None:
        return None
    return float(lo), float(hi)


def file_date_range(path, band: int, bounds) -> Optional[Tuple[float, float]]:
    """The GeoTIFF counterpart of ``date_range_request`` + ``date_range_from_info``
    (``band`` 1-based): the window inside ``bounds`` (WGS84) at the file's own
    resolution, one block at a time, so a large AOI never sits in memory.
    None when the bounds miss the file."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    lo, hi = math.inf, -math.inf
    with rasterio.open(path) as src:
        box = transform_bounds("EPSG:4326", src.crs, *bounds, densify_pts=21) if src.crs else tuple(bounds)
        win = from_bounds(*box, transform=src.transform)
        col0, row0 = max(0, math.floor(win.col_off)), max(0, math.floor(win.row_off))
        col1 = min(src.width, math.ceil(win.col_off + win.width))
        row1 = min(src.height, math.ceil(win.row_off + win.height))
        block_h, block_w = src.block_shapes[band - 1]
        # the file's blocks that meet the window, each clipped to it
        for r in range(row0 - row0 % block_h, row1, block_h):
            for c in range(col0 - col0 % block_w, col1, block_w):
                top, left = max(r, row0), max(c, col0)
                part = Window(left, top, min(c + block_w, col1) - left, min(r + block_h, row1) - top)
                values = src.read(band, window=part, masked=True).compressed().astype(np.float64)
                dates = values[np.isfinite(values) & (values > 0) & (values < DATE_CAP)]
                if dates.size:
                    lo, hi = min(lo, float(dates.min())), max(hi, float(dates.max()))
    if lo > hi:
        return None
    return lo, hi
