"""ipyvue binds a widget's browser v-model only when its v_model is not the
"!!disabled!!" default, so an element that passes ``on_v_model`` without a
``v_model`` never hears the browser: the callback is dead code in the app
(only a test that sets ``widget.v_model`` by hand can fire it). The
Integrate step's attribute panel shipped that way and never opened its form."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_on_v_model_comes_with_a_v_model():
    offenders = []
    for path in sorted((ROOT / "gui").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            names = {k.arg for k in node.keywords}
            if "on_v_model" in names and "v_model" not in names and None not in names:  # None: **kwargs
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {ast.unparse(node.func)}")
    assert offenders == []
