from datetime import date

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import Transformer

from deforestation_alerts.engine import normalize
from deforestation_alerts.project.models import BandMap, CustomSource, NativeGrid

T = Affine(30, 0, 500000, 0, -30, 8900000)  # EPSG:32721, 100 x 80 px
W, H = 100, 80


def _src(path, fmt="radd", encoding="yyddd", alert=1, dt=2):
    return CustomSource(name="tif", origin="file", ref=str(path), asset_type="FILE", format=fmt,
                        date_encoding=encoding, band_map=BandMap(alert=alert, date=dt),
                        native=NativeGrid(crs="EPSG:32721", scale=30.0, transform=tuple(T)[:6]), digit=3)


def _tif(path, bands, dtype="int16", nodata=None):
    with rasterio.open(path, "w", driver="GTiff", width=W, height=H, count=len(bands), dtype=dtype,
                       crs="EPSG:32721", transform=T, nodata=nodata) as dst:
        for i, b in enumerate(bands, start=1):
            dst.write(b.astype(dtype), i)
    return path


def _bounds_of_cols(c0, c1, r0, r1):
    """WGS84 bounds of a pixel block of the file (slightly inside it)."""
    x0, y1 = T @ (c0 + 0.2, r0 + 0.2)
    x1, y0 = T @ (c1 - 0.2, r1 - 0.2)
    return Transformer.from_crs("EPSG:32721", "EPSG:4326", always_xy=True).transform_bounds(x0, y0, x1, y1)


