import numpy as np
import rasterio

from deforestation_alerts.engine import harmonize as hz
from deforestation_alerts.engine import raster_io
from deforestation_alerts.project.models import GridSpec

GRID = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=40, height=30)


def _write(path, *bands):
    with raster_io.write_grid_raster(path, GRID, count=len(bands)) as dst:
        for i, b in enumerate(bands, start=1):
            dst.write(np.asarray(b, dtype="int32"), i)
    return path


def _scene(tmp_path):
    alert = np.zeros((GRID.height, GRID.width), "int32")
    alert[0:20, :] = 1
    alert[20:25, 0:10] = 2
    dates = np.where(alert > 0, 2026001, 0)
    dates[0:5, 0:5] = np.where(alert[0:5, 0:5] > 0, 2026020, 0)
    raw = _write(tmp_path / "raw.tif", alert, dates)
    keep = np.zeros_like(alert)
    keep[:, 0:30] = 1
    excl = np.zeros_like(alert)
    excl[0:3, :] = -5  # non-zero: on
    aoi = np.ones_like(alert)
    aoi[:, 35:] = 0
    return (raw, _write(tmp_path / "keep.tif", keep), _write(tmp_path / "excl.tif", excl),
            _write(tmp_path / "aoi.tif", aoi), alert)


def test_masks_aoi_clip_and_stats(tmp_path):
    raw, keep, excl, aoi, alert = _scene(tmp_path)
    stats = hz.harmonize(raw, [(keep, "keep"), (excl, "exclude")], tmp_path / "h.tif", GRID, aoi_raster=aoi)
    with rasterio.open(tmp_path / "h.tif") as r:
        a, d = r.read(1), r.read(2)
    assert a[:, 35:].max() == 0 and d[:, 35:].max() == 0  # outside the AOI polygon
    assert a[0:3].max() == 0  # the exclude mask's -5 counts as on
    assert a[10, 32] == 0  # outside the keep mask
    assert a[10, 5] == 1 and d[10, 5] == 2026001 and a[22, 5] == 2
    unmasked = int((alert[:, :35] > 0).sum())
    kept = int((alert[3:, :30] > 0).sum())
    assert stats == {"alert_pixels": kept, "alert_pixels_unmasked": unmasked,
                     "date_min": 2026001, "date_max": 2026020}


def test_all_alerts_removed_by_masks(tmp_path):
    raw, keep, excl, aoi, alert = _scene(tmp_path)
    none = _write(tmp_path / "none.tif", np.zeros((GRID.height, GRID.width)))
    stats = hz.harmonize(raw, [(none, "keep")], tmp_path / "h.tif", GRID)
    assert stats["alert_pixels"] == 0 and stats["alert_pixels_unmasked"] == int((alert > 0).sum())
    assert stats["date_min"] is None and stats["date_max"] is None


def test_tiles_give_the_same_pixels_as_strips(tmp_path, monkeypatch):
    raw, keep, excl, aoi, _ = _scene(tmp_path)
    masks = [(keep, "keep"), (excl, "exclude")]
    s1 = hz.harmonize(raw, masks, tmp_path / "tiles.tif", GRID, aoi_raster=aoi)
    monkeypatch.setattr(hz, "tile_windows", lambda grid: raster_io.tile_windows(grid, size=7))
    s2 = hz.harmonize(raw, masks, tmp_path / "small.tif", GRID, aoi_raster=aoi)
    monkeypatch.setattr(hz, "tile_windows", lambda grid: raster_io.row_windows(grid, rows=GRID.height))
    s3 = hz.harmonize(raw, masks, tmp_path / "strips.tif", GRID, aoi_raster=aoi)
    with rasterio.open(tmp_path / "tiles.tif") as a, rasterio.open(tmp_path / "small.tif") as b, \
            rasterio.open(tmp_path / "strips.tif") as c:
        assert (a.read() == b.read()).all() and (a.read() == c.read()).all()
    assert s1 == s2 == s3
