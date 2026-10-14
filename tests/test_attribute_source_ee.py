"""Tier 2: the attribute raster against real Earth Engine. Skips without credentials."""

import pytest

# geedim still multiplies Affines with ``*``, which affine 3 deprecates, and
# warns when an exported image has no STAC entry: nothing to act on in either.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Couldn't find STAC entry:RuntimeWarning"),
    pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning:geedim"),
]


def test_a_sentinel2_attribute_raster_downloads_only_its_included_band(gee, tiny_aoi, tmp_path):
    import ee

    from deforestation_alerts.scripts import attribute_source as asrc
    from deforestation_alerts.scripts import raster_attribute as ra

    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(tiny_aoi)
          .filterDate("2025-06-01", "2025-09-30").first().get("system:id").getInfo())
    cfg = {"source": s2, "kind": "gee", "bands": [{"band": "B11", "stat": "mean", "column": "b11"}]}
    out = asrc.materialize_attribute_bands(cfg, ee.Geometry(tiny_aoi), tmp_path)
    assert len(out) == 1 and ra.band_names(out[0][0]) == ["B11"]
