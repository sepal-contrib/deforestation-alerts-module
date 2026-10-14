"""GLAD-L, RADD, GLAD-S2 and custom Earth Engine sources as images on the engine contract.

Contract (spec D16, §7.2): bands ``alert`` (1 confirmed, 2 probable) and
``date`` (integer yyyyddd), Int32, masked where there is no alert and outside
the *inclusive* window [win_start, win_end], clipped to the AOI. The old
``aoi_helper._from_*`` builders used strict gt/lt and dropped both end days.

No builder calls getInfo. Only GLAD-L's needs metadata (UpdResult's band
list), from ``meta_request("GLAD-L")``, which the app evaluates once per
session with ``await gee_interface.get_info_async``. Where and when each source
holds data for an AOI is ``engine.coverage``'s (spec §6.2).
"""

from datetime import date, timedelta

import ee

from deforestation_alerts.project.models import PRESETS, mapped_bands
from deforestation_alerts.scripts.raster_export import export_bounds

KEYS = ("GLAD-L", "RADD", "GLAD-S2")
LABEL_KEYS = {
    "GLAD-L": "steps.alerts.label_glad_l",
    "RADD": "steps.alerts.label_radd",
    "GLAD-S2": "steps.alerts.label_glad_s2",
}
#: Each source's native projection as (crs, Earth Engine nominal scale in
#: metres), like spatial_risk's per-variable default_crs/default_scale: the raw
#: download is made in it (download.export_native) and aligned onto the project
#: grid locally. Static, so no metadata round trip: GLAD-L is 0.00025 deg,
#: RADD 0.0000898315 deg (10 m) and GLAD-S2 0.0001 deg, all EPSG:4326 (checked
#: on Earth Engine 2026-09-30; tests/test_engine_prepare_ee.py re-checks it).
NATIVE = {
    "GLAD-L": ("EPSG:4326", 27.829872698318393),
    "RADD": ("EPSG:4326", 10.0),
    "GLAD-S2": ("EPSG:4326", 11.131949079327358),
}

GLAD_L_UPD = "projects/glad/alert/UpdResult"
# GLAD-L yearly archives: year -> (collection, last day it holds). Each
# "<year>final" collection holds conf<yy>/alertDate<yy> for its year (plus a
# partial next year). There is no 2024final: the only 2024 alerts are the
# conf24/alertDate24 bands of 2023final, which stop where that collection was
# cut (2024-07-16). UpdResult holds the years its bands name (meta "bands"), so
# nothing holds GLAD-L between that cut and 2025 (checked on Earth Engine,
# 2026-09-30).
GLAD_L_FINAL = {
    **{year: (f"projects/glad/alert/{year}final", date(year, 12, 31)) for year in range(2018, 2024)},
    2024: ("projects/glad/alert/2023final", date(2024, 7, 16)),
}
RADD_COLLECTION = "projects/radar-wur/raddalert/v1"
GLAD_S2 = "projects/glad/S2alert"
GLAD_S2_ORIGIN = date(2018, 12, 31)  # GLAD-S2 dates are days since this day


def yyddd(d: date) -> int:
    """``d`` as a RADD date: two-digit year * 1000 + day of year."""
    return (d.year % 100) * 1000 + d.timetuple().tm_yday


def yyyyddd(d: date) -> int:
    """``d`` as an engine date: year * 1000 + day of year."""
    return d.year * 1000 + d.timetuple().tm_yday


def year_periods(start: date, end: date) -> list[tuple[date, date]]:
    """Split the inclusive window [start, end] at every New Year."""
    out, a = [], start
    while a <= end:
        b = min(end, date(a.year, 12, 31))
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


def meta_request(key: str) -> ee.Dictionary:
    """GLAD-L's metadata ``build_image`` needs, unevaluated: UpdResult's band
    list. The other sources' builders read none."""
    if key != "GLAD-L":
        raise ValueError(f"only GLAD-L has catalogue metadata, not {key!r}")
    col = ee.ImageCollection(GLAD_L_UPD)
    # distinct across the collection: its images carry different years
    return ee.Dictionary({"bands": col.aggregate_array("system:band_names").flatten().distinct()})


def _glad_l_periods(meta: dict, win_start: date, win_end: date) -> list[tuple[str, date, date]]:
    """``(collection, a, b)`` for each year of the window that GLAD-L holds.

    UpdResult (the live, revised collection) for the years its bands name, the
    yearly archive otherwise, cut at the archive's last day. Years that no
    collection holds are left out.
    """
    upd_bands = set(meta["bands"])
    out = []
    for a, b in year_periods(win_start, win_end):
        if f"conf{a.year % 100}" in upd_bands:
            out.append((GLAD_L_UPD, a, b))
        elif a.year in GLAD_L_FINAL and a <= GLAD_L_FINAL[a.year][1]:
            source, last = GLAD_L_FINAL[a.year]
            out.append((source, a, min(b, last)))
    return out


