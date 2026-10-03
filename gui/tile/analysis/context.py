"""Block A of the Analysis controls: where am I, and what is this alert.

Architecture layer: view (raw-widget builder, no ``@solara.component``). Holds
the queue position with its progress bar, the navigation row, and the facts
grid with its status chip — the part of the panel that answers "which alert of
how many, and what does the table say about it" (see
``docs/superpowers/specs/2026-10-03-analysis-side-panel-design.md`` §3).

The set selector and the set-status banner are NOT built here, although the
spec's block table counts them as block A: ``analysis_tile.build_controls``
renders both as siblings in the head, ABOVE this card — the banner is a
full-width warning strip and the selector a ``v-select`` with its own label,
and neither belongs inside a ``pa-3`` card with the queue counter (recorded in
that spec section).

This module builds the queue line (position counter, reviewed/left tally,
progress bar), the navigation row (Previous / Next / jump-to-number) and the
facts grid with its status chip, which replaces the retired "Alert info" card.
``analysis_tile.build_controls`` calls ``build_context`` and puts the card
between the head and block B; nothing here calls back into ``analysis_tile``.

Every number is counted from the live alerts table rather than from
``AlertSet.n_reviewed``, so the tally moves the moment a save lands, and every
string is resolved when it is written rather than when the block is built: the
panel is built once per session and a language switch never rebuilds it.
"""

import ipyvuetify as v

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date
from gui.i18n import format_number, t
from gui.scripts import set_store
from gui.scripts.legend_specs import STATUS_CLASSES, status_colors
from gui.store.workflow_steps import active_set

#: The panel's own slots, kept on the returned card so the tests (and nothing
#: else) can reach them without re-deriving the tree's shape.
_SLOTS = "_defo_context_slots"

#: The status swatch beside the chip's label, as Overview's numbers table
#: draws it: a 10px square filled with the status colour (``{color}`` is
#: formatted in, never a literal -- the palette lives in ``legend_specs``).
_SWATCH = ("width: 10px; height: 10px; border-radius: 2px; display: inline-block;"
           " margin-right: 6px; background: {color};")

#: status code -> the legend key that names it, so the panel, the map and the
#: charts read the same words (as ``charts`` and ``overview_data`` derive it).
_STATUS_KEYS = {code: key for code, _, key in STATUS_CLASSES}


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


def _status_code(status) -> int:
    """The stored status as its ``STATUS_CODES`` number; anything that is not
    one of the stored strings (a NaN, an empty cell, a value from a store
    written by another version) is an unreviewed row."""
    return STATUS_CODES.get(status if isinstance(status, str) else "", 0)


def status_label_key(status) -> str:
    return _STATUS_KEYS[_status_code(status)]


def status_color(status) -> str:
    """The colour the map and the charts paint this status (never a literal:
    ``legend_specs`` is the one place the palette is written)."""
    return status_colors()[_status_code(status)]


def facts_of(gdf, alert_id, project):
    """``{"source", "first", "last", "status"}`` of the selected alert, or
    ``None`` when there is nothing to show: no table, no selection, an id out
    of range, or alerts of another set than the active one — Integrate records
    a new set before it publishes that set's alerts, and Overview skips them
    the same way."""
    s = active_set(project) if project is not None else None
    if s is not None and not set_store.alerts_belong_to(gdf, s):
        return None  # another set's alerts, until the new set's are published
    if gdf is None or alert_id is None or alert_id < 0 or alert_id >= len(gdf):
        return None
    row = gdf.iloc[alert_id]
    status = row.get("status")
    return {
        "source": str(row["alert_sources"]),
        "first": convert_julian_to_date(row["alert_date_min"]),
        "last": convert_julian_to_date(row["alert_date_max"]),
        "status": status if isinstance(status, str) and status else "Not reviewed",
    }


