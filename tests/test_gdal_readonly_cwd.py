"""GDAL allocates scratch files via CPLGenerateTempFilename, which falls back
to "." when CPL_TMPDIR/TMPDIR are unset. On SEPAL the CWD can be read-only, so
ComputeProximity with a non-Float32 destination dies with 'Read-only file
system' — only on the platform, never in dev. configure_gdal_tmpdir() must
make this class of operation survive a read-only CWD."""

import os

import numpy as np
import pytest
from osgeo import gdal

gdal.UseExceptions()


def test_compute_proximity_survives_readonly_cwd(tmp_path, monkeypatch, request):
    # The app wires this once at import of gui/solara_app.py; call it directly
    # so the test does not depend on the whole UI module being importable.
    from deforestation_alerts.gdal_env import configure_gdal_tmpdir

    configure_gdal_tmpdir()

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    request.addfinalizer(lambda: ro.chmod(0o755))
    monkeypatch.chdir(ro)

    drv = gdal.GetDriverByName("MEM")
    src = drv.Create("", 64, 64, 1, gdal.GDT_Byte)
    src.GetRasterBand(1).WriteArray(
        (np.arange(64 * 64).reshape(64, 64) % 7 == 0).astype("uint8")
    )
    # A non-Float32 destination forces GDAL to allocate a Float32 scratch band
    dst = drv.Create("", 64, 64, 1, gdal.GDT_UInt32)
    gdal.ComputeProximity(src.GetRasterBand(1), dst.GetRasterBand(1), ["VALUES=1"])
    assert dst.GetRasterBand(1).ReadAsArray().max() > 0


def test_configure_is_fail_fast(monkeypatch, tmp_path):
    from deforestation_alerts import gdal_env

    # Force every candidate to an unwritable location -> must raise, not log
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    monkeypatch.setenv("DEFORESTATION_ALERTS_GDAL_TMPDIR", str(ro / "nope"))
    monkeypatch.setattr(gdal_env, "_fallback_candidates", lambda: [], raising=False)
    with pytest.raises(RuntimeError):
        gdal_env.configure_gdal_tmpdir()
    ro.chmod(0o755)


# ---------------------------------------------------------------------------
# The engine under SEPAL's conditions (spec §13): an unwritable working
# directory and no temp-dir hint at all, spatial_risk's test_gdal_readonly_cwd
# pattern. Nothing above configures GDAL here: the engine must not need it.
# ---------------------------------------------------------------------------

# What CPLGenerateTempFilename() (CPL_TMPDIR, TMPDIR, TEMP) and Python's
# tempfile (TMPDIR, TEMP, TMP) consult before falling back to the CWD.
_TMP_VARS = ("CPL_TMPDIR", "TMPDIR", "TEMP", "TMP")


@pytest.fixture()
def unconfigured_tmpdir(monkeypatch):
    """Strip every temp-dir hint, through GDAL's config option too: an earlier
    configure_gdal_tmpdir() (the test above, or an app import) sets it, and it
    outlives os.environ edits. pytest does not know GDAL's globals, so the old
    value is put back by hand; tempfile's cached choice is reset as well."""
    import tempfile

    for var in _TMP_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(tempfile, "tempdir", None)
    previous = gdal.GetConfigOption("CPL_TMPDIR")
    gdal.SetConfigOption("CPL_TMPDIR", None)
    yield
    gdal.SetConfigOption("CPL_TMPDIR", previous)


@pytest.fixture()
def readonly_cwd(tmp_path, monkeypatch, request):
    """Run from a directory nothing can be written to, like SEPAL's module mount."""
    cwd = tmp_path / "readonly_module_mount"
    cwd.mkdir()
    cwd.chmod(0o555)
    request.addfinalizer(lambda: cwd.chmod(0o755))  # else tmp_path cleanup trips on it
    monkeypatch.chdir(cwd)
    return cwd


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits, so a read-only CWD proves nothing")
def test_the_fixtures_recreate_the_cwd_fallback(readonly_cwd, unconfigured_tmpdir):
    """Guard for the engine test below: under these fixtures the known
    offender (a UInt32 ComputeProximity, which needs a GDAL temp file) fails,
    so the engine passing there means it needs no temp dir, not that GDAL
    still had one."""
    drv = gdal.GetDriverByName("MEM")
    src = drv.Create("", 64, 64, 1, gdal.GDT_Byte)
    src.GetRasterBand(1).WriteArray((np.arange(64 * 64).reshape(64, 64) % 7 == 0).astype("uint8"))
    dst = drv.Create("", 64, 64, 1, gdal.GDT_UInt32)
    with pytest.raises(RuntimeError):
        gdal.ComputeProximity(src.GetRasterBand(1), dst.GetRasterBand(1), ["VALUES=1"])


