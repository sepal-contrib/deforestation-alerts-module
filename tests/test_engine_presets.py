from datetime import date, timedelta

import numpy as np
import pytest

from deforestation_alerts.engine import presets


def _every_day(first=2015, last=2030):
    d = date(first, 1, 1)
    while d.year <= last:
        yield d
        d += timedelta(days=1)


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("offset", [0.0, 0.5])  # start of day, midday
def test_ccdc_decimal_year_is_exact_for_every_day_2015_2030(dtype, offset):
    days = list(_every_day())
    diy = np.array([366 if presets.is_leap(d.year) else 365 for d in days])
    doy = np.array([d.timetuple().tm_yday for d in days])
    years = np.array([d.year for d in days])
    encoded = (years + (doy - 1 + offset) / diy).astype(dtype)
    got = presets.to_yyyyddd(encoded, "decimal_year")
    assert (got == years * 1000 + doy).all()


def test_yymm_is_the_first_of_the_month_and_bad_months_are_dropped():
    got = presets.to_yyyyddd(np.array([2401, 2403, 2412, 2501, 2413, 2400, 0, -5]), "yymm")
    assert list(got) == [2024001, 2024061, 2024336, 2025001, 0, 0, 0, 0]  # 2024 is a leap year


def test_yyddd_and_yyyyddd_validity():
    assert list(presets.to_yyyyddd(np.array([24366, 25366, 25001, 0]), "yyddd")) == [2024366, 0, 2025001, 0]
    assert list(presets.to_yyyyddd(np.array([2024366, 2025366, 2025045]), "yyyyddd")) == [2024366, 0, 2025045]
    assert list(presets.to_yyyyddd(np.array([np.nan, 2025.5]), "decimal_year"))[0] == 0


def test_alert_codes():
    assert list(presets.alert_codes(np.array([0, 1, 2, 3, 4]), "radd")) == [0, 0, 2, 1, 0]
    assert list(presets.alert_codes(np.array([0, 1, 2, 3, 7, np.nan]), "ccdc")) == [0, 2, 2, 1, 1, 0]


def test_apply_preset_keeps_both_window_ends():
    dates = np.array([25031, 25032, 25060, 25061, 25045])
    alert = np.array([3, 2, 3, 3, 0])
    a, d = presets.apply_preset(alert, dates, "radd", "yyddd", 2025032, 2025060)
    assert list(a) == [0, 2, 1, 0, 0] and list(d) == [0, 2025032, 2025060, 0, 0]
    assert a.dtype == np.int32 and d.dtype == np.int32


def test_date_only_alerts_are_all_confirmed():
    a, d = presets.apply_preset(None, np.array([2401, 2413, 2402]), "date_only", "yymm", 2024001, 2024366)
    assert list(a) == [1, 0, 1] and list(d) == [2024001, 0, 2024032]


@pytest.mark.parametrize("encoding", presets.ENCODINGS)
def test_float32_nodata_is_no_date_without_a_cast_warning(encoding):
    import warnings

    big = float(np.finfo(np.float32).max)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        got = presets.to_yyyyddd(np.array([big, -big], dtype=np.float32), encoding)
    assert list(got) == [0, 0]
