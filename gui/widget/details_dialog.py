"""The Details dialog of a Download & harmonize row (spec "Details dialog"):
what the row's files were made with, read-only.

``details`` is ``prepare_view.source_details`` / ``mask_details``: a title, a
lead line, an optional Why line (orange edge) and sections of label/value
rows. Plain text rows, not fields: nothing here is editable. The card takes
the theme's colours (nothing is painted white), and muted text fades by
opacity (``text_style.MUTED``) so the teleported dialog reads right in both themes.
"""

from typing import Callable, Optional

import reacton.ipyvuetify as rv
import solara

from gui.i18n import t
from gui.widget.text_style import MUTED

LABEL_PX = 118
_LEAD = "font-size: 12px; margin-bottom: 10px;" + MUTED
_WHY = ("border-left: 3px solid #ef6c00; background: rgba(239,108,0,0.12); padding: 6px 10px; font-size: 12px;"
        "margin: 4px 0 6px;")
_HEAD = "display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 600; letter-spacing: .06em;"
_WHEN = "margin-left: auto; font-weight: 400; letter-spacing: 0; text-transform: none;" + MUTED
_GRID = f"display: grid; grid-template-columns: {LABEL_PX}px 1fr; column-gap: 8px; row-gap: 3px; font-size: 12px;"
_VALUE = "min-width: 0; overflow-wrap: anywhere;"


@solara.component
def _Section(section: dict):
    with rv.Html(tag="div", style_="margin-top: 10px;"):
        with rv.Html(tag="div", style_=_HEAD + "text-transform: uppercase;", class_="primary--text"):
            rv.Icon(small=True, color="primary", children=[section["icon"]])
            rv.Html(tag="span", children=[section["title"]])
            if section.get("when"):
                rv.Html(tag="span", style_=_WHEN, children=[section["when"]])
        if section.get("empty"):
            rv.Html(tag="div", style_="font-size: 12px; font-style: italic;" + MUTED, children=[section["empty"]])
        else:
            with rv.Html(tag="div", style_=_GRID):
                for label, value in section["rows"]:
                    rv.Html(tag="div", style_=MUTED, children=[label])
                    rv.Html(tag="div", style_=_VALUE, children=[value])


@solara.component
def DetailsDialog(open: bool, details: Optional[dict], on_close: Callable):
    """``open``: shown; ``on_close()``: Close, Esc or a click outside.

    ``eager``: a lazy dialog builds its content on the first open, after
    VDialog has tried to focus it, so that first open never heard Esc."""
    dialog = rv.Dialog(v_model=open and details is not None, on_v_model=lambda v: None if v else on_close(),
                       max_width=480, eager=True)
    rv.use_event(dialog, "keydown.esc", lambda *_: on_close())
    if details is None:
        return dialog
    with dialog:
        with rv.Card():
            with rv.CardTitle(style_="word-break: normal; overflow-wrap: anywhere; padding-bottom: 0;"):
                rv.Icon(color="primary", class_="mr-2", children=["mdi-information-outline"])
                solara.Text(details["title"])
            with rv.CardText():
                rv.Html(tag="div", style_=_LEAD, children=[details["lead"]])
                if details.get("why"):
                    rv.Html(tag="div", style_=_WHY, children=[
                        rv.Html(tag="b", style_="margin-right: 4px;", children=[t("steps.prepare.why_label")]),
                        rv.Html(tag="span", children=[details["why"]])])
                for section in details["sections"]:
                    _Section(section=section)
            with rv.CardActions(style_="justify-content: flex-end;"):
                solara.Button(t("common.close"), on_click=on_close, text=True, small=True)
