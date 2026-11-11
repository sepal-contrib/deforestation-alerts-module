"""Tier 2 (live Earth Engine): a v1 admin AOI resolves through GAUL 2015, not 2024. Skips without credentials."""

import pytest
from shapely.geometry import shape
from shapely.ops import unary_union


@pytest.mark.ee
def test_gaul_2015_code_2317_is_canindeyu(gee):
    from gui.scripts.v1_open import resolve_v1_aoi

    fc = resolve_v1_aoi({"aoi_method": "ADMIN1", "aoi_admin": "2317"})
    minx, miny, maxx, maxy = unary_union([shape(f["geometry"]) for f in fc["features"]]).bounds
    assert -56.5 <= minx and maxx <= -54 and -25.5 <= miny and maxy <= -23.5  # Paraguay, not Iowa (GAUL 2024)
