"""align.reproject_to_grid: a raster in its native projection onto the project grid.

Nearest-exact (codes and dates are never blended), on the engine raster
contract, and streamed: the warp must never hold the output grid in memory
(spatial_risk's harmonize-all OOM, guarded by its test_xr_reproject_streaming).
"""

import tracemalloc

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer
from rasterio.windows import Window

from deforestation_alerts.engine import align, raster_io
from deforestation_alerts.engine.grid import build_grid

# A 60 x 50 px EPSG:4326 source at 0.0005 deg (~55 m), in UTM zone 21S.
RES = 0.0005
X0, Y0 = -57.01, -9.96
SRC_W, SRC_H = 60, 50
# A 10 m grid over most of the source that runs past its southern edge.
GRID = build_grid((-57.005, -9.99, -56.99, -9.965), "EPSG:32721", 10)


def _source(path):
    """Every source pixel carries a distinct date, so each output pixel tells
    exactly which source pixel it came from; every 7th pixel has no alert."""
    idx = np.arange(1, SRC_W * SRC_H + 1).reshape(SRC_H, SRC_W)
    alert = np.where(idx % 7 == 0, 0, 1 + idx % 2)
    dates = np.where(alert > 0, 2_026_000 + idx, 0)
    profile = dict(driver="GTiff", dtype="int32", nodata=0, count=2, width=SRC_W, height=SRC_H,
                   crs="EPSG:4326", transform=Affine(RES, 0, X0, 0, -RES, Y0))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(alert.astype("int32"), 1)
        dst.write(dates.astype("int32"), 2)
    return alert, dates


# The production chunks put this small grid in one block; 16 px source and 64 px
# destination chunks split it into 5 row bands of 3 blocks, partial ones last,
# so every block is written at its own window.
@pytest.mark.parametrize("src_chunk, dst_chunk", [(align.SRC_CHUNK, align.DST_CHUNK), (16, 64)],
                         ids=["one-block", "5-row-bands"])
def test_reprojection_is_nearest_exact(tmp_path, monkeypatch, src_chunk, dst_chunk):
    monkeypatch.setattr(align, "SRC_CHUNK", src_chunk)
    monkeypatch.setattr(align, "DST_CHUNK", dst_chunk)
    src_alert, src_dates = _source(tmp_path / "src.tif")
    out = tmp_path / "out.tif"
    align.reproject_to_grid(tmp_path / "src.tif", GRID, out)
    with rasterio.open(out) as ds:
        alert, dates = ds.read(1), ds.read(2)

    # Where each output pixel's centre falls in the source, computed exactly.
    cols, rows = np.meshgrid(np.arange(GRID.width), np.arange(GRID.height))
    x = GRID.xmin + (cols + 0.5) * GRID.resolution
    y = GRID.ymax - (rows + 0.5) * GRID.resolution
    lon, lat = Transformer.from_crs(GRID.epsg, "EPSG:4326", always_xy=True).transform(x, y)
    fc, fr = (lon - X0) / RES, (Y0 - lat) / RES
    inside = (fc >= 0) & (fc < SRC_W) & (fr >= 0) & (fr < SRC_H)
    c, r = np.clip(np.floor(fc).astype(int), 0, SRC_W - 1), np.clip(np.floor(fr).astype(int), 0, SRC_H - 1)
    want_alert = np.where(inside, src_alert[r, c], 0)
    want_dates = np.where(inside, src_dates[r, c], 0)
    # GDAL's approximate transformer may move a centre by up to 1/8 output
    # pixel (~0.02 source px here): leave out centres that close to a source edge.
    safe = (np.abs(fc - np.round(fc)) > 0.05) & (np.abs(fr - np.round(fr)) > 0.05)
    assert safe.mean() > 0.8 and (~inside & safe).any()  # some pixels fall south of the source
    np.testing.assert_array_equal(alert[safe], want_alert[safe])
    np.testing.assert_array_equal(dates[safe], want_dates[safe])
    # and nowhere is anything invented: every (alert, date) pair is a source pair
    pairs = set(zip(alert.ravel().tolist(), dates.ravel().tolist()))
    assert pairs <= set(zip(src_alert.ravel().tolist(), src_dates.ravel().tolist())) | {(0, 0)}


def test_output_meets_the_engine_raster_contract(tmp_path):
    _source(tmp_path / "src.tif")
    out = tmp_path / "out.tif.part"  # harmonize writes it as the .part it then renames
    assert align.reproject_to_grid(tmp_path / "src.tif", GRID, out) == out
    raster_io.check_header(out, GRID)
    with rasterio.open(out) as ds:
        assert ds.driver == "GTiff" and ds.count == 2
        assert ds.dtypes == ("int32", "int32") and ds.nodata == 0
        assert ds.profile["tiled"] and ds.block_shapes == [(512, 512), (512, 512)]
        assert ds.compression.value == "DEFLATE"
        assert ds.tags(ns="IMAGE_STRUCTURE").get("PREDICTOR") == "2"


def test_a_source_with_its_own_nodata_becomes_nodata_zero(tmp_path):
    src = tmp_path / "src.tif"
    data = np.full((SRC_H, SRC_W), 255, dtype="uint8")  # no-data ...
    data[20:30, :] = 1  # ... but for a band across the grid
    profile = dict(driver="GTiff", dtype="uint8", nodata=255, count=1, width=SRC_W, height=SRC_H,
                   crs="EPSG:4326", transform=Affine(RES, 0, X0, 0, -RES, Y0))
    with rasterio.open(src, "w", **profile) as dst:
        dst.write(data, 1)
    out = tmp_path / "out.tif"
    align.reproject_to_grid(src, GRID, out)
    with rasterio.open(out) as ds:
        assert ds.dtypes == ("int32",) and ds.nodata == 0
        assert set(np.unique(ds.read(1))) == {0, 1}


