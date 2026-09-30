"""GLAD-L, RADD and GLAD-S2 as Earth Engine images on the engine contract.

Contract (spec D16, §7.2): bands ``alert`` (1 confirmed, 2 probable) and
``date`` (integer yyyyddd), Int32, masked where there is no alert and outside
the *inclusive* window [win_start, win_end], clipped to the AOI. The old
``aoi_helper._from_*`` builders used strict gt/lt and dropped both end days.

No builder calls getInfo: the metadata they need comes from ``meta_request``,
which the tile evaluates with ``await gee_interface.get_info_async``.
"""

from datetime import date, timedelta

import ee

from deforestation_alerts.scripts.raster_export import _export_bounds

KEYS = ("GLAD-L", "RADD", "GLAD-S2")
LABEL_KEYS = {
    "GLAD-L": "steps.alerts.label_glad_l",
    "RADD": "steps.alerts.label_radd",
    "GLAD-S2": "steps.alerts.label_glad_s2",
}
COVERAGE_START = {"GLAD-L": date(2018, 1, 1), "RADD": date(2019, 1, 1), "GLAD-S2": date(2019, 1, 1)}
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
    """The metadata ``coverage``, ``covers`` and ``build_image`` need, unevaluated."""
    if key == "GLAD-L":
        col = ee.ImageCollection(GLAD_L_UPD)
        latest = col.sort("system:time_start", False).first()
        return ee.Dictionary({
            # distinct across the collection: its images carry different years
            "bands": col.aggregate_array("system:band_names").flatten().distinct(),
            "end": ee.Date(latest.get("system:time_start")).format("yyyy-MM-dd"),
        })
    if key == "RADD":
        latest = (
            ee.ImageCollection(RADD_COLLECTION)
            .filterMetadata("layer", "contains", "alert")
            .sort("system:time_end", False)
            .first()
        )
        return ee.Dictionary({"end": latest.get("version_date")})
    if key == "GLAD-S2":
        return ee.Dictionary({"end": ee.Image(GLAD_S2 + "/alert").get("date")})
    raise ValueError(f"unknown source {key!r}")


def coverage(key: str, meta: dict) -> tuple[date, date]:
    """The catalogue's first and last day."""
    return COVERAGE_START[key], date.fromisoformat(str(meta["end"])[:10])


def covers(key: str, meta: dict, win_start: date, win_end: date) -> bool:
    """Whether the catalogue holds any day of the window [win_start, win_end]."""
    first, last = coverage(key, meta)
    if win_start > last or win_end < first:
        return False
    return key != "GLAD-L" or bool(_glad_l_periods(meta, win_start, win_end))


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
    bounds = _export_bounds(aoi)
    images = []
    for source, a, b in _glad_l_periods(meta, win_start, win_end):
        conf, doy_band = f"conf{a.year % 100}", f"alertDate{a.year % 100}"
        col = (
            ee.ImageCollection(source)
            .filterBounds(bounds)
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
        .filterBounds(_export_bounds(aoi))
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
    ``meta_request(key)`` evaluated.
    """
    try:
        builder = _BUILDERS[key]
    except KeyError:
        raise ValueError(f"unknown source {key!r}") from None
    return builder(win_start, win_end, aoi, meta)
