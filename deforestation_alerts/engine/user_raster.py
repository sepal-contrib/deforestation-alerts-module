"""User rasters (custom alert sources, masks, the attribute raster): what is in
them and the grid they are read or downloaded in (spec §6.2).

Earth Engine metadata comes from ONE request the caller evaluates with
``await gee_interface.get_info_async(...)`` (``discovery_request``); nothing
here calls ``getInfo``. GeoTIFF headers are read with rasterio (call
``read_header`` through ``asyncio.to_thread``). Errors are message keys.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds

from deforestation_alerts.engine.download import METRES_PER_DEGREE
from deforestation_alerts.project.models import PRESETS, BandMap, NativeGrid


class UserRasterError(ValueError):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(detail or key)
        self.key = key


@dataclass(frozen=True)
class BandInfo:
    name: str
    index: int  # 1-based
    dtype: str
    numeric: bool


@dataclass(frozen=True)
class FileHeader:
    crs: Optional[str]
    transform: tuple
    width: int
    height: int
    bounds_wgs84: Optional[tuple]
    bands: tuple
    nodata: Optional[float]


def _crs_text(crs) -> Optional[str]:
    if crs is None:
        return None
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.to_string()


def _numeric(dtype) -> bool:
    """Integer or real; a complex band holds no alert or date (GDAL's
    ``complex_int16`` is not even a NumPy type)."""
    try:
        dt = np.dtype(dtype)
    except TypeError:
        return False
    return bool(np.issubdtype(dt, np.integer) or np.issubdtype(dt, np.floating))


def read_header(path) -> FileHeader:
    """The file's grid, its WGS84 extent and its bands ("Band i" when a band
    has no description)."""
    try:
        with rasterio.open(path) as src:
            names = [d if d else f"Band {i}" for i, d in enumerate(src.descriptions, start=1)]
            bands = tuple(BandInfo(name=n, index=i, dtype=str(dt), numeric=_numeric(dt))
                          for i, (n, dt) in enumerate(zip(names, src.dtypes), start=1))
            bounds = (tuple(transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21))
                      if src.crs is not None else None)
            return FileHeader(crs=_crs_text(src.crs), transform=tuple(src.transform)[:6], width=src.width,
                              height=src.height, bounds_wgs84=bounds, bands=bands, nodata=src.nodata)
    except Exception as exc:  # noqa: BLE001 - an unreadable header is the user's file, not a bug
        # not only RasterioIOError: a CRS with no path to WGS84 (an engineering
        # CRS) fails in transform_bounds with GDAL's own error class
        raise UserRasterError("steps.alerts.err_unreadable", str(exc)) from exc


def file_native(header: FileHeader) -> NativeGrid:
    """The file's own grid; the scale in metres (a geographic file's degrees x
    ``METRES_PER_DEGREE``). Call ``check_file`` first: a file without a CRS has
    no native grid."""
    a = abs(float(header.transform[0]))
    geographic = CRS.from_user_input(header.crs).is_geographic
    return NativeGrid(crs=header.crs, scale=a * METRES_PER_DEGREE if geographic else a,
                      transform=tuple(float(v) for v in header.transform), bounds=header.bounds_wgs84)


def check_file(header: FileHeader, indexes) -> Optional[str]:
    """None when the file has a CRS and every mapped (1-based) band exists and
    is numeric; otherwise the message key of the first problem."""
    if header.crs is None:
        return "steps.alerts.err_no_crs"
    by_index = {b.index: b for b in header.bands}
    for i in indexes:
        if i not in by_index:
            return "steps.alerts.err_missing_band"
        if not by_index[i].numeric:
            return "steps.alerts.err_not_numeric"
    return None


# ------------------------------------------------------------------ Earth Engine
def band_list_request(ref: str, asset_type: str):
    """An image's band names; a collection's distinct band names over all its
    images (``getAsset`` on a collection lists no bands)."""
    import ee

    if asset_type == "IMAGE":
        return ee.Image(ref).bandNames()
    return ee.ImageCollection(ref).aggregate_array("system:band_names").flatten().distinct()


def discovery_request(ref: str, asset_type: str, bands: list):
    """Everything validation needs, in one request (spec §6.2): the number of
    images carrying every mapped band, the first such image's band types and
    its grid (the first band's projection: the alert band, or the date band
    for date-only), every matching image's CRS and the finest scale, and the
    ``dateFormat`` property.

    A collection's mosaic reports EPSG:4326 at 1 degree, so the grid is the
    first matching image's. When nothing matches, the answer is
    ``{"count": 0, "asset_type": ...}``: the other entries would fail on a
    missing band, and ``ee.Algorithms.If`` evaluates only the branch it takes
    (``test_engine_user_raster_ee.py`` pins that)."""
    import ee

    bands = [str(b) for b in bands]
    lead = bands[0]
    if asset_type == "IMAGE":
        img = ee.Image(ref)
        count = ee.Algorithms.If(img.bandNames().containsAll(bands), 1, 0)
        first = img
        crss = ee.List([img.select(lead).projection().crs()])
        min_scale = img.select(lead).projection().nominalScale()
        date_format = img.get("dateFormat")
    else:
        col = ee.ImageCollection(ref)
        for b in bands:
            col = col.filter(ee.Filter.listContains("system:band_names", b))
        count = col.size()
        first = ee.Image(col.first())
        tagged = col.map(lambda i: i.set("_crs", i.select(lead).projection().crs(),
                                         "_scale", i.select(lead).projection().nominalScale()))
        crss = tagged.aggregate_array("_crs").distinct()
        min_scale = tagged.aggregate_min("_scale")
        date_format = ee.Algorithms.If(ee.ImageCollection(ref).propertyNames().contains("dateFormat"),
                                       ee.ImageCollection(ref).get("dateFormat"), first.get("dateFormat"))
    proj = first.select(lead).projection()
    # ``projection`` evaluates to {"type": "Projection", "crs": ..., "transform": [six numbers]}
    return ee.Dictionary(ee.Algorithms.If(
        ee.Number(count).gt(0),
        ee.Dictionary({
            "count": count,
            "crs": proj.crs(),
            "scale": proj.nominalScale(),
            "projection": proj,
            "crss": crss,
            "min_scale": min_scale,
            "date_format": date_format,
            "band_types": first.select(bands).bandTypes(),
        }),
        ee.Dictionary({"count": 0, "asset_type": asset_type}),
    ))


def native_from_discovery(info: dict) -> NativeGrid:
    """The grid an Earth Engine source downloads in: the first matching
    image's, or EPSG:4326 at the finest nominal scale when the matching images
    use several CRSs or a CRS that is not an ``EPSG:`` code."""
    crs = str(info.get("crs") or "")
    crss = list(info.get("crss") or [crs])
    transform = (info.get("projection") or {}).get("transform") or info.get("transform")
    if crs.startswith("EPSG:") and len(set(crss)) <= 1:
        return NativeGrid(crs=crs, scale=float(info["scale"]),
                          transform=tuple(float(v) for v in transform) if transform else None)
    # several CRSs (UTM tiles spanning zones) or a CRS that is not an EPSG code:
    # EPSG:4326 at the finest nominal scale (spec §6.2, §7.2)
    return NativeGrid(crs="EPSG:4326", scale=float(info.get("min_scale") or info["scale"]), transform=None)


def check_discovery(info: dict, fmt: str, bands: list) -> Optional[str]:
    """None when ``discovery_request``'s answer is usable for ``fmt``;
    otherwise the message key of the first problem."""
    if not info or int(info.get("count") or 0) <= 0:
        # an image that lacks a mapped band is not "no image of the collection"
        if info and info.get("asset_type") == "IMAGE":
            return "steps.alerts.err_missing_band"
        return "steps.alerts.err_no_images"
    types = info.get("band_types") or {}
    for b in bands:
        if str(b) not in types:
            return "steps.alerts.err_missing_band"
        if (types[str(b)] or {}).get("precision") not in ("int", "float", "double"):
            return "steps.alerts.err_not_numeric"
    # CCDC: dateFormat 0 (Julian days) and 2 (Unix ms) are refused; absent or 1 is fractional years
    if fmt == "ccdc" and info.get("date_format") not in (None, 1, "1"):
        return "steps.alerts.err_date_format"
    return None


def band_projections_request(ref: str, bands: list):
    """``{band: {"crs", "scale"}}`` of an image's bands (bands may differ)."""
    import ee

    img = ee.Image(ref)
    return ee.Dictionary({
        str(b): ee.Dictionary({"crs": img.select(str(b)).projection().crs(),
                               "scale": img.select(str(b)).projection().nominalScale()})
        for b in bands
    })


def _match(names, wanted: Optional[str]):
    if wanted is None:
        return None
    hits = [n for n in names if str(n).casefold() == wanted.casefold()]
    return hits[0] if hits else None


def suggest_format(band_names) -> Optional[str]:
    """The preset whose band names (case-insensitive) are all present; else
    date-only when there is any band."""
    names = list(band_names)
    for fmt in ("radd", "ccdc"):
        if all(_match(names, b) for b in PRESETS[fmt]["bands"]):
            return fmt
    return "date_only" if names else None


def suggest_band_map(fmt: str, band_names) -> Optional[BandMap]:
    """Band **names** for ``fmt`` (a GeoTIFF caller maps them to its 1-based
    indexes); None when the names do not identify the bands ("Band i" names
    never do)."""
    names = list(band_names)
    if fmt == "date_only":
        dates = [n for n in names if "date" in str(n).casefold()]
        return BandMap(alert=None, date=dates[0]) if len(dates) == 1 else None
    alert_name, date_name = PRESETS[fmt]["bands"]
    alert, dt = _match(names, alert_name), _match(names, date_name)
    return BandMap(alert=alert, date=dt) if alert and dt else None
