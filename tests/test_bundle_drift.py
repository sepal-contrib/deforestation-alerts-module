"""The committed bundle is what ``js/app`` builds from the sources. Needs Node
(``~/.local/bin`` is searched too) and ``npm ci`` run in ``js/app``; skips
without them. (The package's own ``node --test`` suite runs from
``tests/test_multiview_plain_browser.py::test_the_package_node_tests_pass``.)"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "js" / "app"
STATIC = ROOT / "gui" / "widget" / "static"


def _path() -> str:
    return os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])


def _tool(name: str) -> str:
    found = shutil.which(name, path=_path())
    if not found:
        pytest.skip(f"{name} is not installed")
    return found


def test_the_committed_bundle_matches_the_sources(tmp_path):
    node = _tool("node")
    if not (APP / "node_modules").is_dir():
        pytest.skip("js/app/node_modules is missing (cd js/app && npm ci)")
    subprocess.run([node, "build.mjs", str(tmp_path)], cwd=APP, check=True, env={**os.environ, "PATH": _path()})
    for name in ("multiview.js", "multiview.css"):
        built = (tmp_path / name).read_bytes()
        assert built == (STATIC / name).read_bytes(), (
            f"gui/widget/static/{name} is stale: run `cd js/app && npm run build` and commit it"
        )
