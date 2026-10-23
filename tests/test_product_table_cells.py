"""The building blocks the compact Prepare tables need (spec 2026-10-23): a
tooltip on every action, the info / refresh / menu kinds, and the title,
state and verb cells."""

import re

import ipyvuetify as v
import reacton

from gui.widget.product_table import ProductTable


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def _walk(w):
    yield w
    for c in getattr(w, "children", None) or []:
        if not isinstance(c, str):
            yield from _walk(c)


def _widgets(rc, kind):
    """Every ``kind`` widget in the table (``find`` stops at the first match of a branch)."""
    return [x for root in rc.find(v.Html).widgets for x in _walk(root) if isinstance(x, kind)]


def _render(cell=None, action=None):
    row = {"key": "a", "cells": [cell or {"value": "x"}], "actions": [action] if action else []}
    return reacton.render(ProductTable(title="T", columns=[{"label": "C"}], rows=[row], empty_text="-"),
                          handle_error=False)


def _tips(rc):
    return [s for s in _widgets(rc, v.Html) if "title" in s.attributes]


def _buttons(rc):
    """The row's buttons, but for the header's collapse chevron."""
    return [b for b in _widgets(rc, v.Btn) if "mdi-chevron-up" not in _strings(b)]


def test_every_action_has_a_tooltip_even_when_disabled():
    _box, rc = _render(action={"kind": "delete", "disabled": True, "on_click": lambda: None})
    try:
        (span,) = _tips(rc)
        assert span.attributes == {"title": "Delete", "aria-label": "Delete"}
        (btn,) = [b for b in _walk(span) if isinstance(b, v.Btn)]
        assert btn.disabled
    finally:
        rc.close()


def test_a_title_overrides_the_kind_default():
    _box, rc = _render(action={"kind": "refresh", "title": "Download again", "on_click": lambda: None})
    try:
        (span,) = _tips(rc)
        assert span.attributes["title"] == "Download again"
        (btn,) = _buttons(rc)
        assert "mdi-refresh" in _strings(btn)
    finally:
        rc.close()


def test_map_toggle_tooltip_follows_its_state():
    for is_on, title in ((True, "Hide from the map"), (False, "Show on the map")):
        _box, rc = _render(action={"kind": "map_toggle", "is_on": is_on, "on_click": lambda: None})
        try:
            (span,) = _tips(rc)
            assert span.attributes["title"] == title
        finally:
            rc.close()


def test_a_menu_action_lists_its_items():
    called = []
    items = [{"label": "Edit", "icon": "mdi-pencil-outline", "on_click": lambda: called.append("f")},
             {"label": "Delete mask…", "icon": "mdi-delete-outline", "on_click": lambda: called.append("g"),
              "color": "error"}]
    _box, rc = _render(action={"kind": "menu", "items": items})
    try:
        (menu,) = _widgets(rc, v.Menu)
        assert menu.offset_y
        (slot,) = menu.v_slots
        activator = slot["children"]
        assert slot["name"] == "activator" and slot["variable"] == "menu"
        assert isinstance(activator, v.Btn) and activator.v_on == "menu.on"
        entries = _widgets(rc, v.ListItem)
        assert [x for t in _widgets(rc, v.ListItemTitle) for x in _strings(t)] == ["Edit", "Delete mask…"]
        entries[0].fire_event("click", None)
        assert called == ["f"]
    finally:
        rc.close()


def test_title_cell_shows_name_and_subtitle():
    sub = "downloaded 2026-10-21 · 12.4 MB"
    _box, rc = _render(cell={"type": "title", "value": "GLAD-L", "subtitle": sub})
    try:
        texts = [s for w in _widgets(rc, v.Html) for s in _strings(w)]
        assert "GLAD-L" in texts and sub in texts
        (line,) = [w for w in _widgets(rc, v.Html) if sub in w.children]
        assert line.attributes["title"] == sub
    finally:
        rc.close()


def test_state_cell_with_progress_draws_a_bar():
    cell = {"type": "state", "icon": "mdi-loading mdi-spin", "color": "info", "label": "Downloading", "progress": 42}
    _box, rc = _render(cell=cell)
    try:
        assert [w for w in _widgets(rc, v.Html) if "width:42%" in (w.style_ or "").replace(" ", "")]
    finally:
        rc.close()
    _box, rc = _render(cell={**cell, "progress": None})
    try:
        assert not [w for w in _widgets(rc, v.Html) if re.search(r"width:\d+%;height", w.style_ or "")]
    finally:
        rc.close()


def test_verb_cell_is_a_clickable_pill():
    clicked = []
    cell = {"type": "verb", "label": "Harmonize", "icon": "mdi-cog-play-outline", "color": "primary",
            "on_click": lambda: clicked.append(1)}
    _box, rc = _render(cell=cell)
    try:
        (btn,) = _buttons(rc)
        assert btn.outlined and btn.rounded and "Harmonize" in _strings(btn)
        btn.fire_event("click", None)
        assert clicked == [1]
    finally:
        rc.close()
    _box, rc = _render(cell={**cell, "disabled": True})
    try:
        (btn,) = _buttons(rc)
        assert btn.disabled
    finally:
        rc.close()


def test_plain_tables_render_as_before():
    _box, rc = _render()
    try:
        assert not _tips(rc)
    finally:
        rc.close()
