"""A CNN prediction raster -> its patches, measured in the project's CRS and cut
at the alert set's minimum size (the same ``min_size_ha`` Integrate applies)."""

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from affine import Affine
from shapely.geometry import box

from deforestation_alerts.scripts.mosaics_helper import calculate_total_area, prediction_patches

UTM = "EPSG:32721"  # Paraguay's zone


def _prediction(path, prob, *, crs=UTM, transform=Affine(10, 0, 500_000, 0, -10, 7_350_000)):
    """A 1-band float32 probability raster, as ``apply_dl_model`` writes one."""
    prob = np.asarray(prob, dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", width=prob.shape[1], height=prob.shape[0],
                       count=1, dtype="float32", crs=crs, transform=transform) as dst:
        dst.write(prob, 1)
    return path


def _two_blobs(tmp_path):
    """A 10 x 10 px patch (1 ha at 10 m) and a 2 x 2 px one (0.04 ha); one
    pixel at the threshold itself, which is not a detection."""
    prob = np.zeros((30, 30), np.float32)
    prob[2:12, 2:12] = 0.9
    prob[20:22, 20:22] = 0.5
    prob[25, 5] = 0.20
    return _prediction(tmp_path / "pred.tif", prob)


def test_patches_under_the_minimum_size_are_dropped(tmp_path):
    patches = prediction_patches(_two_blobs(tmp_path), 0.20, crs=UTM, min_size_ha=0.5)
    assert len(patches) == 1
    assert patches.crs == UTM
    assert patches["area_ha"].tolist() == pytest.approx([1.0])


def test_a_minimum_of_zero_keeps_every_patch(tmp_path):
    patches = prediction_patches(_two_blobs(tmp_path), 0.20, crs=UTM, min_size_ha=0.0)
    assert sorted(patches["area_ha"]) == pytest.approx([0.04, 1.0])


def test_a_patch_exactly_at_the_minimum_is_kept(tmp_path):
    """Integrate keeps a cluster of exactly the minimum (``>=``); so does this."""
    patches = prediction_patches(_two_blobs(tmp_path), 0.20, crs=UTM, min_size_ha=0.04)
    assert len(patches) == 2


def test_a_prediction_with_nothing_above_the_threshold_has_no_patches(tmp_path):
    """No placeholder centroid any more: nothing found is an empty frame."""
    path = _prediction(tmp_path / "empty.tif", np.full((8, 8), 0.1))
    patches = prediction_patches(path, 0.20, crs=UTM, min_size_ha=0.0)
    assert len(patches) == 0
    assert patches.crs == UTM


def test_the_area_is_measured_in_the_given_crs_not_web_mercator(tmp_path):
    """The composite is EPSG:3857 at 4.77 m. Its nominal pixel area overstates
    the ground at 24 deg S by 1/cos^2(lat), about 20 %; in the project's UTM
    zone the area is the true one."""
    x, y = -6_122_000.0, -2_753_000.0  # about 55 W, 24 S in EPSG:3857
    path = _prediction(tmp_path / "merc.tif", np.full((100, 100), 0.9), crs="EPSG:3857",
                       transform=Affine(4.77, 0, x, 0, -4.77, y))
    mercator_ha = (100 * 4.77) ** 2 / 10_000
    patches = prediction_patches(path, 0.20, crs=UTM, min_size_ha=0.0)
    assert patches.loc[0, "area_ha"] == pytest.approx(mercator_ha * np.cos(np.radians(24)) ** 2, rel=0.02)


def test_total_area_uses_the_given_crs():
    drawn = gpd.GeoDataFrame(geometry=[box(500_000, 7_340_000, 500_100, 7_340_100)], crs=UTM)
    assert calculate_total_area(drawn.to_crs(4326), UTM) == pytest.approx(1.0, rel=1e-6)


def test_total_area_without_a_crs_uses_the_local_utm_zone():
    drawn = gpd.GeoDataFrame(geometry=[box(500_000, 7_340_000, 500_100, 7_340_100)], crs=UTM)
    assert calculate_total_area(drawn.to_crs(4326)) == pytest.approx(1.0, rel=1e-6)
