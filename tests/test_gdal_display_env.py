"""GDAL env guards the map's tile server would otherwise override.

localtileserver's create_app does os.environ.setdefault for
GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR (hides .ovr sidecars process-wide) and
GDAL_NUM_THREADS=ALL_CPUS. Our guards set both outright.
"""

import os

import pytest

from deforestation_alerts import gdal_env

VARS = ("GDAL_DISABLE_READDIR_ON_OPEN", "GDAL_NUM_THREADS",
        "DEFORESTATION_ALERTS_LOCAL_SCRATCH", "DEFORESTATION_ALERTS_NUM_THREADS")


@pytest.fixture(autouse=True)
def clean_env():
    saved = {k: os.environ.get(k) for k in VARS}
    for k in VARS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_sidecars_visible_overrides_tile_server_default():
    os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
    gdal_env.keep_gdal_sidecars_visible()
    assert os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] == "FALSE"


def test_app_startup_keeps_sidecars_visible():
    """Importing the app (as voila and ``solara run`` do) installs the guard."""
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}
    out = subprocess.run(
        [
            sys.executable,
            "-W",
            "ignore",
            "-c",
            "import os, gui.solara_app; "
            "print(os.environ['GDAL_DISABLE_READDIR_ON_OPEN'])",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )

    assert out.stdout.strip().splitlines()[-1] == "FALSE"


def test_num_threads_is_half_the_cores(monkeypatch):
    monkeypatch.setattr(gdal_env, "_available_cores", lambda: 8)
    gdal_env.pin_gdal_num_threads()
    assert os.environ["GDAL_NUM_THREADS"] == "4"


def test_num_threads_env_override(monkeypatch):
    monkeypatch.setattr(gdal_env, "_available_cores", lambda: 8)
    os.environ["DEFORESTATION_ALERTS_NUM_THREADS"] = "3"
    gdal_env.pin_gdal_num_threads()
    assert os.environ["GDAL_NUM_THREADS"] == "3"


def test_num_threads_never_zero(monkeypatch):
    monkeypatch.setattr(gdal_env, "_available_cores", lambda: 1)
    gdal_env.pin_gdal_num_threads()
    assert os.environ["GDAL_NUM_THREADS"] == "1"


def test_worker_threads_follow_the_same_policy(monkeypatch):
    """The harmonize warp's dask pool is sized like GDAL's threads."""
    monkeypatch.setattr(gdal_env, "_available_cores", lambda: 8)
    assert gdal_env.worker_threads() == 4
    os.environ["DEFORESTATION_ALERTS_NUM_THREADS"] = "3"
    assert gdal_env.worker_threads() == 3


def test_local_scratch_env_override(tmp_path):
    target = tmp_path / "local"
    os.environ["DEFORESTATION_ALERTS_LOCAL_SCRATCH"] = str(target)
    assert gdal_env.local_scratch_dir() == target
    assert target.is_dir()


def test_local_scratch_falls_back_to_a_writable_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(gdal_env, "_LOCAL_SCRATCH_ROOTS", (tmp_path / "missing-ro",))
    monkeypatch.setattr(gdal_env, "_is_usable", lambda d: d == tmp_path / "scratch")
    monkeypatch.setattr(gdal_env, "scratch_dir", lambda: tmp_path / "scratch")
    assert gdal_env.local_scratch_dir() == tmp_path / "scratch"
