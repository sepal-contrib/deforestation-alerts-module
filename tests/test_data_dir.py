"""Output-path rules: env-overridable data dir (absolute paths only),
GDAL tmpdir configured at import via BOTH channels, no CWD-relative paths."""

import importlib
import os
from pathlib import Path

import pytest
from osgeo import gdal


def _reload_directory():
    import deforestation_alerts.parameter.directory as directory

    return importlib.reload(directory)


def test_module_dir_env_override_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path / "custom"))
    directory = _reload_directory()
    assert directory.module_dir == tmp_path / "custom"
    assert directory.module_dir.is_dir()
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_env_override_tilde(monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", "~/defo_alt_results")
    directory = _reload_directory()
    assert directory.module_dir == Path.home() / "defo_alt_results"
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_env_override_rejects_relative(monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", "relative/dir")
    with pytest.raises(ValueError):
        _reload_directory()
    monkeypatch.delenv("DEFORESTATION_ALERTS_DATA_DIR")
    _reload_directory()


def test_module_dir_default():
    directory = _reload_directory()
    assert directory.module_dir == Path.home() / "module_results" / "deforestation_alerts"


def test_gdal_tmpdir_configured_on_import_both_channels(monkeypatch):
    # Clear BOTH channels so a pre-existing value can't fake a pass
    monkeypatch.delenv("CPL_TMPDIR", raising=False)
    gdal.SetConfigOption("CPL_TMPDIR", None)
    _reload_directory()
    assert os.environ.get("CPL_TMPDIR"), "env channel not set"
    assert gdal.GetConfigOption("CPL_TMPDIR"), "gdal config channel not set"
    assert Path(os.environ["CPL_TMPDIR"]).is_dir()


def test_report_template_not_cwd_relative():
    from gui.tile.analysis import export

    p = Path(export._DEFAULT_REPORT_TEMPLATE)
    assert p.is_absolute(), "report template must not resolve against the CWD"
    assert p.exists()