def test_radd_file_reads_only_the_aoi_window_in_its_own_grid(tmp_path):
    alert = np.full((H, W), 3)
    alert[:, 50:] = 2
    dates = np.full((H, W), 25045)
    src = _src(_tif(tmp_path / "radd.tif", [alert, dates]))
    out = normalize.normalize_file(src, _bounds_of_cols(40, 60, 10, 30), date(2025, 1, 1), date(2025, 12, 31),
                                   tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        assert r.crs.to_epsg() == 32721 and r.count == 2 and r.dtypes == ("int32", "int32")
        assert (r.transform.c - 500000) % 30 == 0 and (8900000 - r.transform.f) % 30 == 0  # the file's lattice
        assert 20 <= r.width <= 22 and 20 <= r.height <= 22  # the AOI window, rounded outward
        a, d = r.read(1), r.read(2)
    col_of_50 = int(round((500000 + 50 * 30 - r.transform.c) / 30))
    assert (a[:, :col_of_50] == 1).all() and (a[:, col_of_50:] == 2).all()
    assert (d == 2025045).all()
    assert not list(tmp_path.glob("*.part"))


def test_both_window_ends_are_kept_and_the_band_order_follows_the_map(tmp_path):
    dates = np.tile(np.array([25031, 25032, 25060, 25061]), (H, W // 4))
    alert = np.full((H, W), 3)
    src = _src(_tif(tmp_path / "swap.tif", [dates, alert]), alert=2, dt=1)
    out = normalize.normalize_file(src, _bounds_of_cols(0, W, 0, H), date(2025, 2, 1), date(2025, 3, 1),
                                   tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        d = r.read(2)
    assert set(np.unique(d)) == {0, 2025032, 2025060}


def test_float32_ccdc_with_nan_nodata(tmp_path):
    count = np.full((H, W), 3, dtype="float32")
    fdate = np.full((H, W), 2024 + 44 / 366, dtype="float32")
    fdate[:10] = np.nan
    src = _src(_tif(tmp_path / "ccdc.tif", [count, fdate], dtype="float32", nodata=np.nan), fmt="ccdc",
               encoding="decimal_year")
    out = normalize.normalize_file(src, _bounds_of_cols(0, W, 0, H), date(2024, 1, 1), date(2024, 12, 31),
                                   tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        a, d = r.read(1), r.read(2)
    assert (a[:10] == 0).all() and (d[:10] == 0).all()
    assert (a[10:] == 1).all() and (d[10:] == 2024045).all()


def test_a_file_that_misses_the_aoi_gives_an_empty_raster(tmp_path):
    src = _src(_tif(tmp_path / "far.tif", [np.full((H, W), 3), np.full((H, W), 25045)]))
    out = normalize.normalize_file(src, (10.0, 10.0, 10.1, 10.1), date(2025, 1, 1), date(2025, 12, 31),
                                   tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        assert (r.width, r.height) == (1, 1) and r.read(1).max() == 0


def test_estimate_bytes_is_the_window_times_eight(tmp_path):
    src = _src(_tif(tmp_path / "e.tif", [np.zeros((H, W)), np.zeros((H, W))]))
    assert normalize.estimate_bytes(src, _bounds_of_cols(0, W, 0, H)) == W * H * 8


def test_alert_and_date_are_zero_together_in_the_raw_file(tmp_path):
    """The raw contract harmonize relies on: date 0 exactly where alert is 0, and
    alert 0 wherever the date is 0, invalid, float32 no-data or outside the window."""
    big = np.float32(3.4028235e38)
    in_win = 2024 + 44 / 366
    rows = [
        (3, in_win),          # kept: confirmed
        (1, in_win),          # kept: probable
        (0, in_win),          # no detection: date dropped
        (3, 2023 + 100 / 365),  # before the window
        (3, 2025 + 10 / 365),   # after the window
        (3, np.nan),          # no-data date
        (3, big),             # float32 max as an undeclared no-data
        (3, -big),
        (3, 0.0),
        (3, -5.0),
    ]
    count = np.repeat(np.array([r[0] for r in rows], "float32")[:, None], W, axis=1)
    fdate = np.repeat(np.array([r[1] for r in rows], "float32")[:, None], W, axis=1)
    count = np.vstack([count] * (H // len(rows)))
    fdate = np.vstack([fdate] * (H // len(rows)))
    src = _src(_tif(tmp_path / "c.tif", [count, fdate], dtype="float32"), fmt="ccdc", encoding="decimal_year")
    out = normalize.normalize_file(src, _bounds_of_cols(0, W, 0, H), date(2024, 1, 1), date(2024, 12, 31),
                                   tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        a, d = r.read(1), r.read(2)
    assert ((a == 0) == (d == 0)).all()
    first = a[: len(rows), 0].tolist(), d[: len(rows), 0].tolist()
    assert first == ([1, 2] + [0] * 8, [2024045, 2024045] + [0] * 8)


def test_a_file_partly_outside_the_aoi_is_clipped_to_the_file(tmp_path):
    src = _src(_tif(tmp_path / "p.tif", [np.full((H, W), 3), np.full((H, W), 25045)]))
    x0, y1 = T @ (W - 10, 0)  # the last 10 columns, and 50 columns east of the file
    x1, y0 = T @ (W + 50, H)
    bounds = Transformer.from_crs("EPSG:32721", "EPSG:4326", always_xy=True).transform_bounds(x0, y0, x1, y1)
    out = normalize.normalize_file(src, bounds, date(2025, 1, 1), date(2025, 12, 31), tmp_path / "raw.tif")
    with rasterio.open(out) as r:
        assert r.transform.c + r.width * 30 == 500000 + W * 30  # ends at the file's eastern edge
        assert 10 <= r.width <= 12 and (r.read(1) == 1).all()


def test_a_failed_write_leaves_no_part_file(tmp_path, monkeypatch):
    src = _src(_tif(tmp_path / "f.tif", [np.full((H, W), 3), np.full((H, W), 25045)]))

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(normalize.presets, "apply_preset", boom)
    with pytest.raises(RuntimeError, match="boom"):
        normalize.normalize_file(src, _bounds_of_cols(0, W, 0, H), date(2025, 1, 1), date(2025, 12, 31),
                                 tmp_path / "raw.tif")
    assert not list(tmp_path.glob("raw.tif*"))
