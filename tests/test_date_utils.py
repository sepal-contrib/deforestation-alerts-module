import numpy as np

from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date


def test_julian_to_date():
    assert convert_julian_to_date(2023.001) == "2023-01-01"
    assert convert_julian_to_date(2020.060) == "2020-02-29"  # leap day


def test_missing_dates_return_na():
    # vectorize.py returns None for alert_date_min/max when a polygon's date
    # pixels are all nodata; stored in a gdf those become NaN. The Overview
    # marker popups call this directly, so a NaN must NOT raise (it blanked the
    # whole Overview map: "not enough values to unpack (expected 2, got 1)").
    assert convert_julian_to_date(np.nan) == "N/A"
    assert convert_julian_to_date(None) == "N/A"
    assert convert_julian_to_date(float("inf")) == "N/A"