# ---- streaming guards (spatial_risk tests/test_xr_reproject_streaming.py) ----
#
# With rioxarray's default lock=None the whole grid is evaluated into one numpy
# array before the first write, so the peak grows with the grid's *area*. One
# dask.array.store over the whole grid (rio.to_raster with lock=True) still
# grew with the area at scale (2.2 GB RSS for a 24k px grid, 3.9 GB for 32k),
# so align stores one destination row band at a time and the peak follows only
# the grid's *width* (the band's strip of source tiles). The guards warp the
# same synthetic source at several sizes with 512 px chunks, so every size
# reaches the steady state cheaply (the chunk size moves the constant, not the
# growth). Measured 2026-09-30, 2 workers, traced numpy peak:
#   2x wider (2000 -> 4000 px square, 125 MB output): 33-37 -> 51-53 MB band by
#     band, 41-43 -> 79 MB with one store, 64 -> 251 MB with lock=None;
#   4x taller (2000 x 2000 -> 2000 x 8000 px, 100 m): 26 -> 26 MB band by band,
#     26-27 -> 47 MB with one store, 78 -> 316 MB with lock=None.


def _sparse_source(path, width, height):
    """A ``width`` x ``height`` px 2-band Int32 alert raster at 0.001 deg, alerts on every 97th row."""
    profile = dict(driver="GTiff", dtype="int32", nodata=0, count=2, width=width, height=height,
                   crs="EPSG:4326", transform=Affine(0.001, 0, -66.0, 0, -0.001, -14.0),
                   tiled=True, blockxsize=512, blockysize=512, compress="deflate")
    with rasterio.open(path, "w", **profile) as dst:
        for row0 in range(0, height, 512):
            h = min(512, height - row0)
            rows = np.arange(row0, row0 + h)[:, None]
            alert = np.broadcast_to(np.where(rows % 97 == 0, 1, 0), (h, width)).astype("int32")
            window = Window(0, row0, width, h)
            dst.write(alert, 1, window=window)
            dst.write(alert * 2026001, 2, window=window)


def _traced_peak(tmp_path, width, height, resolution=111):
    """Peak traced (numpy) allocation while aligning a ``width`` x ``height`` px
    source onto a UTM grid over the same footprint: rotated, so every
    destination tile pulls from several source tiles -- the shape of a real
    harmonize."""
    src = tmp_path / f"src{width}x{height}.tif"
    _sparse_source(src, width, height)
    grid = build_grid((-66.0, -14.0 - height * 0.001, -66.0 + width * 0.001, -14.0), "EPSG:32720", resolution)
    out = tmp_path / f"out{width}x{height}.tif"
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        align.reproject_to_grid(src, grid, out)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    with rasterio.open(out) as ds:
        r, c = ds.height // 2, ds.width // 2
        assert ds.read(1, window=Window(c, r, 256, 256)).max() == 1
    return peak, grid.width * grid.height * 2 * 4  # two Int32 bands


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_NUM_THREADS", "2")
    monkeypatch.setattr(align, "SRC_CHUNK", 512)
    monkeypatch.setattr(align, "DST_CHUNK", 512)
    # one GDAL thread whatever earlier tests left behind (a config option, not setenv)
    with rasterio.Env(GDAL_NUM_THREADS=1):
        yield


def test_warp_streams_instead_of_holding_the_output(tmp_path, small_chunks):
    """Doubling the grid's width, not its area, is what the warp's peak follows."""
    peak_small, _ = _traced_peak(tmp_path, 2000, 2000)
    peak_large, out_large = _traced_peak(tmp_path, 4000, 4000)
    assert peak_large < 2.5 * peak_small, (
        f"peak grew {peak_small / 1e6:.0f} MB -> {peak_large / 1e6:.0f} MB for 4x the area: "
        "the warp is materialising the grid, not streaming it"
    )
    assert peak_large < out_large, f"peak {peak_large / 1e6:.0f} MB >= the {out_large / 1e6:.0f} MB output"


def test_warp_peak_does_not_grow_with_the_grid_height(tmp_path, small_chunks):
    """Four times taller at the same width: the same peak, because the grid is
    stored one row band at a time.

    At 100 m a destination tile (51 km) is smaller than a source tile (53-57
    km), so none straddles more than 2 x 2 source tiles and the peak is the
    band's strip. At 111 m the tiles are the same size, some straddle 3 x 2 or
    3 x 3, and the peak follows which of those the scheduler runs together
    (1.1-1.5x here, run to run).
    """
    peak_short, _ = _traced_peak(tmp_path, 2000, 2000, resolution=100)
    peak_tall, _ = _traced_peak(tmp_path, 2000, 8000, resolution=100)
    assert peak_tall < 1.3 * peak_short, (
        f"peak grew {peak_short / 1e6:.0f} MB -> {peak_tall / 1e6:.0f} MB for a 4x taller grid: "
        "the warp is not stored band by band"
    )


@pytest.mark.parametrize("resampling", ["bilinear", "average"])
def test_only_nearest_resampling_is_accepted(tmp_path, resampling):
    _source(tmp_path / "src.tif")
    with pytest.raises(ValueError, match="nearest"):
        align.reproject_to_grid(tmp_path / "src.tif", GRID, tmp_path / "out.tif", resampling=resampling)
