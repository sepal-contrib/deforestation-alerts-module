"""Shared product-list table used by every workflow tab and the Summary popup.

One CSS-grid table with a collapsible count header, declarative cells, and a
standard trailing Actions column. All product lists (variables, datasets,
samples, models, predictions, evaluations, summary tables) render through this
component so styling, icons, truncation, and empty states stay consistent.

This is the single home of the grid styling formerly duplicated across
variable_list.py / dataset_list.py / sample_set_list.py / summary_lists.py and
of the job-status icon maps formerly duplicated across train_model_list.py /
inference_output_list.py.
"""

import re
from typing import Callable, Optional

import reacton.ipyvuetify as rv
import solara
from reacton import ipyvue

from gui.i18n import t

GRID_BASE = "display:grid;align-items:center;width:100%;column-gap:8px;"
HEADER_EXTRA = (
    "padding:4px 8px 6px;border-bottom:2px solid rgba(0,0,0,0.15);"
    "font-size:0.72rem;font-weight:600;"
    "text-transform:uppercase;letter-spacing:0.05em;"
)
ROW_EXTRA = "padding:5px 8px;border-bottom:1px solid rgba(0,0,0,0.08);"
CELL = "display:flex;align-items:center;gap:4px;min-width:0;"
CELL_RIGHT = "display:flex;align-items:center;justify-content:flex-end;gap:0;"
NAME_CELL = "display:flex;align-items:center;gap:6px;min-width:0;overflow:hidden;"
STACK_CELL = "display:flex;flex-direction:column;gap:3px;min-width:0;overflow:hidden;"
CHIP_LINE = "display:flex;flex-wrap:wrap;gap:4px;min-width:0;"
NAME_TEXT = "min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
# Header labels clip like data cells so a narrow column never overlaps its
# neighbour (grid items need min-width:0 + block display for ellipsis to apply).
HEADER_CELL = (
    "display:block;min-width:0;overflow:hidden;"
    "text-overflow:ellipsis;white-space:nowrap;"
)
# Full-width line under a row (grid rows have no subtitle slot): its notes.
ERROR_LINE = "grid-column:1/-1;padding:0 8px 5px;font-size:0.75rem;"
SUBTITLE = "font-size:11px;line-height:1.3;" + NAME_TEXT
PROGRESS_TRACK = "width:100px;height:3px;border-radius:2px;background:rgba(0,0,0,0.12);overflow:hidden;"
VERB = "text-transform:none;letter-spacing:normal;font-size:11.5px;padding:0 10px;height:24px;"

#: Vuetify text classes of an under-row note's tone (None: muted)
NOTE_CLASSES = {"info": "info--text", "warning": "warning--text", "error": "error--text",
                "success": "success--text", None: "text--secondary"}

# The table budget (spec D4). A table's fixed columns, its Actions column, the
# column gaps and the row padding must leave its name column MIN_FLEX_PX at
# the width it gets: the Workflow panel's content, or an outlined card's
# (``pa-3`` and a 1px border on each side) inside it.
ACTION_PX = 24  # one x-small icon button
STATUS_PX = 128  # a state cell: icon, label and progress bar
PANEL_CONTENT_PX = 448
CARD_CONTENT_PX = 422
MIN_FLEX_PX = 120
_GAP_PX = 8  # GRID_BASE's column-gap
_ROW_PADDING_PX = 16  # ROW_EXTRA's 8px on each side
_PX = re.compile(r"(\d+)px")


def actions_px(n_actions: int) -> int:
    """The Actions column: room for ``n_actions`` buttons (at least 2, so its
    header still reads), plus 8."""
    return ACTION_PX * max(n_actions, 2) + 8


def actions_width(n_actions: int) -> str:
    return f"{actions_px(n_actions)}px"


def fixed_width_px(columns, n_actions: int) -> int:
    """Everything but the flexible column(s): the fixed widths (``"140px"``; a
    column without one is flexible), the Actions column, the gaps between the
    columns and the row's padding."""
    fixed = sum(int(m.group(1)) for c in columns if (m := _PX.fullmatch(str(c.get("width", "")))))
    return fixed + actions_px(n_actions) + _GAP_PX * len(columns) + _ROW_PADDING_PX


