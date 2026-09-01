"""``configure_gdal_tmpdir`` must set BOTH channels GDAL reads, and the app must
actually call it.

It used to run as a side effect of importing ``deforestation_alerts.parameter``,
the package that also held the container-local ``module_dir`` the Workspace
adapter replaced. That package is gone, so the call moved to the app's single
entry point and this test pins it there — losing the wiring would silently
reintroduce the read-only-CWD crash ``gdal_env`` exists to prevent.
"""

import os
from pathlib import Path

from osgeo import gdal

from deforestation_alerts.gdal_env import configure_gdal_tmpdir


def test_configure_sets_both_channels(monkeypatch):
    # Clear BOTH channels so a pre-existing value can't fake a pass
    monkeypatch.delenv("CPL_TMPDIR", raising=False)
    gdal.SetConfigOption("CPL_TMPDIR", None)

    configure_gdal_tmpdir()

    assert os.environ.get("CPL_TMPDIR"), "env channel not set"
    assert gdal.GetConfigOption("CPL_TMPDIR"), "gdal config channel not set"
    assert Path(os.environ["CPL_TMPDIR"]).is_dir()


def test_the_app_entry_point_wires_the_tmpdir():
    """``gui/solara_app.py`` calls it at import, outside any render body."""
    source = (Path(__file__).resolve().parents[1] / "gui" / "solara_app.py").read_text()
    assert "configure_gdal_tmpdir()" in source


def test_report_template_not_cwd_relative():
    from gui.tile.analysis import export

    p = Path(export._DEFAULT_REPORT_TEMPLATE)
    assert p.is_absolute(), "report template must not resolve against the CWD"
    assert p.exists()