def queue_widgets(card):
    """``(counter, tally, bar)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["counter"], slots["tally"], slots["bar"]


def nav_widgets(card):
    """``(prev, next, jump, field_box, go)`` of a card built by ``build_context``."""
    slots = getattr(card, _SLOTS)
    return slots["prev"], slots["next"], slots["jump"], slots["field_box"], slots["go"]


def facts_widgets(card):
    """``(source, detected, chip)`` of a card built by ``build_context``. The
    chip's children are ``[swatch, label]``: the colour is on the swatch's
    ``style_`` and the words are plain text (see ``_build_facts``)."""
    slots = getattr(card, _SLOTS)
    return slots["source"], slots["detected"], slots["chip"]


def _build_nav(app_state, id_field):
    """Previous / Next / jump. The jump field shows the 1-based number the
    counter shows, so ``go_to`` takes ``value - 1``; ``go_to`` clamps and is
    safe to press with no alerts loaded (``max_alert_id`` is -1, so every
    press lands on 0).

    ``id_field`` is the ``v.TextField`` the tile owns; it sits inside
    ``field_box`` next to ``go``, which is also where the tests read and
    write its ``.v_model``."""
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
    field_box = v.Html(tag="div", class_="d-flex align-center mt-2",
                       style_="display: none;", children=[id_field, go])

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


def _build_facts():
    """What the table says about the selected alert: its source, its detection
    window and a chip that names its review status and wears that status's
    colour as a swatch (the row labels are written here, once; the values are
    written by every refresh).

    The swatch, not the words: ``overview_tile._numbers_table`` states the
    convention for this palette — "A status wears a colour chip; the text stays
    in the theme's ink, never in the series colour" — and the palette cannot
    carry text: the grey ``Not reviewed`` wears is roughly 2.8:1 on the light
    surface (WCAG AA wants 4.5:1), and the dark theme was never verified. So
    the 10px swatch carries the colour and the label stays in the theme's ink,
    drawn exactly as Overview's numbers table draws it.
    """
    mono = "font-family: monospace; font-variant-numeric: tabular-nums;"
    source = v.Html(tag="span", style_=mono, children=[])
    detected = v.Html(tag="span", style_=mono, children=[])
    swatch = v.Html(tag="span", style_=_SWATCH.format(color="transparent"))
    chip = v.Chip(small=True, outlined=True, children=[swatch])

    def _label(key):
        return v.Html(tag="span", class_="caption text--secondary text-uppercase", children=[t(key)])

    grid = v.Html(
        tag="div",
        class_="mt-3",
        style_="display: grid; grid-template-columns: 80px minmax(0, 1fr); "
               "gap: 6px 10px; align-items: center;",
        children=[
            _label("analysis_tile.facts.source"), source,
            _label("analysis_tile.facts.detected"), detected,
            _label("analysis_tile.facts.status"), v.Html(tag="div", children=[chip]),
        ],
    )
    return grid, {"source": source, "detected": detected, "chip": chip,
                  "swatch": swatch}


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

    facts_grid, facts_slots = _build_facts()

    def _refresh(*_):
        gdf = app_state.alerts_gdf.value
        total, reviewed = review_counts(gdf)
        counter.children = [counter_text(app_state.actual_alert_id.value, total)]
        tally.children = [tally_text(total, reviewed)]
        bar.value = (100.0 * reviewed / total) if total else 0.0

        # The queue line refreshes before this guard returns: another set's
        # alerts are that table's real totals, while the facts of the alert on
        # screen must stay as they are until the new set's rows are published.
        facts = facts_of(gdf, app_state.actual_alert_id.value, app_state.project.value)
        if facts is None:
            return
        facts_slots["source"].children = [facts["source"]]
        facts_slots["detected"].children = [
            t("analysis_tile.facts.range", first=facts["first"], last=facts["last"])
        ]
        facts_slots["swatch"].style_ = _SWATCH.format(
            color=status_color(facts["status"]))
        facts_slots["chip"].children = [facts_slots["swatch"],
                                        t(status_label_key(facts["status"]))]

    app_state.alerts_gdf.subscribe(_refresh)
    app_state.actual_alert_id.subscribe(_refresh)
    _refresh()

    nav_row, nav_slots = _build_nav(app_state, id_field)

    card = v.Card(class_="pa-3 ma-2", hover=True,
                  children=[queue_line, bar, nav_row, facts_grid])
    setattr(card, _SLOTS,
            {"counter": counter, "tally": tally, "bar": bar, **facts_slots, **nav_slots})
    return card