def _native_raw(path, grid):
    """A raw download as Earth Engine returns it: EPSG:4326 over the grid,
    alerts (code 1, dated 2026010) north of the grid's middle latitude."""
    import math

    import rasterio
    from affine import Affine
    from pyproj import Transformer

    w, s, e, n = Transformer.from_crs(grid.epsg, "EPSG:4326", always_xy=True).transform_bounds(
        grid.xmin, grid.ymax - grid.height * grid.resolution, grid.xmin + grid.width * grid.resolution, grid.ymax
    )
    res, pad = 0.00005, 0.001
    minx, maxy = w - pad, n + pad
    width, height = math.ceil((e + pad - minx) / res), math.ceil((maxy - (s - pad)) / res)
    lat = maxy - (np.arange(height) + 0.5) * res
    alert = np.repeat((lat > (s + n) / 2).astype("int32")[:, None], width, axis=1)
    profile = dict(driver="GTiff", dtype="int32", nodata=0, count=2, width=width, height=height,
                   crs="EPSG:4326", transform=Affine(res, 0, minx, 0, -res, maxy))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(alert, 1)
        dst.write(alert * 2026010, 2)
    return path


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits, so a read-only CWD proves nothing")
# The vectorizer's polygons go to EPSG:4326 for tippecanoe, whose centroid
# geopandas flags for any geographic CRS; harmless for alert-sized polygons.
@pytest.mark.filterwarnings("ignore:Geometry is in a geographic CRS:UserWarning")
def test_the_engine_runs_with_a_readonly_cwd_and_no_temp_hint(tmp_path, readonly_cwd, unconfigured_tmpdir):
    import rasterio

    from deforestation_alerts.engine import integrate, pmtiles, raster_io
    from deforestation_alerts.engine.harmonize import harmonize
    from deforestation_alerts.engine.vectorize import vectorize_alerts
    from deforestation_alerts.project.models import GridSpec

    assert gdal.GetConfigOption("CPL_TMPDIR") is None and "TMPDIR" not in os.environ
    # wide and tall enough for internal overviews, so they are built too
    grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=8900000, width=600, height=520)
    out = tmp_path / "project"
    out.mkdir()

    # write_grid_raster: an on-grid keep mask (the left half)
    keep = np.zeros((grid.height, grid.width), "int32")
    keep[:, : grid.width // 2] = 1
    with raster_io.write_grid_raster(out / "mask.tif", grid, count=1) as dst:
        dst.write(keep, 1)

    # harmonize: align the native raw onto the grid, mask it, nearest overviews
    raw = _native_raw(out / "raw.tif", grid)
    harm = harmonize(raw, [(out / "mask.tif", "keep")], out / "harmonized.tif", grid)
    raster_io.check_header(harm, grid)
    with rasterio.open(harm) as src:
        alert, dates = src.read(1), src.read(2)
        assert src.overviews(1) == [2]
    assert alert[10, 10] == 1 and dates[10, 10] == 2026010  # north, inside the mask
    assert alert[10, grid.width - 10] == 0  # outside the keep mask
    assert alert[grid.height - 10, 10] == 0  # south: no alert

    # integrate's rasters and the vectorizer
    n = integrate.combine([(harm, 0)], grid, out / "combined.tif")
    assert n == int((alert > 0).sum()) > 0
    gdf = vectorize_alerts(out / "combined.tif", min_cluster_size=1)
    assert len(gdf) == 1
    integrate.rasterize_footprint(gdf, grid, out / "footprint.tif")
    with rasterio.open(out / "footprint.tif") as src:
        assert int(src.read(1).sum()) == n

    # the set's tiles (tippecanoe builds in the local scratch, never the CWD)
    if pmtiles.tippecanoe_available():
        alerts = gdf.to_crs(epsg=4326).assign(alert_id=range(len(gdf)), status="Not reviewed", set_id="s")
        assert pmtiles.alerts_to_pmtiles(alerts, out / "alerts.pmtiles").stat().st_size > 0

    assert list(readonly_cwd.iterdir()) == []
    assert not list(out.rglob("*.part"))
