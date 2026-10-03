"""Block A of the Analysis controls: where am I, and what is this alert.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Holds
the set selector, the set-status banner, the queue position with its progress
bar, the navigation row, and the facts grid with its status chip — the part of
the panel that answers "which alert of how many, and what does the table say
about it" (see
``docs/superpowers/specs/2026-10-03-analysis-side-panel-design.md`` §3).

This task builds only the queue line: the position counter, the reviewed/left
tally and the progress bar. Tasks 2 and 3 add the navigation row and the facts
grid to the same card; a later task wires the card into the tile, so nothing
here calls into ``analysis_tile`` and nothing there calls ``build_context``
yet.

Every number is counted from the live alerts table rather than from
``AlertSet.n_reviewed``, so the tally moves the moment a save lands, and every
string is resolved when it is written rather than when the block is built: the
panel is built once per session and a language switch never rebuilds it.
"""

import ipyvuetify as v

from gui.i18n import format_number, t

#: The panel's own slots, kept on the returned card so the tests (and nothing
#: else) can reach them without re-deriving the tree's shape.
_SLOTS = "_defo_context_slots"


def review_counts(gdf) -> tuple:
    """``(total, reviewed)`` of the alerts table.

    Reviewed is a row whose ``status`` is present and is not ``"Not reviewed"``
    — the rule ``set_context.record_review`` writes into ``AlertSet.n_reviewed``.
    A table without the column (an older store) counts as nothing reviewed.
    """
    if gdf is None or len(gdf) == 0:
        return 0, 0
    if "status" not in gdf.columns:
        return len(gdf), 0
    status = gdf["status"]
    reviewed = int((status.notna() & (status != "Not reviewed") & (status != "")).sum())
    return len(gdf), reviewed


def counter_text(index: int, total: int) -> str:
    """``012 / 530``: the 1-based position, zero-padded to the total's width."""
    if total <= 0:
        return t("analysis_tile.queue.empty")
    width = len(str(total))
    return t("analysis_tile.queue.counter",
             index=str(max(index, 0) + 1).zfill(width), total=format_number(total))


def tally_text(total: int, reviewed: int) -> str:
    if total <= 0:
        return ""
    return t("analysis_tile.queue.tally", reviewed=format_number(reviewed),
             left=format_number(total - reviewed))


def queue_widgets(card):
    """``(counter, tally, bar)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["counter"], slots["tally"], slots["bar"]


def build_context(app_state, id_field) -> v.Card:
    """Block A, built once per session; it subscribes to the alerts table and
    the current position and refreshes itself."""
    counter = v.Html(tag="span", class_="text-h6 font-weight-medium",
                     style_="font-variant-numeric: tabular-nums;", children=[])
    tally = v.Html(tag="span", class_="caption text--secondary", children=[])
    bar = v.ProgressLinear(value=0, height=4, color="primary", rounded=True)

    queue_line = v.Html(
        tag="div",
        class_="d-flex align-baseline",
        children=[counter, v.Html(tag="span", style_="flex: 1 1 auto;", children=[]), tally],
    )

    def _refresh(*_):
        gdf = app_state.alerts_gdf.value
        total, reviewed = review_counts(gdf)
        counter.children = [counter_text(app_state.actual_alert_id.value, total)]
        tally.children = [tally_text(total, reviewed)]
        bar.value = (100.0 * reviewed / total) if total else 0.0

    app_state.alerts_gdf.subscribe(_refresh)
    app_state.actual_alert_id.subscribe(_refresh)
    _refresh()

    card = v.Card(class_="pa-3 ma-2", hover=True, children=[queue_line, bar])
    setattr(card, _SLOTS, {"counter": counter, "tally": tally, "bar": bar})
    return card
