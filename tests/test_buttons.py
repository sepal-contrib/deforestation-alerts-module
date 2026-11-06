"""The app's buttons (``gui.widget.buttons``) and the rules that keep the
analysis screen's buttons like the workflow's: green outlines, Vuetify's
capitals."""

import ast
from pathlib import Path

import ipyvuetify as v
import pytest

from gui.widget.buttons import ROLES, btn, label_of, set_label

GUI = Path(__file__).resolve().parents[1] / "gui"


@pytest.mark.parametrize("role", sorted(ROLES))
def test_a_role_is_a_small_button_with_its_props(role):
    button = btn(role, "Run")
    assert isinstance(button, v.Btn) and button.small is True
    for name, value in ROLES[role].items():
        assert getattr(button, name) == value
    assert button.children == ["Run"]


def test_the_icon_comes_before_the_words_and_the_trailing_one_after():
    button = btn("primary", "Save & next", icon="mdi-content-save-outline", icon_right="mdi-arrow-right")
    first, words, last = button.children
    assert first.left is True and first.children == ["mdi-content-save-outline"]
    assert words == "Save & next"
    assert last.right is True and last.children == ["mdi-arrow-right"]


def test_kwargs_win_over_the_role():
    button = btn("primary", "Discard", color="error", small=False)
    assert button.color == "error" and button.small is False


def test_set_label_rewrites_the_words_and_keeps_the_icons():
    button = btn("secondary", "Previous", icon="mdi-chevron-left")
    icon = button.children[0]
    set_label(button, "Précédent")
    assert button.children[0] is icon and label_of(button) == "Précédent"


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
            if (owner, name) in {("solara", "Button"), ("v", "Btn"), ("rv", "Btn")}:
                yield node


def test_no_outlined_button_is_left_without_a_colour():
    """An outlined button without ``color`` draws black: the app's outlined
    buttons are green (``primary``) or, for a destructive one, red."""
    bare = []
    for path in sorted(GUI.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in _calls(tree):
            kwargs = {k.arg: k.value for k in call.keywords if k.arg}
            outlined = kwargs.get("outlined")
            if isinstance(outlined, ast.Constant) and outlined.value is True and "color" not in kwargs:
                bare.append(f"{path.relative_to(GUI.parent)}:{call.lineno}")
    assert bare == []


def test_the_analysis_screen_keeps_vuetifys_capitals_on_its_buttons():
    from gui.tile.analysis.analysis_tile import SCREEN_CSS

    assert ".defo-analysis-screen .v-btn {" not in SCREEN_CSS
    # the one exception: the timeline's toolbar
    assert ".defo-analysis-screen .defo-tl-btn { text-transform: none;" in SCREEN_CSS
