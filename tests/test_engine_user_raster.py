import numpy as np
import pytest
import rasterio
from affine import Affine

from deforestation_alerts.engine import user_raster as ur
from deforestation_alerts.project.models import BandMap


def _tif(path, crs="EPSG:4326", count=2, dtype="int16", descriptions=None,
         transform=Affine(0.0002, 0, -55.1, 0, -0.0002, -10.0)):
    with rasterio.open(path, "w", driver="GTiff", width=50, height=40, count=count, dtype=dtype, crs=crs,
                       transform=transform) as dst:
        for i in range(1, count + 1):
            dst.write(np.zeros((40, 50), dtype=dtype), i)
        if descriptions:
            dst.descriptions = tuple(descriptions)
    return path


def test_read_header_lists_bands_and_wgs84_bounds(tmp_path):
    h = ur.read_header(_tif(tmp_path / "a.tif", descriptions=("Alert", "Date")))
    assert [b.name for b in h.bands] == ["Alert", "Date"] and [b.index for b in h.bands] == [1, 2]
    assert h.crs == "EPSG:4326" and h.bounds_wgs84 == pytest.approx((-55.1, -10.008, -55.09, -10.0))
    assert all(b.numeric for b in h.bands)


def test_a_file_without_descriptions_lists_band_numbers(tmp_path):
    h = ur.read_header(_tif(tmp_path / "b.tif"))
    assert [b.name for b in h.bands] == ["Band 1", "Band 2"]


def test_file_native_converts_degrees_to_metres(tmp_path):
    n = ur.file_native(ur.read_header(_tif(tmp_path / "c.tif")))
    assert n.crs == "EPSG:4326" and n.scale == pytest.approx(0.0002 * 111319.49079327357)
    assert n.transform == (0.0002, 0.0, -55.1, 0.0, -0.0002, -10.0) and n.bounds is not None
    utm = ur.file_native(ur.read_header(_tif(tmp_path / "d.tif", crs="EPSG:32721",
                                             transform=Affine(30, 0, 500000, 0, -30, 8900000))))
    assert utm.scale == 30.0


def test_check_file(tmp_path):
    h = ur.read_header(_tif(tmp_path / "e.tif"))
    assert ur.check_file(h, [1, 2]) is None
    assert ur.check_file(h, [3]) == "steps.alerts.err_missing_band"
    no_crs = ur.FileHeader(crs=None, transform=h.transform, width=1, height=1, bounds_wgs84=None, bands=h.bands,
                           nodata=None)
    assert ur.check_file(no_crs, [1]) == "steps.alerts.err_no_crs"


def test_an_unreadable_file_raises_a_message_key(tmp_path):
    (tmp_path / "x.tif").write_text("not a tiff")
    with pytest.raises(ur.UserRasterError) as e:
        ur.read_header(tmp_path / "x.tif")
    assert e.value.key == "steps.alerts.err_unreadable"


def test_a_complex_band_is_listed_as_not_numeric(tmp_path):
    """GDAL's complex_int16 is no NumPy dtype: the header still reads, and
    check_file refuses the band with its message key."""
    path = tmp_path / "cx.tif"
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=1, dtype="complex_int16",
                       crs="EPSG:4326", transform=Affine(0.0002, 0, -55.1, 0, -0.0002, -10.0)):
        pass  # NumPy has no complex_int16 to write
    h = ur.read_header(path)
    assert [b.numeric for b in h.bands] == [False]
    assert ur.check_file(h, [1]) == "steps.alerts.err_not_numeric"


def test_a_crs_without_a_path_to_wgs84_raises_a_message_key(tmp_path):
    from rasterio.crs import CRS

    local = CRS.from_wkt('LOCAL_CS["arbitrary",UNIT["metre",1],AXIS["Easting",EAST],AXIS["Northing",NORTH]]')
    path = _tif(tmp_path / "local.tif", crs=local, transform=Affine(1, 0, 10, 0, -1, 10))
    with pytest.raises(ur.UserRasterError) as e:
        ur.read_header(path)
    assert e.value.key == "steps.alerts.err_unreadable"


