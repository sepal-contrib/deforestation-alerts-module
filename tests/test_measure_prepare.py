"""scripts/measure_prepare.py (spec §14 gate): where it writes, offline.

The national run itself needs Earth Engine and hours; these tests only pin
that its rasters go to the project disk, never SEPAL's small /var/tmp, and that
Integrate runs without the old ``Workspace``.
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


def test_integrate_builds_a_job_without_a_workspace(mp, tmp_path, monkeypatch):
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


def test_the_coverage_is_saved_before_any_row_runs(mp, tmp_path, monkeypatch):
    """The runner refuses a row without a coverage record: the script checks
    the AOI's coverage first, as the app's loader does, and GLAD-L alone reads
    catalogue metadata."""
    from types import SimpleNamespace

    import pysepal.scripts.utils as utils

    order = []
    raw = {k: {"fraction": 1.0, "regions": [{"name": k, "fraction": 1.0, "last": "2026-09-30"}]}
           for k in ("GLAD-L", "RADD")}
    monkeypatch.setattr(utils, "init_ee", lambda *a, **k: None)
    monkeypatch.setattr(mp, "admin_aoi", lambda level, admin: ("FC", (-55.10, -10.10, -55.00, -10.00)))
    monkeypatch.setattr(mp.coverage, "coverage_request",
                        lambda fc, pre, custom=(): SimpleNamespace(getInfo=lambda: order.append("coverage") or raw))
    monkeypatch.setattr(mp.catalogue, "meta_request",
                        lambda key: SimpleNamespace(getInfo=lambda: order.append(("meta", key)) or {"bands": []}))

    def fake_row(job, **kw):
        order.append(("row", job.source, sorted(job.project.coverage)))
        raise SystemExit(0)  # stop before any download

    monkeypatch.setattr(mp, "run_source_row", fake_row)
    monkeypatch.setattr(sys, "argv", ["measure_prepare.py", "--level", "0", "--admin", "194", "--start", "2026-01-01",
                                      "--end", "2026-01-31", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit):
        mp.main()
    assert order == ["coverage", ("meta", "GLAD-L"), ("row", "GLAD-L", ["GLAD-L", "RADD"])]
