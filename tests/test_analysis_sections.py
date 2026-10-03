"""The two collapsible sections of the Analysis panel. The body is hidden with
``display``, never dropped from the tree: it holds the maps' draw controls and
the CNN toolbars, whose imperative shown/disabled state must survive a
collapse, and the step container already destroys and rebuilds this DOM.
"""

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from gui.tile.analysis.section import build_section


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def test_a_section_starts_collapsed_with_its_body_in_the_tree():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    assert s.is_open() is False
    assert "display: none" in s.body_box.style_
    assert body in s.body_box.children


def test_the_header_toggles_and_the_body_keeps_its_state():
    body = v.Btn(disabled=True, children=["inner"])
    s = build_section("Refine polygon", body)
    s.header.fire_event("click", None)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    body.disabled = False          # the reviewer starts an edit
    s.header.fire_event("click", None)
    assert s.is_open() is False
    s.header.fire_event("click", None)
    assert body.disabled is False  # the edit survived the collapse
    assert body in s.body_box.children


def test_the_badge_follows_what_it_is_told():
    s = build_section("Export this alert", v.Html(tag="div"), badge="available after save")
    assert s.badge.children == ["available after save"]
    s.set_badge("2 files ready", color="primary")
    assert s.badge.children == ["2 files ready"] and s.badge.color == "primary"
    s.set_badge("")
    assert s.badge.children == [] and "display: none" in s.badge.style_


def test_set_open_drives_the_same_state():
    s = build_section("Refine polygon", v.Html(tag="div"))
    s.set_open(True)
    assert s.is_open() is True and "display: none" not in s.body_box.style_
    s.set_open(False)
    assert s.is_open() is False
