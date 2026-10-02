"""masks: the non-zero pixel rule and a mask aligned onto the project grid
(spec §7.2): one chosen band, float rasters with NaN no-data, Int32 0/1,
cleared outside the AOI raster, on-pixels counted."""

import numpy as np
import rasterio
from affine import Affine

from deforestation_alerts.engine import masks, raster_io
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=30)
T = Affine(10, 0, 500000, 0, -10, 8900000)


def _tif(path, bands, dtype, nodata=None):
    with rasterio.open(path, "w", driver="GTiff", width=GRID.width, height=GRID.height, count=len(bands),
                       dtype=dtype, crs=GRID.epsg, transform=T, nodata=nodata) as dst:
        for i, b in enumerate(bands, start=1):
            dst.write(np.asarray(b, dtype=dtype), i)
    return path


def test_the_non_zero_rule():
    v = np.array([0, 1, -5, 0.4, np.nan, 255])
    assert list(masks.mask_on_pixels(v, 255)) == [0, 1, 1, 1, 0, 0]
    assert list(masks.mask_on_pixels(np.array([0, 3]), None)) == [0, 1]


def test_a_float32_mask_with_nan_nodata_aligns(tmp_path):
    prob = np.zeros((GRID.height, GRID.width), "float32")
    prob[:, :20] = 0.7  # not truncated to 0 by a cast
    prob[:5] = np.nan
    src = _tif(tmp_path / "prob.tif", [prob], "float32", nodata=np.nan)
    stats = masks.align_mask(src, GRID, tmp_path / "m.tif")
    with rasterio.open(tmp_path / "m.tif") as r:
        m = r.read(1)
        assert r.dtypes == ("int32",)
    assert m[10, 5] == 1 and m[10, 30] == 0 and m[2, 5] == 0
    assert stats == {"on_pixels": int((m > 0).sum())} and stats["on_pixels"] == 25 * 20


def test_one_band_of_a_multiband_file_with_255_nodata(tmp_path):
    a = np.zeros((GRID.height, GRID.width), "uint8")
    b = np.full((GRID.height, GRID.width), 255, "uint8")
    b[:, :10] = 40  # a class code: non-zero, so on
    src = _tif(tmp_path / "three.tif", [a, b, a], "uint8", nodata=255)
    masks.align_mask(src, GRID, tmp_path / "m.tif", band=2)
    with rasterio.open(tmp_path / "m.tif") as r:
        assert r.count == 1
        m = r.read(1)
    assert m[:, :10].min() == 1 and m[:, 10:].max() == 0


def test_the_aoi_raster_clips_a_geotiff_mask(tmp_path):
    on = _tif(tmp_path / "on.tif", [np.ones((GRID.height, GRID.width))], "uint8")
    aoi = np.zeros((GRID.height, GRID.width), "int32")
    aoi[:, :15] = 1
    with raster_io.write_grid_raster(tmp_path / "aoi.tif", GRID, count=1) as dst:
        dst.write(aoi, 1)
    stats = masks.align_mask(on, GRID, tmp_path / "m.tif", aoi_raster=tmp_path / "aoi.tif")
    with rasterio.open(tmp_path / "m.tif") as r:
        assert r.read(1)[:, 15:].max() == 0
    assert stats["on_pixels"] == GRID.height * 15


def test_a_mask_that_misses_the_grid_has_no_on_pixels(tmp_path):
    far = tmp_path / "far.tif"
    with rasterio.open(far, "w", driver="GTiff", width=10, height=10, count=1, dtype="uint8", crs=GRID.epsg,
                       transform=Affine(10, 0, 900000, 0, -10, 8000000)) as dst:
        dst.write(np.ones((10, 10), "uint8"), 1)
    assert masks.align_mask(far, GRID, tmp_path / "m.tif") == {"on_pixels": 0}
