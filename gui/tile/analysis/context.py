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
progress bar), the navigation row (Previous / the counter's jump menu / Next) and the
facts grid with its status chip, which replaces the retired "Alert info" card.
``analysis_tile.build_controls`` calls ``build_context`` and puts the card
between the head and block B; nothing here calls back into ``analysis_tile``.

Every number is counted from the live alerts table rather than from
``AlertSet.n_reviewed``, so the tally moves the moment a save lands, and every
string is resolved when it is written rather than when the block is built: the
panel is built once per session and a language switch never rebuilds it.
"""

import ipyvuetify as v
import numpy as np

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.project.review_status import reviewed_mask
from deforestation_alerts.scripts.mosaics_helper import convert_julian_to_date
from gui.i18n import format_number, t
from gui.scripts import set_store
from gui.scripts.alert_filter import next_unreviewed_match
from gui.scripts.legend_specs import STATUS_CLASSES, status_colors
from gui.store.workflow_steps import active_set
from gui.tile.analysis.section import TYPE_BODY, TYPE_LABEL, TYPE_TITLE, panel_block

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

    Reviewed is ``review_status.is_reviewed`` — the rule
    ``set_context.record_review`` writes into ``AlertSet.n_reviewed``.
    A table without the column (an older store) counts as nothing reviewed.
    """
    if gdf is None or len(gdf) == 0:
        return 0, 0
    return len(gdf), int(reviewed_mask(gdf).sum())


def counter_text(index: int, total: int) -> str:
    """``012 / 530``: the 1-based position, zero-padded to the total's width."""
    if total <= 0:
        return t("analysis_tile.queue.empty")
    width = len(str(total))
    return t("analysis_tile.queue.counter",
             index=str(max(index, 0) + 1).zfill(width), total=format_number(total))


def filtered_counter_text(mask, index: int) -> str:
    """The counter under a filter (spec §8.3): the alert's place among the
    matches over their number, ``– / 87`` when the alert on screen is not one
    of them, and "No alerts match this filter" when none is."""
    hits = np.flatnonzero(np.asarray(mask, dtype=bool))
    matches = len(hits)
    if matches == 0:
        return t("analysis_tile.filter.no_matches")
    if not (0 <= index < len(mask)) or not mask[index]:
        return t("analysis_tile.filter.counter_outside", total=format_number(matches))
    rank = int(np.searchsorted(hits, index))
    return t("analysis_tile.queue.counter", index=str(rank + 1).zfill(len(str(matches))),
             total=format_number(matches))


def filtered_tally_text(total: int, reviewed: int, all_alerts: int) -> str:
    """The tally of the matches, then "filtered · 530 total"."""
    hint = t("analysis_tile.filter.hint", total=format_number(all_alerts))
    tally = tally_text(total, reviewed)
    return f"{tally} · {hint}" if tally else hint


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
    """``(prev, next, jump, menu, go, unreviewed)`` of a card built by
    ``build_context``: ``jump`` is the counter's button, which opens ``menu``
    (the number field with ``go``, and the next-unreviewed link)."""
    slots = getattr(card, _SLOTS)
    return (slots["prev"], slots["next"], slots["jump"], slots["menu"], slots["go"],
            slots["unreviewed"])


def jump_refusal(card):
    """The jump menu's refusal line of a card built by ``build_context``
    ("Alert 12 is not in the current filter")."""
    return getattr(card, _SLOTS)["refusal"]


def next_unreviewed(gdf, index):
    """The position of the first unreviewed alert after ``index``, wrapping
    round to the start, or ``None`` when every other alert is reviewed.
    Unreviewed is what ``review_counts`` does not count."""
    if gdf is None or len(gdf) == 0:
        return None
    n = len(gdf)
    if "status" in gdf.columns:
        status = gdf["status"]
        todo = (status.isna() | (status == "Not reviewed") | (status == "")).to_numpy()
    else:
        todo = [True] * n  # an older store: nothing reviewed
    start = max(index, -1)
    for step in range(1, n + 1):
        i = (start + step) % n
        if i != index and todo[i]:
            return i
    return None


def facts_widgets(card):
    """``(source, detected, chip)`` of a card built by ``build_context``. The
    chip's children are ``[swatch, label]``: the colour is on the swatch's
    ``style_`` and the words are plain text (see ``_build_facts``)."""
    slots = getattr(card, _SLOTS)
    return slots["source"], slots["detected"], slots["chip"]


def _build_nav(app_state, id_field, counter):
    """Previous / Next, and the counter as a button that opens a small menu:
    "Go to alert number" (``id_field`` and ``go``) and "Next unreviewed
    alert". The menu floats over the panel, so opening it moves nothing.

    The field takes the 1-based number the counter shows, so ``go_to`` takes
    ``value - 1``; ``go_to`` clamps and is safe with no alerts loaded
    (``max_alert_id`` is -1, so every press lands on 0). ``id_field`` is the
    ``v.TextField`` the tile owns, where the tests read and write
    ``.v_model``."""
    # Sized for the screen's top bar (``build_topnav``): 36 px, the bar's
    # control height, and the words kept beside the chevrons.
    prev = v.Btn(outlined=True, style_="height: 36px;",
                 children=[v.Icon(left=True, children=["mdi-chevron-left"]),
                           t("analysis_tile.nav.previous")])
    nxt = v.Btn(outlined=True, style_="height: 36px;",
                children=[t("analysis_tile.nav.next"),
                          v.Icon(right=True, children=["mdi-chevron-right"])])
    prev.on_event("click", lambda *_: app_state.go_prev())
    nxt.on_event("click", lambda *_: app_state.go_next())

    jump = v.Btn(text=True, v_on="menu.on", class_="defo-jump-btn", style_="height: 36px; padding: 0 6px 0 10px;",
                 attributes={"aria-label": t("analysis_tile.nav.jump"), "title": t("analysis_tile.nav.jump")},
                 children=[counter, v.Icon(small=True, children=["mdi-menu-down"])])
    # The menu is drawn at the app's root, outside the screen's type rules, so
    # its sizes and casing are inline.
    _btn = "text-transform: none; letter-spacing: normal; font-size: 14px;"
    go = v.Btn(outlined=True, color="primary", style_="height: 40px; " + _btn,
               children=[t("analysis_tile.nav.go")])
    unreviewed = v.Btn(text=True, color="primary", small=True, class_="px-1",
                       style_="justify-content: flex-start; " + _btn,
                       children=[t("analysis_tile.nav.next_unreviewed")])
    label = v.Html(tag="div", style_="font-size: 12px; opacity: 0.7;", children=[t("analysis_tile.nav.jump")])
    # "Alert 12 is not in the current filter": a number the filter hides is
    # not jumped to (spec §8.3). Hidden until then; opening the menu hides it.
    refusal = v.Html(tag="div", class_="error--text", style_="font-size: 12px; display: none;", children=[])
    body = v.Card(style_="width: 260px; padding: 12px; display: flex; flex-direction: column; gap: 10px;",
                  children=[
                      label,
                      v.Html(tag="div", style_="display: flex; gap: 8px; align-items: center;",
                             children=[id_field, go]),
                      refusal,
                      v.Divider(),
                      unreviewed,
                  ])
    menu = v.Menu(v_model=False, offset_y=True, nudge_bottom=4, close_on_content_click=False,
                  v_slots=[{"name": "activator", "variable": "menu", "children": jump}],
                  children=[body])

    def _show_refusal(text):
        refusal.children = [text] if text else []
        refusal.style_ = "font-size: 12px;" + ("" if text else " display: none;")

    def _on_open(change):
        if change["new"]:
            id_field.v_model = None  # the reviewer types a number, not edits the current one
            _show_refusal("")

    menu.observe(_on_open, "v_model")

    def _on_go(*_):
        try:
            target = int(id_field.v_model) - 1
        except (TypeError, ValueError):
            return
        if app_state.alert_filter is not None:
            # The number is the alert's own (its row in the whole set),
            # clamped as ``go_to`` clamps it; one the filter hides is refused.
            target = max(0, min(target, app_state.max_alert_id))
            mask = app_state.alert_mask()
            if target < len(mask) and not mask[target]:
                _show_refusal(t("analysis_tile.filter.not_in_filter", number=format_number(target + 1)))
                return
        app_state.go_to(target)
        menu.v_model = False

    go.on_event("click", _on_go)
    id_field.on_event("keydown.enter", _on_go)

    def _on_unreviewed(*_):
        # Among the filter's matches (every alert without one), wrapping.
        target = next_unreviewed_match(app_state.alerts_gdf.value, app_state.alert_mask(),
                                       app_state.actual_alert_id.value)
        if target is not None:
            app_state.go_to(target)
        menu.v_model = False

    unreviewed.on_event("click", _on_unreviewed)

    return {"prev": prev, "next": nxt, "jump": jump, "menu": menu, "go": go, "unreviewed": unreviewed,
            "refusal": refusal}


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
    # The panel's body type with tabular figures, not a monospace face: the
    # dates still line up, in the font the rest of the panel is set in.
    source = v.Html(tag="span", class_=f"{TYPE_BODY} dat-num", children=[])
    detected = v.Html(tag="span", class_=f"{TYPE_BODY} dat-num", children=[])
    swatch = v.Html(tag="span", style_=_SWATCH.format(color="transparent"))
    chip = v.Chip(small=True, outlined=True, children=[swatch])

    def _label(key):
        return v.Html(tag="span", class_=TYPE_LABEL, children=[t(key)])

    grid = v.Html(
        tag="div",
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
    counter = v.Html(tag="span", class_=f"{TYPE_TITLE} dat-num", style_="white-space: nowrap;", children=[])
    tally = v.Html(tag="span", class_=TYPE_LABEL, style_="white-space: nowrap;", children=[])
    bar = v.ProgressLinear(value=0, height=4, color="primary", rounded=True)

    facts_grid, facts_slots = _build_facts()
    nav_slots = _build_nav(app_state, id_field, counter)

    def _refresh(*_):
        gdf = app_state.alerts_gdf.value
        index = app_state.actual_alert_id.value
        total, reviewed = review_counts(gdf)
        mask = app_state.alert_mask()
        filtered = app_state.alert_filter is not None and total > 0
        if not filtered:
            counter.children = [counter_text(index, total)]
            tally.children = [tally_text(total, reviewed)]
            bar.value = (100.0 * reviewed / total) if total else 0.0
        else:
            # Spec §8.3: the queue line counts the matches; the whole set's
            # size is the hint after the tally.
            matching, done = review_counts(gdf.iloc[np.flatnonzero(mask)])
            counter.children = [filtered_counter_text(mask, index)]
            tally.children = [filtered_tally_text(matching, done, total)]
            bar.value = (100.0 * done / matching) if matching else 0.0
        # No match: Previous / Next have nowhere to go (the alert stays).
        stuck = filtered and not mask.any()
        nav_slots["prev"].disabled = stuck
        nav_slots["next"].disabled = stuck
        nav_slots["unreviewed"].disabled = next_unreviewed_match(gdf, mask, index) is None
        id_field.placeholder = f"1 – {format_number(total)}" if total else ""

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
    # The filter, when the layout bound one (``build_controls`` binds it
    # before it builds this block).
    if app_state.alert_filter_reactive is not None:
        app_state.alert_filter_reactive.subscribe(_refresh)
    _refresh()

    # The queue and the navigation live in the screen's top bar
    # (``build_topnav``), the facts in the panel: the card is the facts.
    card = panel_block([facts_grid], class_="pb-3")
    setattr(card, _SLOTS,
            {"counter": counter, "tally": tally, "bar": bar, **facts_slots, **nav_slots,
             # for ``build_topnav``'s filter control
             "app_state": app_state})
    return card


def build_topnav(card, set_selector):
    """The screen's top-bar navigation, from the widgets ``build_context``
    built (their handlers and subscriptions stay there): the alert-set
    selector, the review progress (tally over its bar), and Previous · the
    counter (its jump menu) · Next.

    The set and its progress (then the screen's help, which ``build_screen``
    inserts before the navigation) sit right of the title, against the
    navigation; the navigation spans the review panel's width
    (``.defo-topnav-nav``), Previous over its start and Next over its end."""
    slots = getattr(card, _SLOTS)
    progress = v.Html(tag="div", class_="defo-topnav-progress", children=[slots["tally"], slots["bar"]])
    middle = v.Html(tag="div", class_="defo-topnav-pos", children=[slots["menu"]])
    nav = v.Html(tag="div", class_="defo-topnav-nav", children=[slots["prev"], middle, slots["next"]])
    return v.Html(tag="div", class_="defo-topnav", children=[
        v.Html(tag="span", style_="flex: 1 1 auto;", children=[]),
        v.Html(tag="div", class_="defo-topnav-set", children=[set_selector]),
        progress,
        nav,
    ])
