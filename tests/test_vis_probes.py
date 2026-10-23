"""AOI probes for the visualization form: stretch and find classes (no Earth Engine)."""

import numpy as np
import pytest
import rasterio
from affine import Affine

from gui.scripts import vis_probes as vp


def _tif(path, data, *, nodata=None, dtype=None):
    """A WGS84 GeoTIFF of ``data`` (bands, rows, cols) with its top-left at (-55, -25), 0.001° pixels."""
    data = np.asarray(data)
    if data.ndim == 2:
        data = data[None]
    count, h, w = data.shape
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=count, dtype=dtype or data.dtype,
                       crs="EPSG:4326", transform=Affine(0.001, 0, -55.0, 0, -0.001, -25.0), nodata=nodata) as dst:
        dst.write(data.astype(dtype or data.dtype))
    return path


AOI = (-56.0, -26.0, -54.0, -24.0)  # covers every test file


def test_stretch_from_info_spans_the_three_bands():
    info = {"B4_p2": 120, "B4_p98": 2800, "B3_p2": 90, "B3_p98": 3100, "B2_p2": 200, "B2_p98": 2500}
    assert vp.stretch_from_info(info, ["B4", "B3", "B2"]) == (90.0, 3100.0)
    assert vp.stretch_from_info({"B4_p2": None, "B4_p98": 5}, ["B4"]) is None
    assert vp.stretch_from_info({"B4_p2": 5, "B4_p98": 5}, ["B4"]) is None
    assert vp.stretch_from_info({"B4_p2": 5}, ["B4"]) is None


def test_the_histogram_lists_whole_values_and_reads_the_class_metadata():
    info = {"hist": {"10": 5.0, "80.0": 2.5}, "meta": {"Map_class_values": [10, 80],
            "Map_class_palette": ["006400", "0064c8"], "Map_class_names": ["Tree", "Water"]}}
    assert vp.classes_from_info(info, "Map") == {"values": [10, 80], "known": {
        "values": [10, 80], "palette": ["006400", "0064c8"], "names": ["Tree", "Water"]}}


def test_class_metadata_of_unequal_lengths_is_ignored():
    info = {"hist": {"1": 3}, "meta": {"b_class_values": [1, 2], "b_class_palette": ["ff0000"],
            "b_class_names": ["a", "b"]}}
    assert vp.classes_from_info(info, "b") == {"values": [1], "known": None}
    assert vp.classes_from_info({"hist": None, "meta": {}}, "b") == {"error": "steps.layers.err_no_values"}


@pytest.mark.parametrize("values, key", [([], "steps.layers.err_no_values"), ([0.5, 1], "steps.layers.err_not_integer"),
                                         (list(range(51)), "steps.layers.err_too_many_classes")])
def test_check_values_refuses(values, key):
    assert vp.check_values(values) == {"error": key}


def test_check_values_sorts_whole_values_as_ints():
    assert vp.check_values([3.0, 1, 2.0]) == {"values": [1, 2, 3]}
    assert vp.check_values(list(range(50))) == {"values": list(range(50))}


def test_float_band_with_whole_values_is_categorical(tmp_path):
    data = np.ones((20, 20), dtype="float32")
    data[:, 10:] = 2.0
    path = _tif(tmp_path / "f.tif", data)
    assert vp.file_classes(path, 1, AOI) == {"values": [1, 2], "known": None}


def test_a_float_band_with_fractions_is_refused(tmp_path):
    data = np.full((10, 10), 0.5, dtype="float32")
    path = _tif(tmp_path / "f.tif", data)
    assert vp.file_classes(path, 1, AOI) == {"error": "steps.layers.err_not_integer"}


def test_a_geotiff_outside_the_aoi_reports_no_values(tmp_path):
    path = _tif(tmp_path / "f.tif", np.ones((10, 10), dtype="uint8"))
    far = (10.0, 40.0, 11.0, 41.0)
    assert vp.file_sample(path, [1], far).mask.all()
    assert vp.file_classes(path, 1, far) == {"error": "steps.layers.err_no_values"}
    assert vp.file_stretch(path, [1], far) is None


