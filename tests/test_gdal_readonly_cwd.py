"""GDAL allocates scratch files via CPLGenerateTempFilename, which falls back
to "." when CPL_TMPDIR/TMPDIR are unset. On SEPAL the CWD can be read-only, so
ComputeProximity with a non-Float32 destination dies with 'Read-only file
system' — only on the platform, never in dev. configure_gdal_tmpdir() must
make this class of operation survive a read-only CWD."""

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