def fits(columns, n_actions: int, content_px: int = PANEL_CONTENT_PX) -> bool:
    """True when the table leaves its name column at least MIN_FLEX_PX."""
    return content_px - fixed_width_px(columns, n_actions) >= MIN_FLEX_PX

# Vuetify theme tokens, so status chips follow the app accent in light and dark.
# "cancelled" stays a literal grey: it is deliberately neutral, not a theme tone.
# Every status here has its ``widgets.product_table.status_*`` label (the
# scaffold's "trained", a spatial_risk model status, had none and is not used).
STATUS_COLORS = {
    "running": "info",
    "ready": "success",
    "completed": "success",
    "failed": "error",
    "cancelled": "grey",
    "stale": "warning",
    "missing": "grey",
    "no_data": "grey",
    "not_covered": "grey",
    "unchecked": "grey",
}
STATUS_ICONS = {
    "running": "mdi-loading mdi-spin",
    "ready": "mdi-check-circle",
    "completed": "mdi-check-circle",
    "failed": "mdi-alert-circle",
    "cancelled": "mdi-cancel",
    "stale": "mdi-alert",
    "missing": "mdi-circle-outline",
    "no_data": "mdi-minus-circle-outline",
    "not_covered": "mdi-map-marker-off-outline",
    "unchecked": "mdi-help-circle-outline",
}

_ACTION_ICONS = {
    "edit": "mdi-pencil-outline",
    "delete": "mdi-delete-outline",
    "download": "mdi-cloud-download-outline",
    "redownload": "mdi-cloud-sync",  # forces a refresh of a live catalogue (spec §5.3)
    "cancel": "mdi-stop-circle",
    "open": "mdi-table-eye",
    "dismiss": "mdi-close",
    "move_up": "mdi-arrow-up",
    "move_down": "mdi-arrow-down",
    "info": "mdi-information-outline",
    "refresh": "mdi-refresh",
    "menu": "mdi-dots-vertical",
}


def status_text(status: str) -> str:
    """A status's label (``widgets.product_table.status_*``), or the raw value."""
    return t(f"widgets.product_table.status_{status}") if status in STATUS_ICONS else str(status)


def action_icon(kind: str, is_on: bool = False) -> str:
    """Standard icon for an action kind (map_toggle and select switch on is_on)."""
    if kind == "map_toggle":
        return "mdi-map-minus" if is_on else "mdi-map-plus"
    if kind == "select":
        return "mdi-radiobox-marked" if is_on else "mdi-radiobox-blank"
    return _ACTION_ICONS[kind]


def grid_style(widths) -> str:
    """Grid container style for the given column-width list."""
    return GRID_BASE + "grid-template-columns:" + " ".join(widths) + ";"


def _render_chip(item: dict):
    # Its column can be narrower than a translation (the coverage chip has
    # 140 px). The text clips with an ellipsis, and the chip's title shows it
    # whole on hover.
    text = str(item.get("value", ""))
    children = []
    if item.get("icon"):
        children.append(rv.Icon(children=[item["icon"]], x_small=True, left=True))
    children.append(rv.Html(tag="span", style_=NAME_TEXT, children=[text]))
    rv.Chip(
        children=children,
        x_small=True,
        outlined=item.get("outlined", True),
        color=item.get("color"),
        style_="max-width:100%;",
        attributes={"title": text},
    )


# A download action that is a link (spec 2026-10-14 §4.4): its file is built
# and the icon downloads it. The same box holds the dimmed placeholder and
# the spinner, so the Actions column keeps its width.
LINK_ACTION = ("display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;"
               "text-decoration:none;")


