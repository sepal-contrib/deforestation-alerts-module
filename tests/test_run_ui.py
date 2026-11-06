"""run_ui.sh's download settings: on SEPAL it leaves them to the app's
defaults (the sandbox's own jupyter serves $HOME at /api/sandbox/jupyter/files,
on sepal.io and test.sepal.io alike); locally it points them at a server that
can reach the project data. A fake ``jupyter`` on the PATH prints what the
script exported and exits, so nothing is served."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VARS = ("DEFORESTATION_ALERTS_DOWNLOAD_BASE", "DEFORESTATION_ALERTS_DOWNLOAD_ROOT")


def _run(tmp_path, **env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "jupyter"
    fake.write_text("#!/bin/bash\n" + "".join(f'echo "{v}=${{{v}-<unset>}}"\n' for v in VARS))
    fake.chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in VARS and k != "LOCALTILESERVER_CLIENT_PREFIX"}
    base.update(PATH=f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin", HOME=str(tmp_path), **env)
    out = subprocess.run(["bash", str(ROOT / "run_ui.sh"), "--port", "1"], cwd=ROOT, env=base,
                         capture_output=True, text=True, timeout=60, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if line.startswith(VARS))


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_on_sepal_downloads_are_left_to_the_apps_defaults(tmp_path):
    got = _run(tmp_path, LOCALTILESERVER_CLIENT_PREFIX="/api/sandbox/jupyter/proxy/{port}",
               DEFORESTATION_ALERTS_DATA_DIR=str(tmp_path / "module_results"))
    assert got == {v: "<unset>" for v in VARS}


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_locally_data_outside_the_checkout_is_served_through_the_proxy(tmp_path):
    data = tmp_path / "module_results"
    got = _run(tmp_path, DEFORESTATION_ALERTS_DATA_DIR=str(data))
    assert got["DEFORESTATION_ALERTS_DOWNLOAD_BASE"].startswith("/proxy/")
    assert got["DEFORESTATION_ALERTS_DOWNLOAD_ROOT"] == str(data.resolve())


@pytest.mark.skipif(sys.platform == "win32", reason="a bash script")
def test_an_operators_values_win(tmp_path):
    got = _run(tmp_path, DEFORESTATION_ALERTS_DOWNLOAD_BASE="/mine", DEFORESTATION_ALERTS_DOWNLOAD_ROOT="/srv",
               DEFORESTATION_ALERTS_DATA_DIR=str(tmp_path / "module_results"))
    assert got == {"DEFORESTATION_ALERTS_DOWNLOAD_BASE": "/mine", "DEFORESTATION_ALERTS_DOWNLOAD_ROOT": "/srv"}
