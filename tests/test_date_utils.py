from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date


def test_julian_to_date():
    assert convert_julian_to_date(2023.001) == "2023-01-01"
    assert convert_julian_to_date(2020.060) == "2020-02-29"  # leap day