def _glad_s2_offsets(win_start: date, win_end: date) -> list[tuple[int, int]]:
    """``(first, add)`` for each calendar year of the window, from its first day.

    A GLAD-S2 day count ``n`` of that year (``n >= first``) is the yyyyddd date
    ``n + add``. The offsets are computed here, on the client: the server-side
    aoi_helper.to_date, ported before, was a day late for most years and gave
    impossible dates such as yyyy000 around New Year.
    """
    out = []
    for a, _ in year_periods(win_start, win_end):
        jan_first = (date(a.year, 1, 1) - GLAD_S2_ORIGIN).days
        out.append(((a - GLAD_S2_ORIGIN).days, a.year * 1000 + 1 - jan_first))
    return out


def _finish(alert: ee.Image, dates: ee.Image, in_window: ee.Image, aoi) -> ee.Image:
    img = alert.rename("alert").addBands(dates.rename("date"))
    return img.updateMask(in_window.And(alert.gt(0))).toInt32().clip(aoi)


def _glad_l(win_start, win_end, aoi, meta):
    bounds = export_bounds(aoi)
    images = []
    for source, a, b in _glad_l_periods(meta, win_start, win_end):
        conf, doy_band = f"conf{a.year % 100}", f"alertDate{a.year % 100}"
        col = (
            ee.ImageCollection(source)
            .filterBounds(bounds)
            # newest image of the year mosaicked on top (not the natural order)
            .sort("system:time_start")
            # UpdResult's images do not all carry every year's bands
            .filter(ee.Filter.listContains("system:band_names", conf))
            .select([conf, doy_band])
            .mosaic()
        )
        doy = col.select(doy_band)
        in_window = doy.gte(a.timetuple().tm_yday).And(doy.lte(b.timetuple().tm_yday))
        alert = col.select(conf).remap([0, 1, 2, 3], [0, 0, 2, 1])
        images.append(_finish(alert, doy.add(a.year * 1000), in_window, aoi))
    if not images:  # no collection holds the window: an image with no alerts
        empty = ee.Image.constant([0, 0]).rename(["alert", "date"]).toInt32()
        return empty.updateMask(0).clip(aoi)
    return ee.ImageCollection.fromImages(images).mosaic()


def _radd_latest(col: ee.ImageCollection) -> ee.ImageCollection:
    """The latest version (largest ``system:time_end``) of each geography in ``col``.

    RADD publishes a full snapshot of each geography (africa, asia, ca, sa)
    every week and keeps the old ones, plus legacy ``radd_*`` images. A mosaic
    of them all would be the union of every alert ever published, including
    unconfirmed alerts RADD later withdrew, and the legacy images would sit on
    top outside South America (``system:index`` order). The latest snapshot
    still holds the confirmed alerts back to 2019. Sorted oldest first, so
    where two geographies overlap the more recent one is mosaicked on top.
    """
    latest = col.aggregate_array("geography").distinct().map(
        lambda g: col.filter(ee.Filter.eq("geography", g)).sort("system:time_end", False).first()
    )
    return ee.ImageCollection.fromImages(latest).sort("system:time_end")


def _radd(win_start, win_end, aoi, meta):
    col = _radd_latest(
        ee.ImageCollection(RADD_COLLECTION)
        .filterBounds(export_bounds(aoi))
        .filterMetadata("layer", "contains", "alert")
    ).mosaic()
    d = col.select("Date")  # yyddd
    in_window = d.gte(yyddd(win_start)).And(d.lte(yyddd(win_end)))
    alert = col.select("Alert").remap([0, 1, 2, 3], [0, 0, 2, 1])
    return _finish(alert, d.add(2_000_000), in_window, aoi)


def _glad_s2(win_start, win_end, aoi, meta):
    alert_raw = ee.Image(GLAD_S2 + "/alert").selfMask()
    days = ee.Image(GLAD_S2 + "/alertDate").selfMask()
    lo = (win_start - GLAD_S2_ORIGIN).days
    hi = (win_end - GLAD_S2_ORIGIN).days
    in_window = days.gte(lo).And(days.lte(hi))
    offsets = _glad_s2_offsets(win_start, win_end)
    dates = days.add(offsets[0][1])
    for first, add in offsets[1:]:
        dates = dates.where(days.gte(first), days.add(add))
    alert = alert_raw.remap([0, 1, 2, 3, 4], [0, 2, 2, 1, 1])
    return _finish(alert, dates, in_window, aoi)