def link_action(act: dict, key: str = ""):
    """A ``download`` action carrying ``href``: an ``<a download>`` around its
    icon once ``href`` is set, a dimmed ``<span aria-disabled>`` while it is
    None, a spinner while ``loading``. ``title`` is the tooltip of each (a
    span, not a disabled v-btn: a disabled button shows no tooltip). Each
    state has its own key: ipyvue reads an Html widget's ``tag`` once, when its
    Vue component is built, so reusing the span's widget for the anchor would
    set ``href`` on a span that is never a link. ``key`` tells apart the links
    of one component render (reacton keys are unique per render, not per parent)."""
    title = act.get("title", "")
    if act.get("loading"):
        with rv.Html(tag="span", class_="defo-download-action", attributes={"title": title},
                     style_=LINK_ACTION).key(f"{key}link-loading"):
            rv.ProgressCircular(indeterminate=True, size=14, width=2, color="primary")
        return
    href = act.get("href")
    if href:
        attributes = {"title": title, "href": href, "target": "_blank", "download": act.get("file", "")}
        with rv.Html(tag="a", class_="defo-download-action", attributes=attributes,
                     style_=LINK_ACTION).key(f"{key}link-ready"):
            rv.Icon(small=True, color=act.get("color", "primary"), children=[action_icon(act["kind"])])
    else:
        with rv.Html(tag="span", class_="defo-download-action", attributes={"title": title, "aria-disabled": "true"},
                     style_=LINK_ACTION + "opacity:0.38;").key(f"{key}link-pending"):
            rv.Icon(small=True, children=[action_icon(act["kind"])])


def _render_cell(cell: dict, first: bool):
    ctype = cell.get("type", "text")
    wrapper = NAME_CELL if first else CELL

    if ctype == "render":
        with rv.Html(tag="div", style_=wrapper):
            cell["fn"]()
        return
    if ctype == "status":
        status = cell.get("status", "")
        # ``label`` in place of the status's own (a download's percent)
        label = cell.get("label") or status_text(status)
        # compact: the icon alone, its label (or the cell's ``title``) as the tooltip;
        # a ``label`` of its own still shows
        attributes = {"title": cell.get("title") or label} if cell.get("compact") else {}
        with rv.Html(tag="div", style_=CELL, attributes=attributes):
            rv.Icon(
                children=[STATUS_ICONS.get(status, "mdi-help-circle")],
                color=STATUS_COLORS.get(status, "grey"),
                small=True,
            )
            if not cell.get("compact") or cell.get("label"):
                # clips with an ellipsis, like the name, in a narrow stage column
                solara.Text(label, classes=["text--secondary"], style=NAME_TEXT + "font-size:0.8rem;")
        return
    if ctype == "title":
        # the name over a one-line subtitle (tone: warning / error), whole in its tooltip
        sub = str(cell.get("subtitle", ""))
        tone = NOTE_CLASSES.get(cell.get("subtitle_tone"), "text--secondary")
        with rv.Html(tag="div", style_=STACK_CELL + "gap:0;"):
            solara.Text(str(cell.get("value", "")), style=NAME_TEXT)
            rv.Html(tag="div", class_=tone, style_=SUBTITLE, attributes={"title": sub}, children=[sub])
        return
    if ctype == "state":
        attributes = {"title": cell["title"]} if cell.get("title") else {}
        with rv.Html(tag="div", style_=STACK_CELL + "gap:2px;", attributes=attributes):
            with rv.Html(tag="div", style_=CELL):
                rv.Icon(children=[cell["icon"]], color=cell.get("color"), small=True)
                solara.Text(cell.get("label", ""), classes=["text--secondary"], style=NAME_TEXT + "font-size:0.8rem;")
            if cell.get("progress") is not None:
                with rv.Html(tag="div", style_=PROGRESS_TRACK):
                    rv.Html(tag="div", style_=f"width:{cell['progress']}%;height:100%;", class_="primary")
        return
    if ctype == "verb":
        attributes = {"title": cell["title"]} if cell.get("title") else {}
        with rv.Html(tag="div", style_=CELL, attributes=attributes):
            solara.Button(
                cell["label"],
                icon_name=cell["icon"],
                on_click=cell["on_click"],
                outlined=True,
                rounded=True,
                small=True,
                color=cell.get("color"),
                disabled=cell.get("disabled", False),
                style=VERB,
            )
        return
    if ctype == "chip":
        with rv.Html(tag="div", style_=CELL):
            _render_chip(cell)
        return
    if ctype == "chips":
        with rv.Html(tag="div", style_=CELL):
            for item in cell.get("items", []):
                _render_chip(item)
        return

    if ctype == "text" and cell.get("stack"):
        # The name on its own line, its chips wrapping under it: a row with
        # several chips would otherwise clip the name to a few letters.
        with rv.Html(tag="div", style_=STACK_CELL):
            solara.Text(str(cell.get("value", "")), style=NAME_TEXT)
            with rv.Html(tag="div", style_=CHIP_LINE):
                for item in cell.get("chips", []):
                    _render_chip(item)
        return

    # "text" (default)
    style = NAME_TEXT if first else ""
    classes = ["text--secondary"] if cell.get("muted") else []
    if cell.get("size"):
        style += f"font-size:{cell['size']};"
    with rv.Html(tag="div", style_=wrapper):
        solara.Text(str(cell.get("value", "")), classes=classes, style=style)
        for item in cell.get("chips", []):
            _render_chip(item)


