"""AOI probes for the visualization form: a p2/p98 stretch and the class list of
a band, over the AOI's WGS84 bounds rectangle.

Earth Engine probes are split into a request (an ``ee.Dictionary`` the form
runs through ``gee_interface.get_info_async``) and a pure reading of its
info. GeoTIFF probes are blocking decimated reads: run them through
``asyncio.to_thread``. Errors are i18n keys, never translated here.
"""

import math
from typing import Optional, Tuple

import numpy as np

from gui.scripts.vis_classes import MAX_CLASSES

PROBE_MAX_PIXELS = 10_000_000
FILE_PROBE_SIZE = 1024  # the longest side of a GeoTIFF sample, in pixels
PERCENTILES = (2, 98)

ERR_NO_VALUES = "steps.layers.err_no_values"
ERR_NOT_INTEGER = "steps.layers.err_not_integer"
ERR_TOO_MANY = "steps.layers.err_too_many_classes"


def _region(img, band: str, bounds) -> dict:
    """The ``reduceRegion`` arguments shared by both Earth Engine probes."""
    import ee

    return {
        "geometry": ee.Geometry.Rectangle(list(bounds)),
        "scale": img.select(band).projection().nominalScale(),
        "bestEffort": True,
        "maxPixels": PROBE_MAX_PIXELS,
    }


def stretch_request(asset: str, bands, bounds):
    """p2 and p98 of each band of ``asset`` over ``bounds``, at the first band's nominal scale."""
    import ee

    img = ee.Image(asset).select(list(bands))
    return img.reduceRegion(reducer=ee.Reducer.percentile(list(PERCENTILES)), **_region(img, bands[0], bounds))


def stretch_from_info(info: dict, bands) -> Optional[Tuple[float, float]]:
    """``(lowest p2, highest p98)`` over ``bands``; ``None`` when a value is missing or there is no spread."""
    lows, highs = [], []
    for b in bands:
        lo, hi = info.get(f"{b}_p2"), info.get(f"{b}_p98")
        if lo is None or hi is None:
            return None
        lows.append(float(lo))
        highs.append(float(hi))
    return _span(lows, highs)


def classes_request(asset: str, band: str, bounds):
    """The band's histogram over ``bounds``, trimmed on the server to its size
    ``n`` and its first ``MAX_CLASSES + 1`` keys (a float band can hold millions),
    and the band's ``*_class_*`` properties, if any."""
    import ee

    img = ee.Image(asset)
    one = img.select(band)
    hist = one.reduceRegion(reducer=ee.Reducer.frequencyHistogram(), **_region(img, band, bounds)).get(band)
    h = ee.Dictionary(ee.Algorithms.If(hist, hist, {}))  # null when nothing is found
    meta = img.toDictionary([f"{band}_class_values", f"{band}_class_palette", f"{band}_class_names"])
    return ee.Dictionary({"n": h.size(), "hist": h.select(h.keys().slice(0, MAX_CLASSES + 1)), "meta": meta})


def classes_from_info(info: dict, band: str) -> dict:
    """``{"values", "known"}`` from a ``classes_request`` info, or ``{"error": key}``."""
    hist = info.get("hist") or {}
    checked = check_values([float(k) for k in hist])
    if "error" not in checked and info.get("n", len(hist)) > MAX_CLASSES:
        checked = {"error": ERR_TOO_MANY}  # the keys beyond the trimmed ones
    if "error" in checked:
        return checked
    meta = info.get("meta") or {}
    parts = [meta.get(f"{band}_class_{p}") for p in ("values", "palette", "names")]
    known = None
    if all(isinstance(p, list) for p in parts) and len({len(p) for p in parts}) == 1:
        known = dict(zip(("values", "palette", "names"), parts))
    return {"values": checked["values"], "known": known}


def check_values(values) -> dict:
    """The categorical rule: some values, all whole, at most ``MAX_CLASSES``."""
    values = [float(v) for v in values]
    if not values:
        return {"error": ERR_NO_VALUES}
    if not all(v.is_integer() for v in values):  # NaN and infinities are not whole either
        return {"error": ERR_NOT_INTEGER}
    ordered = sorted({int(v) for v in values})
    if len(ordered) > MAX_CLASSES:
        return {"error": ERR_TOO_MANY}
    return {"values": ordered}


def file_sample(path, bands, bounds) -> np.ma.MaskedArray:
    """``bands`` (1-based) of the GeoTIFF inside ``bounds`` (WGS84), read nearest
    with the longest side at most ``FILE_PROBE_SIZE``; nodata and non-finite
    values masked. Fully masked when the bounds miss the file."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    bands = [int(b) for b in bands]
    with rasterio.open(path) as src:
        box = transform_bounds("EPSG:4326", src.crs, *bounds, densify_pts=21) if src.crs else tuple(bounds)
        win = from_bounds(*box, transform=src.transform)
        col0, row0 = max(0, math.floor(win.col_off)), max(0, math.floor(win.row_off))
        col1 = min(src.width, math.ceil(win.col_off + win.width))
        row1 = min(src.height, math.ceil(win.row_off + win.height))
        if col1 <= col0 or row1 <= row0:
            return np.ma.masked_all((len(bands), 1, 1), dtype=src.dtypes[bands[0] - 1])
        w, h = col1 - col0, row1 - row0
        k = min(1.0, FILE_PROBE_SIZE / max(w, h))
        shape = (len(bands), max(1, round(h * k)), max(1, round(w * k)))
        data = src.read(bands, window=Window(col0, row0, w, h), out_shape=shape,
                        resampling=Resampling.nearest, masked=True)
    if np.issubdtype(data.dtype, np.floating):
        data = np.ma.masked_where(~np.isfinite(data.filled(0)), data)
    return data


def file_stretch(path, bands, bounds) -> Optional[Tuple[float, float]]:
    """The GeoTIFF counterpart of ``stretch_request`` + ``stretch_from_info``."""
    sample = file_sample(path, bands, bounds)
    lows, highs = [], []
    for band in sample:
        values = band.compressed()
        if values.size == 0:
            return None
        lo, hi = np.percentile(values, PERCENTILES)
        lows.append(float(lo))
        highs.append(float(hi))
    return _span(lows, highs)


def file_classes(path, band: int, bounds) -> dict:
    """The GeoTIFF counterpart of ``classes_from_info``; files carry no class metadata."""
    checked = check_values(np.unique(file_sample(path, [band], bounds)[0].compressed()))
    if "error" in checked:
        return checked
    return {"values": checked["values"], "known": None}


def _span(lows, highs) -> Optional[Tuple[float, float]]:
    lo, hi = min(lows), max(highs)
    return (lo, hi) if lo < hi else None
