"""The engine layer (``deforestation_alerts/``, ``scripts/``) never imports the GUI."""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _gui_imports(path: Path) -> list:
    out = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
        out += [f"{path.relative_to(REPO)}:{node.lineno} {n}" for n in names if n == "gui" or n.startswith("gui.")]
    return out


def test_the_engine_layer_does_not_import_the_gui():
    found = [hit for folder in ("deforestation_alerts", "scripts")
             for path in sorted((REPO / folder).rglob("*.py")) for hit in _gui_imports(path)]
    assert found == []