def action_title(act: dict) -> str:
    """An action's tooltip: its ``title``, else its kind's
    (``widgets.product_table.action_<kind>``; a map toggle says show or hide)."""
    if act.get("title"):
        return act["title"]
    kind = act["kind"]
    if kind == "map_toggle":
        kind = "hide" if act.get("is_on") else "show"
    return t(f"widgets.product_table.action_{kind}")


# The span around an action button holds its tooltip: a disabled v-btn takes
# no pointer events, so its own title would never show.
ACTION_TIP = "display:inline-flex;"


def _render_action(act: dict, key: str = ""):
    if "href" in act:
        link_action(act, key)
        return
    tip = action_title(act)
    with rv.Html(tag="span", attributes={"title": tip, "aria-label": tip}, style_=ACTION_TIP).key(f"{key}tip"):
        _render_button(act)


@solara.component
def _MenuItem(item: dict):
    color = item.get("color")
    row = rv.ListItem(
        dense=True,
        disabled=item.get("disabled", False),
        children=[
            rv.ListItemIcon(children=[rv.Icon(children=[item["icon"]], small=True, color=color)], style_="margin-right:8px;"),
            rv.ListItemTitle(children=[item["label"]], class_=f"{color}--text" if color else ""),
        ],
    )
    ipyvue.use_event(row, "click", lambda *_: item["on_click"]())
    return row


def _render_button(act: dict):
    kind = act["kind"]
    is_on = act.get("is_on", False)
    if "color" in act:
        color = act["color"]
    elif kind in ("map_toggle", "select", "info"):
        color = "primary" if is_on else "grey darken-1"
    elif kind in ("download", "open"):
        color = "primary"
    else:
        color = None
    if kind == "menu":
        activator = rv.Btn(icon=True, text=True, x_small=True, color=color, disabled=act.get("disabled", False),
                           children=[rv.Icon(children=[action_icon(kind)], small=True)])
        with rv.Menu(offset_y=True, left=True, v_slots=[{"name": "activator", "variable": "menu", "children": activator}]):
            with rv.List(dense=True):
                for item in act.get("items", []):
                    _MenuItem(item)
        return
    solara.Button(
        "",
        icon_name=action_icon(kind, is_on),
        on_click=act["on_click"],
        icon=True,
        text=True,
        x_small=True,
        color=color,
        disabled=act.get("disabled", False),
        loading=act.get("loading", False),
    )


