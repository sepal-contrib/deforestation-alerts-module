"""The date band's value range over the AOI and the encoding it suggests
(gui/scripts/date_probes.py; spec 2026-10-23-source-form Goal 6)."""

import numpy as np
import pytest
import rasterio
from affine import Affine

from gui.scripts import date_probes as dp

AOI = (-56.0, -26.0, -54.0, -24.0)  # covers every test file


def _tif(path, data, *, nodata=None):
    """A WGS84 GeoTIFF of one band ``data`` with its top-left at (-55, -25), 0.001° pixels."""
    data = np.asarray(data)
    h, w = data.shape
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1, dtype=data.dtype,
                       crs="EPSG:4326", transform=Affine(0.001, 0, -55.0, 0, -0.001, -25.0), nodata=nodata) as dst:
        dst.write(data, 1)
    return path


# ------------------------------------------------------------ the encoding guess
@pytest.mark.parametrize("lo, hi, encoding", [
    (2501, 2510, "yymm"),
    (1001, 9912, "yymm"),
    (25003, 25296, "yyddd"),
    (10001, 99366, "yyddd"),
    (2025032, 2025301, "yyyyddd"),
    (1990001, 2100366, "yyyyddd"),
    (2025.02, 2025.81, "decimal_year"),
    (1990.5, 2099.9, "decimal_year"),
    (2025.0, 2025.5, "decimal_year"),  # one end a fraction: not whole numbers
])
def test_guess_encoding_reads_the_range(lo, hi, encoding):
    assert dp.guess_encoding(lo, hi) == encoding


@pytest.mark.parametrize("lo, hi", [
    (0, 87),  # not dates
    (2513, 2601),  # month 13
    (2500, 2510),  # month 0
    (25003, 25400),  # day 400
    (25000, 25100),  # day 0
    (2025032, 2025367),  # day 367
    (1989001, 2025001),  # before 1990
    (2025.0, 2025.0),  # whole, 4 digits: month 25
    (1989.5, 2025.5),  # a fraction before 1990
    (2025.5, 2100.5),  # a fraction after 2100
    (0.5, 0.9),
    (99367, 100001),  # between the yyddd and yyyyddd ranges
    (2101001, 2101100),  # after 2100
])
def test_guess_encoding_refuses_what_does_not_read_as_dates(lo, hi):
    assert dp.guess_encoding(lo, hi) is None


# ------------------------------------------------------------ Earth Engine info
def test_date_range_from_info():
    assert dp.date_range_from_info({"min": 25003, "max": 25296}) == (25003.0, 25296.0)
    assert dp.date_range_from_info({"min": None, "max": None}) is None  # no positive value
    assert dp.date_range_from_info({}) is None


# ------------------------------------------------------------------- GeoTIFF
def test_file_date_range_skips_zeros(tmp_path):
    data = np.zeros((20, 20), dtype="int16")
    data[:10, :10] = np.arange(2501, 2511, dtype="int16")
    assert dp.file_date_range(_tif(tmp_path / "d.tif", data), 1, AOI) == (2501.0, 2510.0)


def test_file_date_range_of_only_zeros_is_none(tmp_path):
    assert dp.file_date_range(_tif(tmp_path / "d.tif", np.zeros((10, 10), dtype="int16")), 1, AOI) is None


def test_file_date_range_skips_negatives_and_nodata(tmp_path):
    data = np.full((10, 10), -9999, dtype="int32")
    data[0, :5] = [-5, 0, 25003, 25296, 30000]
    path = _tif(tmp_path / "d.tif", data, nodata=30000)
    assert dp.file_date_range(path, 1, AOI) == (25003.0, 25296.0)


def test_file_date_range_skips_an_untagged_float32_fill(tmp_path):
    data = np.full((10, 10), np.finfo("float32").max, dtype="float32")
    data[0, :5] = [2025.1, 2025.3, 2025.5, np.nan, 0.0]
    lo, hi = dp.file_date_range(_tif(tmp_path / "d.tif", data), 1, AOI)
    assert lo == pytest.approx(2025.1) and hi == pytest.approx(2025.5)


def test_file_date_range_outside_the_aoi_is_none(tmp_path):
    path = _tif(tmp_path / "d.tif", np.full((10, 10), 2501, dtype="int16"))
    assert dp.file_date_range(path, 1, (10.0, 40.0, 11.0, 41.0)) is None


