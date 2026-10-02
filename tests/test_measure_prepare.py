"""scripts/measure_prepare.py (spec §14 gate): where it writes, offline.

The national run itself needs Earth Engine and hours; these tests only pin
that its rasters go to the project disk, never SEPAL's small /var/tmp, and that
it leaves no scratch behind.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_prepare.py"


@pytest.fixture
def mp():
    spec = importlib.util.spec_from_file_location("measure_prepare_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_default_output_is_on_the_project_disk(mp, tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path / "data"))
    assert mp.default_out() == (tmp_path / "data").resolve() / "_measure"
    assert not mp.on_local_disk(mp.default_out())


@pytest.mark.parametrize("out", ["/var/tmp", "/var/tmp/measure", "/var/tmp/a/../measure"])
def test_an_output_on_the_small_local_disk_is_refused_before_earth_engine(mp, monkeypatch, capsys, out):
    import pysepal.scripts.utils as utils

    def never(*args, **kwargs):
        raise AssertionError("Earth Engine was initialised")

    monkeypatch.setattr(utils, "init_ee", never)
    monkeypatch.setattr(sys, "argv", ["measure_prepare.py", "--admin", "194", "--start", "2026-01-01",
                                      "--end", "2026-01-31", "--out", out])
    with pytest.raises(SystemExit) as info:
        mp.main()
    assert info.value.code == 2
    assert "/var/tmp" in capsys.readouterr().err


def test_integrate_builds_a_job_without_a_workspace_and_leaves_no_scratch(mp, tmp_path, monkeypatch):
    out = tmp_path / "out"
    jobs = []

    def fake_run(job, on_step=None):
        jobs.append(job)
        return "result"

    monkeypatch.setattr(mp, "run_integration", fake_run)
    assert mp.integrate_into(out, None, None, ["GLAD-L"]) == "result"
    (job,) = jobs
    assert isinstance(job, mp.IntegrationJob)
    assert not hasattr(job, "workspace")
    assert job.pdir == out and job.sources == ["GLAD-L"]
    assert not (out / "scratch").exists()
