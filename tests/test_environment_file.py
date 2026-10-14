"""sepal_environment.yml declares every package the app imports by name.

The runtime environment is built from that file alone (pyproject declares no
dependencies), so a package the code imports directly but the file does not
name only works while some other package happens to pull it in.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Import name -> the package name the environment file uses for it.
PACKAGE = {
    "babel": "babel",
    "docx": "python-docx",
    "ee": "earthengine-api",
    "matplotlib_scalebar": "matplotlib-scalebar",
    "odc": "odc-geo",
    "osgeo": "gdal",
    "PIL": "pillow",
}

#: Imports that a declared package brings with it (import -> that package).
#: Keep this short: a package the app leans on belongs in the file.
TRANSITIVE = {
    "affine": "rasterio",
    "aiohttp": "geedim",
    "dateutil": "pandas",
    "reacton": "solara",
}


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _declared() -> set:
    names = set()
    for line in (ROOT / "sepal_environment.yml").read_text().splitlines():
        m = re.match(r'\s*-\s+"?([A-Za-z0-9_.\-]+)', line.split("#", 1)[0])
        if m:
            names.add(_norm(m.group(1)))
    return names


def _third_party_imports() -> set:
    local = {"gui", "deforestation_alerts", "__future__"}
    mods = set()
    for pkg in ("gui", "deforestation_alerts"):
        for py in (ROOT / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(), str(py))):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    mods.add(node.module.split(".")[0])
    return mods - set(sys.stdlib_module_names) - local


def test_every_package_the_app_imports_is_declared():
    declared = _declared()
    missing = sorted(
        mod for mod in _third_party_imports()
        if _norm(PACKAGE.get(mod, mod)) not in declared
        and _norm(TRANSITIVE.get(mod, "")) not in declared
    )
    assert missing == []


def test_the_transitive_allowance_names_declared_packages():
    assert {_norm(p) for p in TRANSITIVE.values()} <= _declared()
