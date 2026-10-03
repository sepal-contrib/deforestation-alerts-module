"""One collapsible section of the Analysis controls: a 50px header carrying a
chevron, a title and a summary badge, over a body that is hidden with
``display`` (spec §5).

The body is NEVER removed from the widget tree and NEVER rendered through
``v-if``: it holds the maps' draw controls and the two CNN toolbars, whose
imperative ``.show()`` / ``.hide()`` / ``.disabled`` state is the edit session
itself, and MapApp already destroys and rebuilds this DOM when the step closes
and reopens.
"""

from types import SimpleNamespace

import ipyvuetify as v

_HIDDEN = "display: none;"


def build_section(title: str, body, *, badge: str = "", open: bool = False):
    """A collapsed-by-default section. ``title`` and ``badge`` are already
    translated by the caller (nothing here resolves a message)."""
    chevron = v.Icon(small=True, children=["mdi-chevron-right"])
    badge_chip = v.Chip(
        x_small=True,
        outlined=True,
        children=[badge] if badge else [],
        style_="" if badge else _HIDDEN,
    )
    header = v.Btn(
        text=True,
        block=True,
        class_="px-3",
        style_="height: 50px; justify-content: flex-start; text-transform: none;",
        children=[
            chevron,
            v.Html(tag="span", class_="ml-2 body-2", children=[title]),
            v.Html(tag="span", style_="flex: 1 1 auto;", children=[]),
            badge_chip,
        ],
    )
    body_box = v.Html(tag="div", class_="px-3 pb-3", children=[body])

    state = {"open": False}

    def is_open() -> bool:
        return state["open"]

    def set_open(flag: bool) -> None:
        state["open"] = bool(flag)
        body_box.style_ = "" if state["open"] else _HIDDEN
        chevron.children = ["mdi-chevron-down" if state["open"] else "mdi-chevron-right"]

    def set_badge(text: str, color=None) -> None:
        badge_chip.children = [text] if text else []
        badge_chip.style_ = "" if text else _HIDDEN
        if color is not None:
            badge_chip.color = color

    header.on_event("click", lambda *_: set_open(not state["open"]))
    set_open(open)

    root = v.Card(class_="ma-2", hover=True, children=[header, body_box])
    return SimpleNamespace(
        root=root,
        header=header,
        body_box=body_box,
        badge=badge_chip,
        set_badge=set_badge,
        set_open=set_open,
        is_open=is_open,
    )
