import os

import numpy as np
import pytest
import rasterio

from deforestation_alerts.engine import raster_io
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=1200, height=900)


def test_write_grid_raster_int32_nodata0_nearest_overviews(tmp_path):
    out = tmp_path / "a.tif"
    data = np.zeros((GRID.height, GRID.width), dtype="int32")
    data[100:110, 200:210] = 222222222  # the largest 9-source code
    # A 2x2 cell of 4 distinct codes: at the first overview level (factor 2)
    # it decimates to one pixel, so only nearest-neighbour resampling can
    # avoid inventing a 5th value (average/bilinear would blend them).
    corner_codes = {1000001, 2000002, 3000003, 4000004}
    data[300, 300] = 1000001
    data[300, 301] = 2000002
    data[301, 300] = 3000003
    data[301, 301] = 4000004
    with raster_io.write_grid_raster(out, GRID, count=1) as dst:
        for w in raster_io.row_windows(GRID, rows=256):
            dst.write(data[w.row_off:w.row_off + w.height, :], 1, window=w)
    assert out.exists() and not (tmp_path / "a.tif.part").exists()
    with rasterio.open(out) as src:
        assert src.dtypes[0] == "int32" and src.nodata == 0
        assert src.overviews(1)  # internal pyramid
        assert src.tags(ns="rio_overview").get("resampling") == "nearest"
        assert src.read(1)[105, 205] == 222222222
        ovr = src.read(1, out_shape=(GRID.height // 2, GRID.width // 2))
        assert set(np.unique(ovr)) <= ({0, 222222222} | corner_codes)  # no invented codes
        assert ovr[150, 150] in corner_codes  # decimated 2x2 cell: one of its own pixels


def test_failed_write_leaves_nothing(tmp_path):
    out = tmp_path / "b.tif"
    with pytest.raises(RuntimeError):
        with raster_io.write_grid_raster(out, GRID, count=1):
            raise RuntimeError("boom")
    assert not out.exists() and not (tmp_path / "b.tif.part").exists()


def test_check_header_rejects_other_grid(tmp_path):
    out = tmp_path / "c.tif"
    with raster_io.write_grid_raster(out, GRID, count=1):
        pass
    raster_io.check_header(out, GRID)
    other = GRID.model_copy(update={"xmin": 500010})
    with pytest.raises(raster_io.GridMismatchError):
        raster_io.check_header(out, other)


def test_ensure_free_space(tmp_path):
    raster_io.ensure_free_space(tmp_path, 1)
    with pytest.raises(raster_io.NotEnoughSpaceError):
        raster_io.ensure_free_space(tmp_path, 10**18)


def test_compressed_estimate_is_the_documented_fraction():
    assert raster_io.COMPRESSED_FRACTION == 0.05  # user decision 2026-10-01
    assert raster_io.compressed_estimate(1_000_000) == 50_000
    assert raster_io.compressed_estimate(0) == 0
    assert raster_io.compressed_estimate(1) == 1  # rounded up, never 0 for a real file
    assert raster_io.compressed_estimate(21) == 2  # 1.05 -> 2
    assert isinstance(raster_io.compressed_estimate(10**12), int)


def test_compressed_estimate_holds_for_dense_clustered_alerts(tmp_path):
    """85 % of the pixels alerted in 16-px patches, one code and date per patch
    (far denser than any real AOI): the file our writer produces, overviews
    included, stays under the estimate."""
    n, k = 1024, 16
    grid = GRID.model_copy(update={"width": n, "height": n})
    rng = np.random.default_rng(0)
    hit = rng.random((n // k, n // k)) < 0.85
    code = np.where(hit, rng.integers(1, 3, hit.shape), 0).astype("int32")
    dates = np.where(hit, 2026001 + rng.integers(0, 120, hit.shape), 0).astype("int32")
    patch = np.ones((k, k), dtype="int32")
    out = tmp_path / "dense.tif"
    with raster_io.write_grid_raster(out, grid, count=2) as dst:
        dst.write(np.kron(code, patch), 1)
        dst.write(np.kron(dates, patch), 2)
    assert os.path.getsize(out) < raster_io.compressed_estimate(n * n * 2 * 4)


def test_row_windows_cover_grid_once():
    rows = sum(w.height for w in raster_io.row_windows(GRID, rows=256))
    assert rows == GRID.height


def _write_plain_geotiff(path, size=600):
    """A small Int32 raster on the project grid profile with *no* internal
    overviews -- mirrors a plain user GeoTIFF ``ensure_overviews`` targets."""
    data = np.zeros((size, size), dtype="int32")
    profile = raster_io.grid_profile(
        GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=size, height=size),
        count=1,
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def test_ensure_overviews_builds_external_sidecar(tmp_path):
    tif = tmp_path / "user.tif"
    _write_plain_geotiff(tif)
    before = tif.read_bytes()

    assert raster_io.ensure_overviews(tif) is True
    sidecar = tmp_path / "user.tif.ovr"
    assert sidecar.exists()
    assert tif.read_bytes() == before  # the source raster is byte-identical


def test_ensure_overviews_skips_when_present(tmp_path):
    tif = tmp_path / "user.tif"
    _write_plain_geotiff(tif)
    assert raster_io.ensure_overviews(tif) is True
    assert raster_io.ensure_overviews(tif) is False


def test_ensure_overviews_names_a_missing_file(tmp_path):
    """Slice-1 Task 5 minor: a missing file was GDAL's raw RuntimeError."""
    missing = tmp_path / "gone.tif"
    with pytest.raises(FileNotFoundError, match="gone.tif"):
        raster_io.ensure_overviews(missing)
    assert not (tmp_path / "gone.tif.ovr").exists()


def test_ensure_overviews_rebuilds_when_stale(tmp_path):
    tif = tmp_path / "user.tif"
    _write_plain_geotiff(tif)
    assert raster_io.ensure_overviews(tif) is True
    sidecar = tmp_path / "user.tif.ovr"
    built_at = sidecar.stat().st_mtime
    os.utime(tif, (built_at + 10, built_at + 10))
    assert raster_io.ensure_overviews(tif) is True  # rebuilt, not skipped
    assert sidecar.stat().st_mtime != built_at


def test_concurrent_ensure_overviews_build_the_sidecar_once(tmp_path, monkeypatch):
    """The shared map's and the Analysis maps' builds of one GeoTIFF layer may
    ask for its overviews at once: one builds, the other finds them."""
    import threading
    import time

    tif = tmp_path / "user.tif"
    _write_plain_geotiff(tif)
    levels = raster_io._overview_levels

    def slow_levels(*args):  # widens the check-then-build window
        time.sleep(0.2)
        return levels(*args)

    monkeypatch.setattr(raster_io, "_overview_levels", slow_levels)
    results = []
    threads = [threading.Thread(target=lambda: results.append(raster_io.ensure_overviews(tif))) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert sorted(results) == [False, True]