@solara.component
def ProductTable(
    title: str,
    columns: list,
    rows: list,
    empty_text: str,
    collapsible: bool = True,
    show_actions: bool = True,
    banner: Optional[str] = None,
    header_action: Optional[Callable] = None,
):
    """Uniform product table: collapsible count header + CSS-grid rows.

    Args:
        title: header label; rendered as ``TITLE (n)``.
        columns: content columns ``[{"label", "width"?}]``; an Actions column
            is appended automatically when ``show_actions``, sized for the
            most actions any row has (``actions_width``).
        rows: ``[{"key", "cells": [CellSpec], "actions": [ActionSpec],
            "notes": [{"text": str, "tone": "info"|"warning"|"error"|
            "success"|None}], "error": str|None}]``. A status cell
            ``{"type": "status", "status", "compact": True, "title"?}`` shows
            its icon only, with ``title`` (else its label) as the tooltip;
            a ``"label"`` replaces the status's label, and shows even when
            compact.
            Each note is a line
            under the row in its tone's colour (None: muted); ``error`` is
            one more error-tone note after them. See module docstring /
            spec for CellSpec and ActionSpec shapes. An action's ``title``
            is its tooltip (default: its kind's, ``action_title``).
        empty_text: grey placeholder when there are no rows.
        collapsible: show the collapse chevron (expanded by default).
        show_actions: False = read-only mode (Summary popup).
        banner: optional stats line under the header (Summary popup).
        header_action: optional render function for the title row's right
            end (the Evaluation step's Split alerts… / Edit split).
    """
    collapsed, set_collapsed = solara.use_state(False)

    widths = [c.get("width", "minmax(0,1fr)") for c in columns]
    labels = [c["label"] for c in columns]
    if show_actions:
        widths.append(actions_width(max((len(r.get("actions") or []) for r in rows), default=0)))
        labels.append(t("common.actions"))
    grid = grid_style(widths)

    with solara.Column(style="gap:0;width:100%;"):
        with solara.Row(style="align-items:center;gap:8px;padding:4px 0;"):
            solara.Text(
                f"{title} ({len(rows)})",
                style=(
                    "font-weight:600;font-size:0.8rem;"
                    "text-transform:uppercase;letter-spacing:0.05em;"
                ),
            )
            if collapsible:
                solara.Button(
                    "",
                    icon_name="mdi-chevron-down" if collapsed else "mdi-chevron-up",
                    on_click=lambda: set_collapsed(not collapsed),
                    icon=True,
                    text=True,
                    x_small=True,
                )
            if header_action is not None:
                with rv.Html(tag="div", style_="margin-left:auto;display:flex;align-items:center;"):
                    header_action()
        if not collapsed:
            if banner:
                solara.Text(
                    banner,
                    classes=["text--secondary"],
                    style="font-size:0.78rem;padding:2px 8px 8px;",
                )
            if not rows:
                solara.Text(
                    empty_text,
                    classes=["text--secondary"],
                    style="padding:4px 8px;",
                )
            else:
                with rv.Html(
                    tag="div", class_="text--secondary", style_=grid + HEADER_EXTRA
                ):
                    for i, lbl in enumerate(labels):
                        # Right-align the Actions header to sit over its
                        # right-aligned (flex-end) action buttons.
                        is_actions = show_actions and i == len(labels) - 1
                        rv.Html(
                            tag="span",
                            style_=HEADER_CELL
                            + ("text-align:right;" if is_actions else ""),
                            children=[lbl],
                        )
                for r, row in enumerate(rows):
                    with rv.Html(tag="div", style_=grid + ROW_EXTRA):
                        for i, cell in enumerate(row.get("cells", [])):
                            _render_cell(cell, first=i == 0)
                        if show_actions:
                            with rv.Html(tag="div", style_=CELL_RIGHT):
                                for n, act in enumerate(row.get("actions", [])):
                                    _render_action(act, f"{r}:{n}:")
                        notes = list(row.get("notes") or [])
                        if row.get("error"):
                            notes.append({"text": str(row["error"]), "tone": "error"})
                        for note in notes:
                            rv.Html(tag="span", class_=NOTE_CLASSES.get(note.get("tone"), "text--secondary"),
                                    style_=ERROR_LINE, children=[str(note["text"])])
