"""The date band of a custom alert source: its lowest and highest date values
over the AOI's WGS84 bounds rectangle, and the encoding they read as
(spec 2026-10-23-source-form, Goal 6).

Only finite values > 0 and < 1e7 are dates, as ``presets.to_yyyyddd`` reads
them: zero, negatives, no-data and a float32 fill (~3.4e38) are not. As in
``vis_probes``, the Earth Engine probe is a request (an ``ee.Dictionary`` the
form runs through ``gee_interface.get_info_async``) and a pure reading of its
info; the GeoTIFF probe is a blocking decimated read (run it through
``asyncio.to_thread``).
"""

import math
from typing import Optional, Tuple

import numpy as np

from gui.scripts.vis_probes import _region, file_sample

#: values at or above this are not dates (``presets.to_yyyyddd``'s bound)
DATE_CAP = 1e7


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
    carrying the band: the mosaic's own is EPSG:4326 at 1 degree."""
    import ee

    if asset_type == "IMAGE":
        img = ee.Image(ref)
        scaled = img
    else:
        col = ee.ImageCollection(ref).filter(ee.Filter.listContains("system:band_names", band))
        scaled = ee.Image(col.first())
        col = col.filterBounds(ee.Geometry.Rectangle(list(bounds))).select([band])
        timed = col.filter(ee.Filter.notNull(["system:time_start"]))
        ordered = ee.ImageCollection(ee.Algorithms.If(timed.size().eq(col.size()), col.sort("system:time_start"), col))
        img = ordered.mosaic()
    values = img.select([band], ["d"])
    dates = values.updateMask(values.gt(0).And(values.lt(DATE_CAP)))
    stats = dates.reduceRegion(reducer=ee.Reducer.minMax(), **_region(scaled, band, bounds))
    return stats.rename(["d_min", "d_max"], ["min", "max"])


def date_range_from_info(info: dict) -> Optional[Tuple[float, float]]:
    """``(lowest, highest)`` from a ``date_range_request`` info; None when the band has no date there."""
    lo, hi = (info or {}).get("min"), (info or {}).get("max")
    if lo is None or hi is None:
        return None
    return float(lo), float(hi)


def file_date_range(path, band: int, bounds) -> Optional[Tuple[float, float]]:
    """The GeoTIFF counterpart of ``date_range_request`` + ``date_range_from_info``
    (``band`` 1-based), on ``vis_probes.file_sample``'s decimated read."""
    values = file_sample(path, [band], bounds)[0].compressed().astype(np.float64)
    dates = values[np.isfinite(values) & (values > 0) & (values < DATE_CAP)]
    if dates.size == 0:
        return None
    return float(dates.min()), float(dates.max())
