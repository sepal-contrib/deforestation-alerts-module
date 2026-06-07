"""Tier-1 tests for the local raster→vector pipeline (no GEE, no UI)."""

from deforestation_alerts.scripts.geo_utils import (
    get_utm_proj_str_from_lat_lon,
    optimal_block_shape,
)


def test_utm_proj_northern_and_southern():
    # London ~ (51.5N, 0W) -> UTM 31N -> 32631
    assert get_utm_proj_str_from_lat_lon(0.0, 51.5) == "EPSG:32631"
    # Rio ~ (-22.9S, -43.2W) -> UTM 23S -> 32723
    assert get_utm_proj_str_from_lat_lon(-43.2, -22.9) == "EPSG:32723"


def test_utm_proj_polar_caps():
    assert get_utm_proj_str_from_lat_lon(10.0, 85.0) == "EPSG:5041"   # UPS North
    assert get_utm_proj_str_from_lat_lon(10.0, -82.0) == "EPSG:5042"  # UPS South


def test_optimal_block_shape_bounds_rows_by_ram():
    # 10000x10000, 1 band, 1 GB at 70% -> rows bounded, cols = full width
    rows, cols = optimal_block_shape(10000, 10000, 1, ram_gb=1.0)
    assert cols == 10000
    assert 1 <= rows <= 10000
