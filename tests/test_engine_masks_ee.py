"""Tier 2 (live Earth Engine): a mask downloads natively as one uint8 0/1 band
and aligns onto the grid (spec §7.2). Skips without credentials."""

import pytest

# geedim looks every exported image up in its STAC catalogue and warns when a
# computed image (no asset id) has no entry, and it still multiplies Affines
# with ``*``, which affine 3 deprecates: nothing to act on in either.
pytestmark = [
    pytest.mark.filterwarnings("ignore:Couldn't find STAC entry:RuntimeWarning"),
    pytest.mark.filterwarnings("ignore:Use `@` matmul:PendingDeprecationWarning:geedim"),
]


def test_an_earth_engine_mask_downloads_as_one_band_uint8_and_aligns(gee, tiny_aoi, tmp_path):
    import ee
    import rasterio

    from deforestation_alerts.engine import grid as gridmod
    from deforestation_alerts.engine import masks
    from deforestation_alerts.project.models import MaskLayer, NativeGrid

    # Hansen's tree cover (2000), its 2025 release: earlier releases warn they are superseded
    mask = MaskLayer(name="tc", origin="gee", ref="UMD/hansen/global_forest_change_2025_v1_13",
                     asset_type="IMAGE", band="treecover2000", native=NativeGrid(crs="EPSG:4326", scale=27.83))
    aoi = ee.FeatureCollection([ee.Feature(tiny_aoi)])
    raw = masks.export_mask_native(masks.build_mask_image(mask, aoi), mask.native, aoi, tmp_path / "raw.tif")
    with rasterio.open(raw) as r:
        assert r.count == 1 and r.dtypes == ("uint8",) and set(map(int, set(r.read(1).ravel()))) <= {0, 1}
    bounds = (-55.10, -10.10, -55.00, -10.00)
    grid = gridmod.build_grid(bounds, gridmod.auto_utm_epsg(bounds), 30.0)
    stats = masks.align_mask(raw, grid, tmp_path / "m.tif")
    assert stats["on_pixels"] > 0