_BUILDERS = {"GLAD-L": _glad_l, "RADD": _radd, "GLAD-S2": _glad_s2}


def build_image(key: str, win_start: date, win_end: date, aoi, meta: dict) -> ee.Image:
    """``key``'s alerts in the inclusive window [win_start, win_end] on the contract.

    ``aoi`` is an ``ee.FeatureCollection`` or an ``ee.Geometry``; ``meta`` is
    ``meta_request("GLAD-L")`` evaluated for GLAD-L, and is not read for the others.
    """
    try:
        builder = _BUILDERS[key]
    except KeyError:
        raise ValueError(f"unknown source {key!r}") from None
    return builder(win_start, win_end, aoi, meta)


# --- custom Earth Engine sources: the three format presets (spec §7.2) -------

_MONTH_STARTS = [1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335]


def encoding_for(src) -> str:
    return PRESETS[src.format]["encoding"] or src.date_encoding


def _ee_leap(year):
    return year.mod(4).eq(0).And(year.mod(100).neq(0).Or(year.mod(400).eq(0)))


def ee_yyyyddd(band: ee.Image, encoding: str) -> ee.Image:
    """``presets.to_yyyyddd`` on Earth Engine: one band in, ``yyyyddd`` out, masked where invalid."""
    v = band.toDouble()
    if encoding == "decimal_year":
        year = v.floor()
        diy = ee.Image(365).add(_ee_leap(year))
        doy = v.subtract(year).multiply(diy).add(0.05).floor().min(diy.subtract(1)).add(1)
        valid = v.gt(0)
    else:
        iv = v.floor()
        if encoding == "yyddd":
            year, doy = iv.divide(1000).floor().add(2000), iv.mod(1000)
            valid = iv.gt(0).And(doy.gte(1)).And(doy.lte(ee.Image(365).add(_ee_leap(year))))
        elif encoding == "yyyyddd":
            year, doy = iv.divide(1000).floor(), iv.mod(1000)
            valid = year.gte(1).And(doy.gte(1)).And(doy.lte(ee.Image(365).add(_ee_leap(year))))
        elif encoding == "yymm":
            year, month = iv.divide(100).floor().add(2000), iv.mod(100).toInt()
            valid = iv.gt(0).And(month.gte(1)).And(month.lte(12))
            doy = month.remap(list(range(1, 13)), _MONTH_STARTS, 1).add(month.gt(2).And(_ee_leap(year)))
        else:
            raise ValueError(f"unknown date encoding {encoding!r}")
    return year.multiply(1000).add(doy).updateMask(valid).rename("date")


def _ee_codes(alert: ee.Image, fmt: str) -> ee.Image:
    if fmt == "radd":
        return alert.remap([2, 3], [2, 1], 0)
    if fmt == "ccdc":
        return alert.gte(3).add(alert.gte(1).And(alert.lt(3)).multiply(2))
    raise ValueError(f"the {fmt!r} preset has no alert band")


def preset_image(img: ee.Image, src, win_start: date, win_end: date, aoi) -> ee.Image:
    """A custom source's mapped bands -> the contract (spec §7.2), windowed by date per pixel."""
    dates = ee_yyyyddd(img.select(str(src.band_map.date)), encoding_for(src))
    codes = dates.gt(0) if src.format == "date_only" else _ee_codes(img.select(str(src.band_map.alert)), src.format)
    in_window = dates.gte(yyyyddd(win_start)).And(dates.lte(yyyyddd(win_end)))
    return _finish(codes, dates, in_window, aoi)


def custom_image(src, aoi) -> ee.Image:
    """The mapped bands of a custom Earth Engine source, over ``aoi``.

    An image is used as is. A collection is filtered to the AOI bounds and to
    the images that carry every mapped band (RADD's own collection mixes
    forest-baseline images without them), sorted by ``system:time_start``
    when every image has it (newest on top), and mosaicked. Never
    ``filterDate``: SEPAL exports carry no timestamps; the window is applied
    per pixel on the date band.
    """
    bands = [str(b) for b in mapped_bands(src)]
    if src.asset_type == "IMAGE":
        return ee.Image(src.ref).select(bands)
    col = ee.ImageCollection(src.ref).filterBounds(export_bounds(aoi))
    for b in bands:
        col = col.filter(ee.Filter.listContains("system:band_names", b))
    col = col.select(bands)
    timed = col.filter(ee.Filter.notNull(["system:time_start"]))
    ordered = ee.ImageCollection(ee.Algorithms.If(timed.size().eq(col.size()), col.sort("system:time_start"), col))
    return ordered.mosaic()


def build_custom_image(src, win_start: date, win_end: date, aoi) -> ee.Image:
    return preset_image(custom_image(src, aoi), src, win_start, win_end, aoi)
