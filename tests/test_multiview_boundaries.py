"""The leaflet-multiview packages stay liftable into their own repo (spec §2).

* Nothing under ``gui/widget/multiview/`` imports the app (``gui.*``,
  ``deforestation_alerts``) or ``pysepal``.
* Every import specifier under ``js/leaflet-multiview/src/`` is relative and
  resolves to a file inside ``js/leaflet-multiview/``: no bare specifiers
  (not even ``leaflet``: the page injects ``L``).
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_PACKAGE = ROOT / "gui" / "widget" / "multiview"
JS_PACKAGE = ROOT / "js" / "leaflet-multiview"

#: Top-level modules the Python package must never import.
FORBIDDEN_ROOTS = {"gui", "deforestation_alerts", "pysepal"}

# ``import x from "s"``, ``import {a,\n b} from "s"``, ``export {a} from "s"``,
# ``export * from "s"``: a keyword, then anything but a quote, semicolon or
# backtick, then ``from`` and the quoted specifier.
_FROM = re.compile(r"""\b(?:import|export)\b[^'"`;]*?\bfrom\s*(['"])([^'"]+)\1""", re.S)
# ``import "s"`` (side effect) and ``import("s")`` (dynamic).
_BARE_IMPORT = re.compile(r"""\bimport\s*\(?\s*(['"])([^'"]+)\1""")
# Block comments, and line comments that start a line (a ``//`` inside a
# string, as in a URL, is left alone).
_COMMENTS = re.compile(r"/\*.*?\*/|^\s*//[^\n]*", re.S | re.M)


def _python_offenders(path: Path) -> list:
    offenders = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 1:  # ``from .. import`` leaves the package, into gui.widget
                offenders.append(f"{path.name}:{node.lineno} relative import level {node.level}")
                continue
            mods = [node.module] if node.module and node.level == 0 else []
        else:
            continue
        offenders += [f"{path.name}:{node.lineno} {m}" for m in mods if m.split(".")[0] in FORBIDDEN_ROOTS]
    return offenders


def js_specifiers(source: str) -> list:
    """Every module specifier a JS source imports or re-exports from."""
    source = _COMMENTS.sub("", source)
    return [m.group(2) for m in _FROM.finditer(source)] + [m.group(2) for m in _BARE_IMPORT.finditer(source)]


def test_the_python_package_imports_neither_the_app_nor_pysepal():
    files = sorted(PY_PACKAGE.rglob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "widget.py", "plugins.py", "specs.py"}
    offenders = [o for f in files for o in _python_offenders(f)]
    assert offenders == []


def test_the_python_check_catches_app_imports(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import gui.i18n\nfrom pysepal.mapping import x\nfrom deforestation_alerts import y\n"
                   "from ..alert_views import AlertViews\nfrom .plugins import Draw\nimport traitlets\n")
    assert [o.split(" ", 1)[1] for o in _python_offenders(bad)] == [
        "gui.i18n", "pysepal.mapping", "deforestation_alerts", "relative import level 2"]


def test_the_specifier_scan_finds_every_import_form():
    source = '''
        import L from "leaflet";
        import {
          a,
          b,
        } from "./core/layers.js";
        export {draw} from "./plugins/draw/index.js";
        export * from '../x.js';
        import "./side-effect.js";
        const lazy = await import("./lazy.js");
        export const ALL = "all";
        const url = "https://tile.example/{z}/{x}/{y}.png"; // from "not-an-import"
        /* import gone from "commented-out"; */
        // import also from "commented-out-too";
    '''
    assert sorted(js_specifiers(source)) == sorted([
        "leaflet", "./core/layers.js", "./plugins/draw/index.js", "../x.js", "./side-effect.js", "./lazy.js"])


def test_every_js_import_is_relative_and_stays_inside_the_package():
    sources = sorted((JS_PACKAGE / "src").rglob("*.js"))
    assert sources, "js/leaflet-multiview/src has no .js files"
    package = JS_PACKAGE.resolve()
    offenders = []
    for src in sources:
        for spec in js_specifiers(src.read_text(encoding="utf-8")):
            target = (src.parent / spec).resolve()
            if not spec.startswith(("./", "../")):
                offenders.append(f"{src.relative_to(JS_PACKAGE)}: bare specifier {spec!r}")
            elif not target.is_relative_to(package):
                offenders.append(f"{src.relative_to(JS_PACKAGE)}: {spec!r} leaves the package")
            elif not target.is_file():
                offenders.append(f"{src.relative_to(JS_PACKAGE)}: {spec!r} is not a file")
    assert offenders == []