def _info(crs="EPSG:4326", crss=("EPSG:4326",), scale=20.0, scales=(20.0,), count=12, date_format=None,
          types=None, transform=(0.00017966, 0, -56.0, 0, -0.00017966, -23.0)):
    return {"count": count, "crs": crs, "scale": scale, "transform": list(transform), "crss": list(crss),
            "min_scale": min(scales), "date_format": date_format,
            "band_types": types or {"detection_count": {"precision": "int"},
                                    "first_detection_date": {"precision": "float"}}}


def test_native_comes_from_the_first_image_not_the_mosaic():
    n = ur.native_from_discovery(_info())
    assert (n.crs, n.scale, n.transform) == ("EPSG:4326", 20.0, (0.00017966, 0, -56.0, 0, -0.00017966, -23.0))


def test_two_crss_or_a_non_epsg_crs_fall_back_to_4326_at_the_finest_scale():
    n = ur.native_from_discovery(_info(crs="EPSG:32721", crss=("EPSG:32721", "EPSG:32722"), scale=30.0,
                                       scales=(30.0, 10.0)))
    assert (n.crs, n.scale, n.transform) == ("EPSG:4326", 10.0, None)
    n = ur.native_from_discovery(_info(crs="SR-ORG:6974", crss=("SR-ORG:6974",), scale=463.3, scales=(463.3,)))
    assert (n.crs, n.scale) == ("EPSG:4326", 463.3)


@pytest.mark.parametrize("info, key", [
    (_info(count=0), "steps.alerts.err_no_images"),
    ({"count": 0, "asset_type": "IMAGE"}, "steps.alerts.err_missing_band"),  # an image, not a collection
    (_info(date_format=0), "steps.alerts.err_date_format"),
    (_info(date_format=2), "steps.alerts.err_date_format"),
    (_info(types={"detection_count": {"precision": "int"}}), "steps.alerts.err_missing_band"),
])
def test_check_discovery_refusals(info, key):
    assert ur.check_discovery(info, "ccdc", ["detection_count", "first_detection_date"]) == key


def test_check_discovery_accepts_ccdc_without_or_with_date_format_1():
    bands = ["detection_count", "first_detection_date"]
    assert ur.check_discovery(_info(), "ccdc", bands) is None
    assert ur.check_discovery(_info(date_format=1), "ccdc", bands) is None
    assert ur.check_discovery(_info(date_format=0), "radd", bands) is None  # only CCDC reads dateFormat


def test_suggestions():
    assert ur.suggest_format(["alert", "DATE", "constant"]) == "radd"
    assert ur.suggest_format(["detection_count", "first_detection_date"]) == "ccdc"
    assert ur.suggest_format(["available_image_count", "total_changes", "first_change_date_above_threshold",
                              "fcd_decision_map"]) == "pyeo"
    assert ur.suggest_band_map("pyeo", ["total_changes", "first_change_date_above_threshold"]) == BandMap(
        alert="total_changes", date="first_change_date_above_threshold")
    assert ur.suggest_format(["disturbance_date"]) == "date_only"
    assert ur.suggest_band_map("radd", ["date", "ALERT"]) == BandMap(alert="ALERT", date="date")
    assert ur.suggest_band_map("radd", ["Band 1", "Band 2"]) is None
    assert ur.suggest_band_map("date_only", ["disturbance_date"]) == BandMap(alert=None, date="disturbance_date")


@pytest.mark.parametrize("fmt, names, expected", [
    ("ccdc", ["detection_count", "magnitude"], ("detection_count", None)),  # each band found on its own
    ("ccdc", ["magnitude", "FIRST_DETECTION_DATE"], (None, "FIRST_DETECTION_DATE")),
    ("radd", ["alert", "date"], ("alert", "date")),  # the raster's own names, matched case-insensitively
    ("pyeo", ["Band 1", "Band 2"], (None, None)),
    ("date_only", ["magnitude", "change_date", "confidence"], (None, "change_date")),
    ("date_only", ["a_date", "b_date"], (None, None)),  # two candidates: none
    ("date_only", ["Band 1"], (None, None)),
])
def test_suggest_bands_finds_each_band_on_its_own(fmt, names, expected):
    assert ur.suggest_bands(fmt, names) == expected
