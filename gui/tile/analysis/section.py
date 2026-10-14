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

#: one block of the panel: flat on the panel's own surface, with a hairline
#: above it, like the other Workflow steps' panels -- not a raised card with
#: its own margins and a hover lift. The line is the footer's
#: (``footer.FOOTER_STYLE``), which reads on both themes.
BLOCK_STYLE = "border-top: 1px solid rgba(128, 128, 128, 0.25);"

#: The screen's type scale (its CSS is ``analysis_tile.SCREEN_CSS``): every
#: text of the panel takes one of these, so a title, a value or a label reads
#: the same in every block. Sentence case throughout; no capitals.
TYPE_TITLE = "dat-title"  # 14 px semibold: section and block titles
TYPE_BODY = "dat-body"  # 14 px regular: values, options, names
TYPE_LABEL = "dat-label"  # 12 px dimmed: field labels, hints, counts
TYPE_CAPTION = "dat-caption"  # 12 px in the ink: state lines


def panel_block(children, *, class_: str = "py-3"):
    """A flat block of the Analysis panel (see ``BLOCK_STYLE``)."""
    return v.Html(tag="div", class_=class_, style_=BLOCK_STYLE, children=children)


def build_section(title: str, body, *, badge: str = "", open: bool = False, divided: bool = True):
    """A collapsed-by-default section. ``title`` and ``badge`` are already
    translated by the caller (nothing here resolves a message). ``divided``
    draws the block's hairline; the footer's section has the footer's own."""
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
        class_="px-1",
        style_="height: 50px; justify-content: flex-start; text-transform: none;",
        children=[
            chevron,
            v.Html(tag="span", class_=f"ml-2 {TYPE_TITLE}", children=[title]),
            v.Html(tag="span", style_="flex: 1 1 auto;", children=[]),
            badge_chip,
        ],
    )
    body_box = v.Html(tag="div", class_="px-1 pb-3", children=[body])

    state = {"open": False}

    def is_open() -> bool:
        return state["open"]

    def set_open(flag: bool) -> None:
        state["open"] = bool(flag)
        body_box.style_ = "" if state["open"] else _HIDDEN
        chevron.children = ["mdi-chevron-down" if state["open"] else "mdi-chevron-right"]

    def set_badge(text: str, color=None) -> None:
        # The colour is written on EVERY call, the ``None`` of a neutral state
        # included -- ``None`` is a fresh chip's own colour, so this puts the
        # badge back as it was built. Both consumers (the refine summary, the
        # export file count) pass a colour for their emphasised state and
        # nothing for the others, and they mean "no colour", not "keep the
        # previous alert's": left unwritten, the chip's emphasis was the one
        # piece of per-alert state no reset cleared.
        badge_chip.children = [text] if text else []
        badge_chip.style_ = "" if text else _HIDDEN
        badge_chip.color = color

    header.on_event("click", lambda *_: set_open(not state["open"]))
    set_open(open)

    root = v.Html(tag="div", style_=BLOCK_STYLE if divided else "", children=[header, body_box])
    return SimpleNamespace(
        root=root,
        header=header,
        body_box=body_box,
        badge=badge_chip,
        set_badge=set_badge,
        set_open=set_open,
        is_open=is_open,
    )