def test_file_stretch_ignores_nodata(tmp_path):
    data = np.tile(np.arange(100, dtype="uint8"), (100, 1))
    data[:, :30] = 255
    path = _tif(tmp_path / "f.tif", data, nodata=255)
    lo, hi = vp.file_stretch(path, [1], AOI)
    assert 30 <= lo < hi <= 99


def test_file_stretch_spans_the_three_bands(tmp_path):
    rng = np.random.default_rng(0)
    data = np.stack([rng.integers(100, 200, (50, 50)), rng.integers(0, 150, (50, 50)),
                     rng.integers(50, 300, (50, 50))]).astype("int16")
    path = _tif(tmp_path / "f.tif", data)
    lo, hi = vp.file_stretch(path, [1, 2, 3], AOI)
    assert lo < 10 and hi > 280


def test_file_stretch_of_a_flat_band_is_none(tmp_path):
    path = _tif(tmp_path / "f.tif", np.full((10, 10), 7, dtype="uint8"))
    assert vp.file_stretch(path, [1], AOI) is None


def test_the_file_sample_is_decimated(tmp_path):
    path = _tif(tmp_path / "f.tif", np.zeros((3000, 4000), dtype="uint8"))
    sample = vp.file_sample(path, [1], (-56.0, -29.0, -50.0, -24.0))
    assert sample.shape[-2] <= 768 and sample.shape[-1] <= 1024
    assert sample.shape[-2:] == (768, 1024)


def test_the_file_sample_is_cut_to_the_aoi(tmp_path):
    data = np.zeros((100, 100), dtype="uint8")
    data[:, 50:] = 9  # the east half, west edge at -54.95
    path = _tif(tmp_path / "f.tif", data)
    assert vp.file_classes(path, 1, (-56.0, -26.0, -54.96, -24.0)) == {"values": [0], "known": None}


# The histogram comes back trimmed: its size ``n`` and at most MAX_CLASSES + 1 keys.
@pytest.mark.parametrize("info, key", [
    ({"n": 291_752, "hist": {"0": 1, "0.0012": 2, "0.0049": 1}, "meta": {}}, "steps.layers.err_not_integer"),
    ({"n": 60, "hist": {str(i): 1 for i in range(51)}, "meta": {}}, "steps.layers.err_too_many_classes"),
    ({"n": 51, "hist": {str(i): 1 for i in range(50)}, "meta": {}}, "steps.layers.err_too_many_classes"),
    ({"n": 0, "hist": {}, "meta": {}}, "steps.layers.err_no_values"),
])
def test_a_trimmed_histogram_is_refused_on_its_size(info, key):
    assert vp.classes_from_info(info, "b") == {"error": key}


def test_a_trimmed_histogram_within_the_cap_lists_its_values():
    info = {"n": 50, "hist": {str(i): 1 for i in range(50)}, "meta": {}}
    assert vp.classes_from_info(info, "b") == {"values": list(range(50)), "known": None}


def test_the_class_request_trims_the_histogram_on_the_server(gee):
    import ee

    bounds = (-55.10, -10.10, -55.00, -10.00)
    many = (ee.Image.pixelLonLat().select("longitude").multiply(1e4).int().rename("b")
            .reproject("EPSG:4326", None, 100))  # ~110 distinct columns over the bounds
    info = vp.classes_request(many, "b", bounds).getInfo()
    assert info["n"] > vp.MAX_CLASSES + 1
    assert len(info["hist"]) == vp.MAX_CLASSES + 1
    assert vp.classes_from_info(info, "b") == {"error": "steps.layers.err_too_many_classes"}

    empty = ee.Image(1).updateMask(0).rename("b")
    info = vp.classes_request(empty, "b", bounds).getInfo()
    assert info["n"] == 0 and info["hist"] == {}
    assert vp.classes_from_info(info, "b") == {"error": "steps.layers.err_no_values"}
