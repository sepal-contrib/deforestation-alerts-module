"""The three format presets of a custom alert source, in numpy (spec §7.2).

GeoTIFF sources are converted with these functions (``normalize.py``); the
Earth Engine twins live in ``catalogue`` (``ee_yyyyddd``, ``preset_image``) and
must give the same codes and dates. Dates become the engine's integer
``yyyyddd``; invalid dates are 0, which means "no alert". The twins agree
after windowing only: ``to_yyyyddd`` turns |value| >= 1e7 (float32 no-data)
into 0 and ``ee_yyyyddd`` does not, but no such value falls in a date window.

The CCDC conversion adds a 0.05-day tolerance (about an hour) inside the
floor: the literal ``floor(frac(y) * diy)`` put half of all start-of-day
encodings one day early (float rounding), in float32 and float64. The cap at
``diy - 1`` keeps 31 December from becoming day 366 of a 365-day year. An
end-of-day encoding (``doy / diy``) would come out one day late (spec §15).
"""

import numpy as np

FORMATS = ("radd", "ccdc", "date_only")
ENCODINGS = ("yyddd", "yymm", "yyyyddd", "decimal_year")
TOLERANCE_DAYS = 0.05
#: day of year of each month's first day in a non-leap year, indexed by month 1..12
_MONTH_START = np.array([0, 1, 32, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335], dtype=np.int64)


def is_leap(year):
    year = np.asarray(year)
    return (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))


def days_in_year(year):
    return np.where(is_leap(year), 366, 365)


def to_yyyyddd(values, encoding: str) -> np.ndarray:
    """Dates in ``encoding`` -> int64 ``yyyyddd``, 0 where invalid (NaN, <= 0,
    |value| >= 1e7 such as float32 no-data, a day of year outside its year, a
    month outside 1..12). The bound comes before the integer cast, which would
    otherwise overflow (and warn) on +-3.4e38."""
    v = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(v)
    ok = finite & (np.abs(v) < 1e7)
    v0 = np.where(ok, v, 0.0)
    if encoding == "decimal_year":
        year = np.floor(v0).astype(np.int64)
        diy = days_in_year(year)
        frac = v0 - year
        doy = 1 + np.minimum(np.floor(frac * diy + TOLERANCE_DAYS), diy - 1).astype(np.int64)
        valid = ok & (v0 > 0)
    else:
        iv = np.floor(v0).astype(np.int64)
        if encoding == "yyddd":
            year, doy = 2000 + iv // 1000, iv % 1000
            valid = ok & (iv > 0) & (doy >= 1) & (doy <= days_in_year(year))
        elif encoding == "yyyyddd":
            year, doy = iv // 1000, iv % 1000
            valid = ok & (year >= 1) & (doy >= 1) & (doy <= days_in_year(year))
        elif encoding == "yymm":
            year, month = 2000 + iv // 100, iv % 100
            valid = ok & (iv > 0) & (month >= 1) & (month <= 12)
            m = np.clip(month, 1, 12)
            doy = _MONTH_START[m] + ((m > 2) & is_leap(year)).astype(np.int64)
        else:
            raise ValueError(f"unknown date encoding {encoding!r}")
    return np.where(valid, year * 1000 + doy, 0).astype(np.int64)


def alert_codes(alert, fmt: str) -> np.ndarray:
    """Preset alert values -> 1 confirmed, 2 probable, 0 none."""
    a = np.asarray(alert, dtype=np.float64)
    a = np.where(np.isfinite(a), a, 0.0)
    if fmt == "radd":
        return np.where(a == 3, 1, np.where(a == 2, 2, 0)).astype(np.int32)
    if fmt == "ccdc":
        return np.where(a >= 3, 1, np.where(a >= 1, 2, 0)).astype(np.int32)
    raise ValueError(f"the {fmt!r} preset has no alert band")


def apply_preset(alert, dates, fmt: str, encoding: str, win_start: int, win_end: int):
    """Both contract bands for the inclusive window ``[win_start, win_end]`` (yyyyddd)."""
    d = to_yyyyddd(dates, encoding)
    if fmt == "date_only":
        codes = (d > 0).astype(np.int32)
    else:
        codes = alert_codes(alert, fmt)
    keep = (codes > 0) & (d > 0) & (d >= win_start) & (d <= win_end)
    return np.where(keep, codes, 0).astype(np.int32), np.where(keep, d, 0).astype(np.int32)


def has_probable(source) -> bool:
    """Whether the source's alerts have a probable (unconfirmed) level: False
    only for a custom source whose format is ``date_only``."""
    return getattr(source, "format", None) != "date_only"
