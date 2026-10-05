"""The table budget (spec D4): every step table leaves its name column at
least ``MIN_FLEX_PX`` at the width it gets, and ``ProductTable`` sizes its
Actions column from its rows."""

import ipyvuetify as v
import pytest
import reacton

from gui.i18n import t
from gui.tile import alerts_step, layers_step, masks_card, period_step, prepare_step
from gui.widget import product_table
from gui.widget.product_table import (
    CARD_CONTENT_PX, MIN_FLEX_PX, NAME_TEXT, PANEL_CONTENT_PX, ProductTable, actions_px, actions_width, fits,
    fixed_width_px, status_text,
)


def _cols(*widths):
    """A name column, then one column per fixed width."""
    return [{"label": "name"}] + [{"label": f"c{i}", "width": f"{w}px"} for i, w in enumerate(widths)]


def test_the_actions_column_holds_its_buttons_and_never_less_than_two():
    assert [actions_px(n) for n in (0, 1, 2, 3, 5)] == [56, 56, 56, 80, 128]
    assert actions_width(3) == "80px"
    assert not hasattr(product_table, "ACTIONS_COL_WIDTH")


def test_the_fixed_width_counts_columns_actions_gaps_and_padding():
    # Measured in the browser (spec D4): what each step table leaves its name column.
    assert fixed_width_px(_cols(140), 3) == 252  # sources: 196 left of 448
    assert fixed_width_px(_cols(100, 100), 3) == 320  # download & harmonize: 128 left
    assert fixed_width_px(_cols(96, 28), 5) == 292  # masks: 130 left of the card's 422
    assert fixed_width_px(_cols(40, 84), 3) == 244  # periods: 204 left
    assert fixed_width_px([{"label": "a", "width": "minmax(0,1fr)"}], 0) == 56 + 8 + 16  # flexible: not counted


def test_fits_needs_the_minimum_left_at_the_given_width():
    assert (PANEL_CONTENT_PX, CARD_CONTENT_PX, MIN_FLEX_PX) == (448, 422, 120)
    assert fits(_cols(140), 3)
    assert not fits(_cols(120, 150, 150), 3)  # yesterday's sources table: 0 px left
    assert fits(_cols(96, 28), 5, CARD_CONTENT_PX)
    assert not fits(_cols(90, 52, 100, 90, 90, 70), 5, CARD_CONTENT_PX)  # yesterday's masks table


def _htmls(widget):
    """Every ``Html`` under ``widget`` (``rc.find`` stops at the outermost match)."""
    if isinstance(widget, v.Html):
        yield widget
    for child in getattr(widget, "children", None) or []:
        if hasattr(child, "children"):
            yield from _htmls(child)


def _grid_columns(box) -> list:
    styles = [w.style_ for w in _htmls(box) if "grid-template-columns:" in (w.style_ or "")]
    return [s.split("grid-template-columns:")[1].split(";")[0] for s in styles]


def test_the_actions_column_is_sized_from_the_row_with_the_most_actions():
    act = {"kind": "edit", "on_click": lambda: None}
    rows = [{"key": "a", "cells": [{"value": "a"}], "actions": [act]},
            {"key": "b", "cells": [{"value": "b"}], "actions": [act, act, act]}]
    box, rc = reacton.render(ProductTable(title="t", columns=[{"label": "c"}], rows=rows, empty_text="-"),
                             handle_error=False)
    try:
        assert _grid_columns(box) == 3 * ["minmax(0,1fr) 80px"]  # the header and both rows
    finally:
        rc.close()


def test_a_status_label_clips_and_a_compact_status_is_its_icon_with_a_tooltip():
    rows = [{"key": "a", "actions": [], "cells": [
        {"value": "a"},
        {"type": "status", "status": "stale"},
        {"type": "status", "status": "missing", "compact": True, "title": "Download: missing · Aligned: missing"},
        {"type": "status", "status": "ready", "compact": True},
    ]}]
    columns = [{"label": "c"}, {"label": "s", "width": "100px"}, {"label": "", "width": "28px"},
               {"label": "", "width": "28px"}]
    box, rc = reacton.render(ProductTable(title="t", columns=columns, rows=rows, empty_text="-"),
                             handle_error=False)
    try:
        [label] = [w for w in _htmls(box) if w.children == [t("widgets.product_table.status_stale")]]
        assert NAME_TEXT in label.style_
        titled = {w.attributes["title"]: w for w in _htmls(box) if "title" in (w.attributes or {})}
        assert set(titled) == {"Download: missing · Aligned: missing", status_text("ready")}
        compact = titled["Download: missing · Aligned: missing"]
        assert [c.children for c in compact.children] == [["mdi-circle-outline"]]  # the icon only
    finally:
        rc.close()


def test_status_text():
    assert status_text("ready") == t("widgets.product_table.status_ready")
    assert status_text("odd") == "odd"



#: Every step table, at the width it gets: ``(id, columns_fn, n_actions, content_px)``.
#: A table inside an outlined card (masks, attribute layers) gets CARD_CONTENT_PX.
STEP_TABLES = [
    ("sources", alerts_step.source_columns, alerts_step.SOURCE_ACTIONS, PANEL_CONTENT_PX),
    ("layers", layers_step.layer_columns, layers_step.LAYER_ACTIONS, PANEL_CONTENT_PX),
    ("periods", period_step.period_columns, period_step.PERIOD_ACTIONS, PANEL_CONTENT_PX),
    ("prepare", prepare_step.source_columns, prepare_step.SOURCE_ACTIONS, PANEL_CONTENT_PX),
    ("masks", masks_card.mask_columns, masks_card.MASK_ACTIONS, CARD_CONTENT_PX),
]


@pytest.mark.parametrize("columns_fn, n_actions, content_px",
                         [pytest.param(fn, n, px, id=name) for name, fn, n, px in STEP_TABLES])
def test_every_step_table_fits_the_panel(columns_fn, n_actions, content_px):
    columns = columns_fn()
    left = content_px - fixed_width_px(columns, n_actions)
    assert fits(columns, n_actions, content_px), f"{left} px left to the name column, {MIN_FLEX_PX} needed"