def test_file_date_range_reads_every_pixel_of_a_large_file(tmp_path):
    """Two dated pixels in 3000x20 zeros, where a 1024-px nearest read skips both."""
    data = np.zeros((20, 3000), dtype="uint16")
    data[2, 2], data[16, 2999] = 2507, 2503  # rows a 7x1024 read skips
    path = _tif(tmp_path / "d.tif", data)
    assert dp.file_date_range(path, 1, (-56.0, -26.0, -51.0, -24.0)) == (2503.0, 2507.0)


def test_file_date_range_reads_only_the_aoi_window(tmp_path):
    data = np.zeros((20, 3000), dtype="uint16")
    data[5, 100], data[5, 2500] = 2501, 2512  # the second one east of the AOI
    path = _tif(tmp_path / "d.tif", data)
    assert dp.file_date_range(path, 1, (-56.0, -26.0, -54.0, -24.0)) == (2501.0, 2501.0)


def test_date_range_request_refuses_a_type_that_is_not_a_raster():
    with pytest.raises(ValueError, match="TABLE"):
        dp.date_range_request("users/x/t", "TABLE", "d", AOI)


# ------------------------------------------------------- Earth Engine (live tier)
BOUNDS = (-55.10, -10.10, -55.00, -10.00)


def _dates():
    """``d``: 0 in the west, 2501 + one per 0.01° of longitude in the middle, a
    float32 fill in the east; 1 km pixels (about 11 columns over ``BOUNDS``)."""
    import ee

    lon = ee.Image.pixelLonLat().select("longitude")
    d = lon.add(55.10).multiply(100).floor().add(2501)
    d = d.where(lon.lt(-55.07), 0).where(lon.gt(-55.02), float(np.finfo("float32").max))
    return d.float().rename("d").reproject("EPSG:4326", None, 1000)


def test_the_date_range_of_an_image_reads_its_positive_values(gee):
    info = dp.date_range_request(_dates(), "IMAGE", "d", BOUNDS).getInfo()
    lo, hi = dp.date_range_from_info(info)
    assert 2503 <= lo < hi <= 2509  # no 0, no fill
    assert dp.guess_encoding(lo, hi) == "yymm"


def test_the_date_range_of_a_collection_reads_its_mosaic_at_the_band_scale(gee):
    """The first image lacks the band; the mosaic of the others is read at the
    scale of the first one carrying it (1 km), not the mosaic's 1 degree,
    which over ``BOUNDS`` gives one value at most."""
    import ee

    col = ee.ImageCollection([ee.Image(9).rename("x"), _dates()])
    lo, hi = dp.date_range_from_info(dp.date_range_request(col, "IMAGE_COLLECTION", "d", BOUNDS).getInfo())
    assert 2503 <= lo < hi <= 2509


def test_a_collection_with_no_image_over_the_aoi_has_no_range(gee):
    import ee

    away = _dates().clip(ee.Geometry.Rectangle([10.0, 40.0, 11.0, 41.0]))
    col = ee.ImageCollection([away.set("system:footprint", ee.Geometry.Rectangle([10.0, 40.0, 11.0, 41.0]))])
    info = dp.date_range_request(col, "IMAGE_COLLECTION", "d", BOUNDS).getInfo()
    assert info == {"min": None, "max": None}
    assert dp.date_range_from_info(info) is None


def test_the_date_read_is_exact_and_never_best_effort(gee):
    """At the band's own scale, no ``bestEffort`` (a coarser scale averages the dates)."""
    import json

    graph = json.dumps(json.loads(dp.date_range_request(_dates(), "IMAGE", "d", BOUNDS).serialize()))
    assert "bestEffort" not in graph
    assert '"tileScale": {"constantValue": 4}' in graph
    assert '"maxPixels": {"constantValue": 1000000000' in graph


def test_an_image_with_no_positive_value_has_no_range(gee):
    import ee

    empty = ee.Image(0).rename("d").reproject("EPSG:4326", None, 1000)
    info = dp.date_range_request(empty, "IMAGE", "d", BOUNDS).getInfo()
    assert info == {"min": None, "max": None}
    assert dp.date_range_from_info(info) is None


def test_the_date_range_of_radd_reads_as_yyddd(gee):
    from deforestation_alerts.engine import catalogue

    req = dp.date_range_request(catalogue.RADD_COLLECTION, "IMAGE_COLLECTION", "Date", BOUNDS)
    lo, hi = dp.date_range_from_info(req.getInfo())
    assert dp.guess_encoding(lo, hi) == "yyddd"
