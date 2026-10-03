"""Block A of the Analysis controls: where am I, and what is this alert.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Holds
the set selector, the set-status banner, the queue position with its progress
bar, the navigation row, and the facts grid with its status chip — the part of
the panel that answers "which alert of how many, and what does the table say
about it" (see
``docs/superpowers/specs/2026-10-03-analysis-side-panel-design.md`` §3).

So far this module builds the queue line (position counter, reviewed/left
tally, progress bar) and the navigation row (Previous / Next / jump-to-number).
Task 3 adds the facts grid to the same card; a later task wires the card into
the tile, so nothing here calls into ``analysis_tile`` and nothing there calls
``build_context`` yet.

Every number is counted from the live alerts table rather than from
``AlertSet.n_reviewed``, so the tally moves the moment a save lands, and every
string is resolved when it is written rather than when the block is built: the
panel is built once per session and a language switch never rebuilds it.
"""

import ipyvuetify as v
from ipywidgets import Widget

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


def nav_widgets(card):
    """``(prev, next, jump, field_box, go)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["prev"], slots["next"], slots["jump"], slots["field_box"], slots["go"]


def _build_nav(app_state, id_field):
    """Previous / Next / jump. The jump field shows the 1-based number the
    counter shows, so ``go_to`` takes ``value - 1``; ``go_to`` clamps and is
    safe to press with no alerts loaded (``max_alert_id`` is -1, so every
    press lands on 0).

    ``id_field`` is the ``v.TextField`` the tile owns in production, but the
    tests drive this with a bare ``SimpleNamespace(v_model=...)`` stand-in so
    they can set its value without building a real widget tree. An
    ipyvuetify container's ``children`` only accepts real ``Widget``
    instances, so the stand-in is read for its value but only spliced into
    the rendered box when it actually is one."""
    prev = v.Btn(outlined=True, class_="flex-grow-1", style_="height: 44px;",
                 children=[v.Icon(left=True, children=["mdi-chevron-left"]),
                           t("analysis_tile.nav.previous")])
    nxt = v.Btn(outlined=True, class_="flex-grow-1", style_="height: 44px;",
                children=[t("analysis_tile.nav.next"),
                          v.Icon(right=True, children=["mdi-chevron-right"])])
    jump = v.Btn(outlined=True, style_="height: 44px; min-width: 44px;",
                 attributes={"aria-label": t("analysis_tile.nav.jump")},
                 children=[v.Icon(children=["mdi-pound"])])
    go = v.Btn(outlined=True, color="primary", class_="ml-2", style_="height: 44px;",
               children=[t("analysis_tile.nav.go")])
    field_children = [id_field, go] if isinstance(id_field, Widget) else [go]
    field_box = v.Html(tag="div", class_="d-flex align-center mt-2",
                       style_="display: none;", children=field_children)

    prev.on_event("click", lambda *_: app_state.go_prev())
    nxt.on_event("click", lambda *_: app_state.go_next())

    def _toggle(*_):
        hidden = "display: none" in (field_box.style_ or "")
        field_box.style_ = "" if hidden else "display: none;"

    jump.on_event("click", _toggle)

    def _on_go(*_):
        try:
            target = int(id_field.v_model) - 1
        except (TypeError, ValueError):
            return
        app_state.go_to(target)

    go.on_event("click", _on_go)

    row = v.Html(tag="div", class_="mt-3", children=[
        v.Html(tag="div", class_="d-flex", style_="gap: 8px;", children=[prev, nxt, jump]),
        field_box,
    ])
    return row, {"prev": prev, "next": nxt, "jump": jump, "field_box": field_box, "go": go}


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

    nav_row, nav_slots = _build_nav(app_state, id_field)

    card = v.Card(class_="pa-3 ma-2", hover=True, children=[queue_line, bar, nav_row])
    setattr(card, _SLOTS, {"counter": counter, "tally": tally, "bar": bar, **nav_slots})
    return card
