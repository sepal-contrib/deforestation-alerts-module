"""The Overview dashboard (spec §9.2, §9.3; sections, 2026-11-06), a MapApp
dialog 1300 px wide.

What it shows is the alert set the set selector picks (``gui.widget.set_selector``,
shared with Analysis): a pick changes only the project's ``active_set``, and this
view then summarises that set. Its selector also offers **All periods** first:
every period's set as ``overview_data.period_sets`` picks them, summed. That
choice is this view's own state -- the active set stays where it was -- so
Review alerts and the map layers, which belong to one set, are off while it holds.

Layout: one headline line (confirmed loss with its delta, detected, reviewed,
largest), then tabs -- Status, Over time, Place & source, Answers, Pixels,
Clusters, Files -- each a pair of cards, and nothing shown twice. Only the open
tab is rendered.

Architecture layer: view. The alert layers, the earlier sets and the not-analysable
pixels are the shell's ``_AlertLayers`` leaf, because a layer switched on stays on
while the user moves between steps and dialogs. This view carries that leaf's three
booleans as the layer buttons at the end of the tab row. The eyes of the Pixels tab
(a source's harmonized file, a mask) are plain ``MapLayers`` shows, as the Prepare
step makes them. Turning a layer on closes this dialog: the map is behind it.

Three rulings shape the component:

* **The numbers come off the store, not off the session's alerts.** A summary
  is a projected parquet read (``overview_data.summary_of``), so any set can be
  summarised without loading it, and the history reads one per period. The read
  blocks, so it runs on a daemon thread through ``spawn_in_context`` from an
  effect, with a run counter -- a set switched twice quickly must not land the
  first read's numbers. The pixel and cluster counts are the artifacts' records
  (``overview_data.pixels_of``), read in the body.
* **One measure for the whole page**, Area (ha) by default.
* **The figures are drawn once per (summary, measure, theme, language,
  questions)**, in a ``use_memo``: the project is committed on every auto-save.
  A figure that raises comes back as ``None`` and that one card lists its
  numbers instead (``_figure_or_numbers``).

Feedback is hybrid. The component reports its own read failure in place. The
export card is ``gui.tile.overview_export``'s RAW ipyvuetify ``ExportCard``,
whose writes run on a daemon thread and report through the notification bus.
"""

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.i18n import current_locale
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, harm_key, mask_key, set_status
from deforestation_alerts.project import grouping, paths
from deforestation_alerts.scripts.set_summary import UNATTRIBUTED
from gui.i18n import format_date, format_number, t, tn
from gui.scripts import alert_layers, charts, growth_index, overview_data, set_store, source_layers
from gui.scripts import questions as questions_mod
from gui.scripts.error_text import error_text
from gui.scripts.legend_specs import status_colors
from gui.scripts.solara_threads import spawn_in_context
from gui.store.workflow_steps import active_set
from gui.tile.overview_export import build_export_card, build_summary_file, relabel_export_card
from gui.widget.product_table import action_icon
from gui.widget.set_selector import SetSelector

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")

#: One card of a tab's pair. The figures are 420 px wide, so a column that
#: falls below that scales them down rather than clipping them, and the two
#: columns stack on a narrow screen.
_COLUMN = "flex: 1 1 420px; min-width: 320px; display: flex; flex-direction: column; gap: 10px;"
_TITLE = "font-size: 0.82rem; font-weight: 600; letter-spacing: 0.02em;"
_NOTE = "font-size: 0.72rem; opacity: 0.72; line-height: 1.25;"
_VALUE = "font-size: 1.05rem; font-weight: 600; line-height: 1.1;"
#: The one-series bar colour of every HTML bar here: SEPAL secondary, as the figures'.
_BAR = charts.THEMES["light"]["accent"]
#: How many administrative areas the table lists before it stops.
ADMIN_LIMIT = 8
#: The admin card's levels: ``(key, its summary rows' attribute)``. FAO GAUL
#: level 2 is recorded too (``admin3``) but left out: it lists too many areas.
ADMIN_COUNTRY, ADMIN_LEVEL_1 = "country", "level1"
_ADMIN_ROWS = {ADMIN_COUNTRY: "by_admin", ADMIN_LEVEL_1: "by_admin_level1"}
#: The tabs, in order.
TABS = ("status", "time", "place", "answers", "pixels", "clusters", "files")


# ---------------------------------------------------------------------------
# The dashboard's own pieces. Plain functions, not components: each one emits
# its elements into whatever container the caller is inside.
# ---------------------------------------------------------------------------

def period_label(period) -> str:
    """A monitoring period as every axis and caption here names it."""
    return t("steps.period.range", start=period.start.isoformat(), end=period.end.isoformat())


def _share(value: float) -> str:
    return f"{format_number(value * 100, 0)}%"


def _count(value) -> str:
    """A recorded count, or a dash for one the set's run did not record."""
    return "—" if value is None else format_number(value, 0)


@contextmanager
def _card(title: str, note: str = ""):
    """A dashboard card. Its contents are whatever the caller emits inside."""
    with rv.Card(outlined=True, class_="pa-3", style_="height: 100%;"):
        with solara.Column(style="gap: 6px;"):
            solara.Text(title, style=_TITLE)
            if note:
                solara.Text(note, style=_NOTE)
            yield


@contextmanager
def _pair():
    """A tab's row of cards."""
    with solara.Row(style="gap: 10px; align-items: stretch; flex-wrap: wrap;"):
        yield


def _column():
    return solara.Div(style=_COLUMN)


def _figure_or_numbers(svg, rows):
    """The figure, or -- when matplotlib raised on it -- its own numbers in
    text, so a card that cannot draw still answers the question it asks."""
    if svg is not None:
        solara.HTML(tag="div", unsafe_innerHTML=svg)
        return
    solara.Text(t("overview_tile.chart_failed"), classes=["warning--text"], style=_NOTE)
    for label, value in rows:
        solara.Text(f"{label} — {value}", style=_NOTE)


def _table(head, rows, *, bold_last: bool = False):
    """A dense table: ``head`` the column titles, ``rows`` lists of cells
    (text or elements). ``bold_last`` sets a total row apart."""
    body = []
    for i, cells in enumerate(rows):
        total = bold_last and i == len(rows) - 1
        body.append(rv.Html(tag="tr", style_="font-weight: 600;" if total else "", children=[
            rv.Html(tag="td", children=cell if isinstance(cell, list) else [cell]) for cell in cells]))
    rv.SimpleTable(dense=True, children=[
        rv.Html(tag="thead", children=[rv.Html(tag="tr", children=[
            rv.Html(tag="th", children=[column]) for column in head])]),
        rv.Html(tag="tbody", children=body),
    ])


def _swatch(colour: str):
    return rv.Html(tag="span", style_=(f"background: {colour}; width: 10px; height: 10px; border-radius: 2px;"
                                       " display: inline-block; margin-right: 6px;"))


def _eye(on: bool, on_click, *, busy: bool = False, title: str = ""):
    """The map toggle of a row, as the Prepare tables draw theirs."""
    with rv.Html(tag="span", attributes={"title": title} if title else {}):
        solara.Button("", icon_name=action_icon("map_toggle", on), icon=True, text=True, x_small=True,
                      color="primary" if on else None, loading=busy, on_click=on_click)


def _numbers_table(summary, measure: str):
    """Every status with its count, area, share (a data bar in the status's
    colour) and median. Text stays in the theme's ink, never the series colour."""
    head = (t("overview_tile.table_status"), t("overview_tile.table_alerts"),
            t("overview_tile.table_area"), t("overview_tile.table_share"),
            t("overview_tile.table_median"))
    rows = []
    for code, colour, label, n, area, median, share in overview_data.table_rows(summary, measure):
        name = [solara.Text(label).key(f"name-{code}")]
        bar = []
        if colour is not None:
            name.insert(0, _swatch(colour))
            bar = [rv.Html(tag="span", style_=(f"display: inline-block; height: 8px; width: {share * 60:.1f}px;"
                                               f" background: {colour}; border-radius: 2px; margin-right: 6px;"))]
        rows.append([name, format_number(n, 0), format_number(area, 1), bar + [_share(share)],
                     "—" if median is None else format_number(median, 2)])
    _table(head, rows, bold_last=True)


def _admin_label(level: str) -> str:
    return t("overview_tile.admin_level_country" if level == ADMIN_COUNTRY else "overview_tile.admin_level_1")


def _segment(options, current, on_pick, *, x_small=False):
    """Segmented buttons in the theme's neutral ink, the current one filled."""
    with solara.Row(style="gap: 4px;"):
        for key, label in options:
            solara.Button(label, x_small=x_small, small=not x_small, depressed=True,
                          outlined=current != key, on_click=lambda key=key: on_pick(key))


def _admin_table(summary, level: str):
    """Where the loss is, by the admin names the reviewer recorded. Reviewed
    alerts only -- nothing writes them for an unreviewed alert."""
    head = (_admin_label(level), t("overview_tile.table_area"), t("legend.status.confirmed"),
            t("overview_tile.table_alerts"))
    _table(head, [[t("overview_tile.admin_unattributed") if name == UNATTRIBUTED else name,
                   format_number(area, 1), format_number(confirmed_ha, 1), format_number(n, 0)]
                  for name, area, confirmed_ha, n in getattr(summary, _ADMIN_ROWS[level])[:ADMIN_LIMIT]])


def _reviewed_note(key: str, summary) -> str:
    return t(key, reviewed=format_number(summary.n_reviewed, 0),
             total=format_number(summary.n_alerts, 0))


def _delta_of(history, summary, measure: str):
    """``(text, critical, period)`` comparing the shown set's confirmed loss
    with the period before it, or None when there is no earlier period to
    compare with. More loss is worse, so a rise is the critical one."""
    entry = next((e for e in history if e[2].set_id == summary.set_id), None)
    if entry is None:
        return None
    before = overview_data.previous_in(history, entry[0].id)
    if before is None:
        return None
    now = overview_data.status_value(summary, overview_data.CONFIRMED_CODE, measure)
    was = overview_data.status_value(before[2], overview_data.CONFIRMED_CODE, measure)
    label = period_label(before[0])
    if now == was:
        return t("overview_tile.delta_same", period=label), False
    key = "overview_tile.delta_up" if now > was else "overview_tile.delta_down"
    return t(key, value=charts.format_value(abs(now - was), measure), period=label), now > was


def _stat(label: str, value: str, support: str = "", *, lead: bool = False, delta=None):
    """One number of the headline line: its value, what it is, and a support
    line. ``lead`` is confirmed loss, set off by the Confirmed red rule."""
    red = status_colors()[overview_data.CONFIRMED_CODE]
    rule = f"border-left: 3px solid {red}; padding-left: 8px;" if lead else ""
    with rv.Html(tag="div", class_="ov-stat", style_=f"flex: 0 1 auto; min-width: 140px; {rule}"):
        solara.Text(label, style=_NOTE)
        with solara.Row(style="gap: 8px; align-items: baseline; flex-wrap: wrap;"):
            solara.Text(value, style=_VALUE)
            if delta is not None:
                text, critical = delta
                solara.Text(text, style=f"{_NOTE} padding: 1px 6px; border-radius: 9px; opacity: 1;"
                                        f" border: 1px solid {red if critical else 'currentColor'};"
                                        f"{f' color: {red};' if critical else ''}")
        if support:
            solara.Text(support, style=_NOTE)


def _headline(summary, history, measure: str, all_periods: bool):
    """Confirmed loss (with its delta), the total, the reviewed share and the
    single largest alert, on one line."""
    confirmed = overview_data.status_value(summary, overview_data.CONFIRMED_CODE, measure)
    with solara.Row(style="gap: 24px; flex-wrap: wrap; align-items: flex-start;"):
        _stat(t("overview_tile.kpi_confirmed"), charts.format_value(confirmed, measure), lead=True,
              delta=None if all_periods else _delta_of(history, summary, measure))
        _stat(t("overview_tile.kpi_detected"),
              charts.format_value(overview_data.total_of(summary, measure), measure),
              tn("steps.sets.alerts", summary.n_alerts))
        _stat(t("overview_tile.kpi_reviewed"),
              _share(summary.n_reviewed / summary.n_alerts if summary.n_alerts else 0.0),
              _reviewed_note("overview_tile.kpi_reviewed_of", summary))
        # Always an area: "the largest alert" means the largest polygon, and
        # in the Alerts measure every alert would be worth the same 1.
        largest = summary.largest
        value, support = "—", ""
        if largest is not None:
            alert_id, area, admin, when = largest
            where = admin or t("overview_tile.admin_unattributed")
            value = charts.format_value(area, charts.MEASURE_AREA)
            support = t("overview_tile.kpi_largest_at", id=format_number(alert_id, 0),
                        where=where if when is None else f"{where} · {format_date(when)}")
        _stat(t("overview_tile.kpi_largest"), value, support)


def _funnel(rows, *, pixel_ha: Optional[float] = None):
    """Horizontal steps: ``(key, label, value, dropped, sub, eye)`` per row,
    ``eye`` None or ``(on, on_click, busy)``. A dropped step is a soft bar with
    a minus; a value the set did not record is a dash and no bar. With
    ``pixel_ha`` each value also reads in hectares."""
    top = max((value for _, _, value, *_ in rows if value), default=0) or 1
    grid = "display: grid; grid-template-columns: minmax(150px, 1.2fr) 2fr minmax(90px, auto) 28px;"
    with rv.Html(tag="div", style_=f"{grid} gap: 4px 10px; align-items: center;"):
        for key, label, value, dropped, sub, eye in rows:
            colour = "rgba(128, 128, 128, 0.35)" if dropped else _BAR
            width = 0.0 if not value else max(value / top * 100, 0.8)
            solara.Text(label, style=f"font-size: 0.8rem;{' padding-left: 14px; opacity: 0.8;' if sub else ''}")
            with rv.Html(tag="div", style_="height: 10px; background: rgba(128, 128, 128, 0.12); border-radius: 2px;"):
                rv.Html(tag="div", style_=f"height: 10px; width: {width:.1f}%; background: {colour};"
                                          " border-radius: 2px;")
            text = _count(value) if value is not None else "—"
            if value and dropped:
                text = f"−{text}"
            with rv.Html(tag="div", style_="text-align: right; font-size: 0.8rem; font-variant-numeric: tabular-nums;"):
                solara.Text(text)
                if pixel_ha and value is not None:
                    solara.Text(f" {t('overview_tile.px_ha', ha=format_number(value * pixel_ha, 1))}", style=_NOTE)
            if eye is None:
                rv.Html(tag="span")
            else:
                on, on_click, busy = eye
                _eye(on, on_click, busy=busy, title=label)


# --- the tabs ------------------------------------------------------------

def _status_tab(summary, figures, measure: str):
    with _pair():
        with _column():
            with _card(t("overview_tile.card_status")):
                _figure_or_numbers(figures.get("status"),
                                   [(label, charts.format_value(value, measure))
                                    for _, _, label, value in charts.status_rows(summary, measure=measure)])
                _numbers_table(summary, measure)
        with _column():
            with _card(t("overview_tile.card_sizes")):
                _figure_or_numbers(figures.get("sizes"), [])


def _time_tab(history, figures, measure: str):
    with _pair():
        with _column():
            with _card(t("overview_tile.card_history"), t("overview_tile.history_note")):
                if len(history) < 2:
                    solara.Text(t("overview_tile.history_one_period"), style=_NOTE)
                else:
                    _figure_or_numbers(figures.get("history"),
                                       [(period_label(period),
                                         charts.format_value(overview_data.total_of(item, measure), measure))
                                        for period, _, item in history])
        with _column():
            with _card(t("overview_tile.card_timing"), t("overview_tile.timing_note")):
                _figure_or_numbers(figures.get("timing"), [])


def _place_tab(summary, figures, admin_level: str, on_admin_level):
    with _pair():
        with _column():
            with _card(t("overview_tile.card_admin"), _reviewed_note("overview_tile.admin_note", summary)):
                if not summary.by_admin:
                    solara.Text(t("overview_tile.admin_empty"), style=_NOTE)
                else:
                    _segment([(k, _admin_label(k)) for k in (ADMIN_COUNTRY, ADMIN_LEVEL_1)], admin_level,
                             on_admin_level, x_small=True)
                    _admin_table(summary, admin_level)
        with _column():
            with _card(t("overview_tile.card_sources"), t("overview_tile.sources_note")):
                _figure_or_numbers(figures.get("sources"), [])


def _answers_tab(summary, figures, measure: str, questions):
    # One card per question, in questionnaire order (spec D9). Reviewed alerts only.
    with _pair():
        for q in questions:
            with _column():
                with _card(questions_mod.question_label(q), _reviewed_note("overview_tile.answers_note", summary)):
                    if not summary.breakdown(q.key):
                        solara.Text(t("overview_tile.answers_empty"), style=_NOTE)
                    else:
                        _figure_or_numbers(
                            figures.get(f"q:{q.key}"),
                            charts.question_rows(summary, q.key, measure=measure,
                                                 label_of=lambda code, q=q: questions_mod.label(q, code)))


def _pixels_tab(px, grid_m: float, eyes):
    """Source pixels to alert pixels, the per-source table and the masks.
    ``eyes`` maps a layer to its row's ``(on, on_click, busy)``, None for a
    layer that cannot be shown (All periods, no file)."""
    before, kept = px.total(1), px.total(2)
    masked = None if before is None or kept is None else before - kept
    states, alerts = eyes.get("states"), eyes.get("alerts")
    rows = [
        ("source", t("overview_tile.px_source"), before, False, False, None),
        ("masked", t("overview_tile.px_masked"), masked, True, True, None),
        ("kept", t("overview_tile.px_kept"), kept, False, True, None),
        ("combined", t("overview_tile.px_combined"), px.combined_px, False, False, None),
        ("small", t("overview_tile.px_too_small"), px.too_small_px, True, True, states),
        ("cap", t("overview_tile.px_over_cap"), px.over_cap_px, True, True, states),
        ("alerts", t("overview_tile.px_alerts"), px.analysable_px, False, False, alerts),
    ]
    note = t("overview_tile.pixels_note")
    if grid_m:
        note += " " + t("overview_tile.pixels_res", res=format_number(grid_m, 0), ha=format_number(px.pixel_ha, 2))
    with _pair():
        with _column():
            with _card(t("overview_tile.card_pixels"), note):
                _funnel(rows, pixel_ha=px.pixel_ha)
                if any(r[2] is None for r in rows):
                    solara.Text(t("overview_tile.not_recorded"), style=_NOTE)
        with _column():
            with _card(t("overview_tile.card_by_source")):
                body = []
                for name, b, k in px.sources:
                    lost = None if b is None or k is None else b - k
                    share = f" ({_share(lost / b)})" if lost is not None and b else ""
                    eye = eyes.get(f"src:{name}")
                    body.append([name, _count(b), f"−{_count(lost)}{share}" if lost else _count(lost), _count(k),
                                 [] if eye is None else [_eye_cell(eye, name)]])
                if len(px.sources) > 1:
                    lost = masked
                    body.append([t("overview_tile.all_sources"), _count(before),
                                 f"−{_count(lost)}" if lost else _count(lost), _count(kept), ""])
                _table((t("overview_tile.col_source"), t("overview_tile.col_alert_px"), t("overview_tile.col_masked"),
                        t("overview_tile.col_kept"), ""), body, bold_last=len(px.sources) > 1)
                solara.Text(t("overview_tile.masks_used"), style=f"{_TITLE} margin-top: 8px;")
                if not px.masks:
                    solara.Text(t("overview_tile.masks_none"), style=_NOTE)
                else:
                    _table((t("overview_tile.col_mask"), t("overview_tile.col_mode"), ""), [
                        [name, t("steps.prepare.mask_mode_keep" if mode == "keep" else "steps.prepare.mask_mode_exclude"),
                         [] if eyes.get(f"mask:{name}") is None else [_eye_cell(eyes[f"mask:{name}"], name)]]
                        for name, mode in px.masks])


def _eye_cell(eye, title):
    """``_eye`` as a table cell's element (``_table`` takes elements)."""
    on, on_click, busy = eye
    return rv.Html(tag="span", attributes={"title": title}, children=[
        solara.Button("", icon_name=action_icon("map_toggle", on), icon=True, text=True, x_small=True,
                      color="primary" if on else None, loading=busy, on_click=on_click)])


def _layers_menu(groups):
    """The Map layers button and its checklist. ``groups`` holds
    ``(title, action, rows)``: ``action`` None or ``(label, on_click)``, each
    row ``(label, sub, on, on_click, busy)``. The menu stays open on a click:
    the click that turns a layer on closes the whole dialog anyway."""
    n_on = sum(1 for _, _, rows in groups for row in rows if row[2])
    label = t("overview_tile.map_layers") + (f" ({format_number(n_on, 0)})" if n_on else "")
    activator = solara.Button(label, icon_name="mdi-layers-outline", v_on="menu.on", small=True, depressed=True,
                              outlined=not n_on, color="primary" if n_on else None,
                              children=[rv.Icon(right=True, small=True, children=["mdi-menu-down"])])
    with rv.Menu(offset_y=True, left=True, close_on_content_click=False, max_height=480,
                 v_slots=[{"name": "activator", "variable": "menu", "children": activator}]):
        with rv.Card(style_="min-width: 340px; max-width: 420px; padding: 4px 0 8px;"):
            for i, (title, action, rows) in enumerate(groups):
                if i:
                    rv.Divider(class_="my-1")
                with solara.Row(style="padding: 6px 14px 0; align-items: center; justify-content: space-between;"):
                    solara.Text(title, style=f"{_NOTE} text-transform: uppercase; letter-spacing: 0.05em;")
                    if action is not None:
                        solara.Button(action[0], text=True, x_small=True, color="primary", on_click=action[1])
                for text, sub, on, on_click, busy in rows:
                    with solara.Div(style="padding: 2px 14px;"):
                        rv.Checkbox(label=text, v_model=on, on_v_model=lambda _v, f=on_click: f(), dense=True,
                                    hide_details=True, disabled=busy, class_="mt-0 pt-0")
                        if sub:
                            solara.Text(sub, style=f"{_NOTE} display: block; padding-left: 32px;")


def _period_id_label(periods, period_id: str) -> str:
    """A period by its id; a review copy may hold earlier periods the project
    does not, whose ids are ``<start>_<end>``."""
    if period_id in periods:
        return period_label(periods[period_id])
    start, _, end = period_id.partition("_")
    return t("steps.period.range", start=start, end=end) if end else period_id


def _growth_range(row, periods) -> str:
    first, last = (_period_id_label(periods, pid) for pid in (row.first_period, row.last_period))
    return first if row.first_period == row.last_period else t("overview_tile.growth_range", first=first, last=last)


def _clusters_tab(summary, px, nearby, grouping_on: bool, growth, measure: str, periods, on_review,
                  loaded: bool):
    """Clusters to alerts, the nearby alerts (found by distance, saved as a
    group) and the alerts linked to earlier loss."""
    small = None if px.clusters is None or px.clusters_kept is None else px.clusters - px.clusters_kept
    capped = None if px.clusters_kept is None else px.clusters_kept - summary.n_alerts
    rows = [
        ("formed", t("overview_tile.cl_formed"), px.clusters, False, False, None),
        ("small", t("overview_tile.cl_small"), small, True, True, None),
    ]
    if capped:
        rows.append(("cap", t("overview_tile.cl_over_cap"), capped, True, True, None))
    rows += [
        ("alerts", t("overview_tile.cl_alerts"), summary.n_alerts, False, False, None),
        ("reviewed", t("overview_tile.cl_reviewed"), summary.n_reviewed, False, True, None),
    ]
    with _pair():
        with _column():
            with _card(t("overview_tile.card_clusters"), t("overview_tile.clusters_note")):
                _funnel(rows)
                if px.clusters is None:
                    solara.Text(t("overview_tile.not_recorded"), style=_NOTE)
                if nearby is not None and nearby.n_alerts:
                    solara.Text(t("overview_tile.card_nearby"), style=f"{_TITLE} margin-top: 8px;")
                    solara.Text(t("overview_tile.nearby_note", radius=format_number(nearby.radius_m, 0)),
                                style=_NOTE)
                    found = format_number(nearby.n_alerts - nearby.n_alone, 0)
                    _table(("", t("overview_tile.col_groups"), t("overview_tile.table_alerts")), [
                        [t("overview_tile.nearby_found"), format_number(nearby.n_clustered, 0),
                         t("overview_tile.nearby_of", grouped=found, alerts=format_number(nearby.n_alerts, 0),
                           largest=format_number(nearby.largest, 0))],
                        [t("overview_tile.nearby_saved_row"), format_number(nearby.n_groups, 0),
                         format_number(nearby.n_grouped, 0)],
                    ])
                    if not grouping_on:
                        solara.Text(t("overview_tile.nearby_off"), style=_NOTE)
        with _column():
            with _card(t("overview_tile.card_linked"), t("overview_tile.linked_note")):
                if growth is None:
                    return
                n = growth.n_growth + growth.n_growth_small
                if not n:
                    solara.Text(t("overview_tile.linked_none"), style=_NOTE)
                    return
                headline = (tn("overview_tile.growth_headline_count", n)
                            if measure == charts.MEASURE_ALERTS
                            else tn("overview_tile.growth_headline", n, ha=format_number(growth.area_ha, 1)))
                solara.Text(headline, style=_VALUE)
                split = t("overview_tile.growth_split", growth=format_number(growth.n_growth, 0),
                          small=format_number(growth.n_growth_small, 0))
                reviewed = tn("overview_tile.growth_reviewed", growth.n_reviewed)
                solara.Text(f"{split} · {reviewed}", style=_NOTE)
                if growth.rows:
                    _table((t("overview_tile.growth_period"), t("overview_tile.table_alerts"),
                            t("overview_tile.growth_confirmed"), t("overview_tile.growth_added"), ""), [
                        [_growth_range(row, periods), format_number(row.n, 0), format_number(row.confirmed_ha, 1),
                         format_number(row.added_ha, 1),
                         [solara.Button(t("overview_tile.growth_review"), x_small=True, color="primary",
                                        outlined=True, disabled=not loaded,
                                        on_click=lambda alert_id=row.alert_id: on_review(alert_id))]]
                        for row in growth.rows])


def _files_tab(export_card, rows, periods, summary_file, all_periods: bool):
    # stacked, not paired: the summary table needs the width for its eight columns
    with solara.Column(style="gap: 10px;"):
        with solara.Div():
            if export_card is not None:
                # A child of a reacton element, so the raw card is part of the rendered tree.
                rv.Html(tag="div", children=[export_card])
            solara.Text(t("overview_tile.export_all_note" if all_periods else "overview_tile.export_pick_all"),
                        style=_NOTE)
        if not all_periods:
            return
        with _card(t("overview_tile.card_summary"), t("overview_tile.summary_note")):
            _table((t("overview_tile.col_period"), t("overview_tile.table_alerts"),
                    t("overview_tile.col_confirmed_ha"), t("overview_tile.col_reviewed"),
                    t("overview_tile.col_source_px"), t("overview_tile.col_clusters"),
                    t("overview_tile.col_groups"), t("overview_tile.col_linked")), [
                [rv.Html(tag="span", style_="white-space: nowrap;",
                         children=[_period_id_label(periods, row.period_id)]),
                 format_number(row.n_alerts, 0), format_number(row.confirmed_ha, 1),
                 _share(row.n_reviewed / row.n_alerts if row.n_alerts else 0.0), _count(row.source_px),
                 _count(row.clusters), format_number(row.groups, 0), _count(row.linked)]
                for row in rows])
            if summary_file is not None:
                rv.Html(tag="div", children=[summary_file])


def _tab_label(key: str, count=None) -> str:
    label = t(f"overview_tile.tab_{key}")
    return label if count is None else f"{label} ({format_number(count, 0)})"


class _Runs:
    """The read counter: only the newest read may publish its numbers."""

    __slots__ = ("current",)

    def __init__(self):
        self.current = 0


@dataclass(frozen=True, eq=False)
class _Read:
    """What one read publishes. ``growth`` is None when the Linked card could
    not be read, ``nearby`` when the Nearby alerts could not; ``alerts`` is
    every period's alerts with a ``period`` column, for All periods only.
    Compared by identity: a new read is always news."""

    key: object = None
    summary: object = None
    history: tuple = ()
    failure: Optional[str] = None
    growth: object = None
    nearby: object = None
    rows: tuple = ()
    alerts: object = None


_NOTHING = _Read()


def _all_alerts(project, read_alerts):
    """Every period set's alerts with a ``period`` column, and the summed
    growth and nearby counts. Blocking: the worker's."""
    import pandas as pd

    frames, growth, nearby = [], [], []
    for period, alert_set in overview_data.period_sets(project):
        alerts = read_alerts(project, alert_set)
        if alerts is None:
            continue
        frames.append(alerts.assign(period=period.id))
        growth.append(growth_index.growth_counts(alerts))
        try:
            nearby.append(overview_data.nearby_of(project, alert_set, alerts))
        except Exception:  # noqa: BLE001 - one set's count, not the card
            logger.exception("could not count the nearby alerts of set %s", alert_set.id)
    gdf = pd.concat(frames, ignore_index=True) if frames else None
    return gdf, growth_index.sum_growth(growth), overview_data.sum_nearby(nearby)


@solara.component
def OverviewTile(app_state, map_, layers, dismiss=None):
    """The dashboard of the alert set the selector picks, or of every period.

    Parameters
    ----------
    app_state : AppState
        The session state: the project (its active set), ``alerts_gdf`` (the
        active set's alerts, loaded by the shell -- the export card reads them)
        and the navigation reactives.
    map_ : SepalMap
        The single shared map, for the Pixels tab's source and mask layers.
    layers : MapLayers
        The session's map-layer registry. A layer button is held while its own
        layer is being built (``layers.inflight``).
    dismiss : callable, optional
        Closes this dialog. "Review alerts" closes it before it sends the user
        to Analysis, and a layer turned on closes it on its way on.
    """
    measure, set_measure = solara.use_state(charts.MEASURE_AREA)
    admin_level, set_admin_level = solara.use_state(ADMIN_COUNTRY)
    tab, set_tab = solara.use_state(0)
    all_on, set_all_on = solara.use_state(False)
    read, set_read = solara.use_state(_NOTHING)
    runs = solara.use_memo(_Runs, [])
    notifications = use_notifications()

    p = app_state.project.value
    s = active_set(p)
    all_periods = all_on and s is not None
    # Read in the body, so a theme toggle or a language switch re-renders the
    # view AND re-draws its figures (both are memo dependencies below).
    theme = charts.theme_for(app_state.dark.value)
    locale = current_locale()

    keys = overview_data.history_keys(p)
    key = (overview_data.ALL_PERIODS, *keys) if all_periods else overview_data.summary_key(p, s)
    # Each answer split by its own question's codes (legacy labels read back
    # to codes). The summary key carries the questions.
    qmap = {q.key: q for q in p.questions} if p is not None else {}

    def _split(key, value):
        q = qmap.get(key)
        return questions_mod.codes(q, value) if q is not None else []

    def _start_read():
        if p is None or s is None:
            return
        runs.current += 1
        run, project, alert_set, everything = runs.current, p, s, all_periods

        def _work():
            # On a worker (parquet): the summary on screen, then one per period
            # for the trend. Only the newest read publishes.
            try:
                summary = (overview_data.all_summary_of(project, split_of=_split) if everything
                           else overview_data.summary_of(project, alert_set, split_of=_split))
                history = overview_data.history_of(project, split_of=_split)
            except Exception as exc:  # noqa: BLE001 - said in place, where the numbers would be
                logger.exception("could not read the alerts of set %s", alert_set.id)
                if runs.current == run:
                    set_read(_Read(key=key, failure=error_text(exc)))
                return
            # The Linked and Nearby cards read the alerts' polygons (Linked the
            # other sets' stores too); without them the dashboard is still
            # whole, so a failure here is only logged.
            alerts = growth = nearby = everyone = None
            try:
                if everything:
                    everyone, growth, nearby = _all_alerts(project, set_store.load_set_alerts)
                else:
                    alerts = set_store.load_set_alerts(project, alert_set)
                    if alerts is not None:
                        growth = growth_index.set_histories(project, alert_set.id, alerts)
            except Exception:  # noqa: BLE001 - one card, not the dashboard
                logger.exception("could not read the history of set %s", alert_set.id)
            try:
                if alerts is not None:
                    nearby = overview_data.nearby_of(project, alert_set, alerts)
            except Exception:  # noqa: BLE001 - one card, not the dashboard
                logger.exception("could not count the nearby alerts of set %s", alert_set.id)
            try:
                rows = overview_data.period_rows(project, history)
            except Exception:  # noqa: BLE001 - one table, not the dashboard
                logger.exception("could not build the summary of every period")
                rows = ()
            if runs.current == run:
                set_read(_Read(key=key, summary=summary, history=history, growth=growth, nearby=nearby,
                               rows=rows, alerts=everyone))

        spawn_in_context(_work)

    # The nearby distance is a dependency too: an edit in the Evaluation step recounts the chains.
    radius = overview_data.nearby_radius(p, s) if p is not None and s is not None else None
    solara.use_effect(_start_read, [key, keys, radius])

    fresh = read.summary is not None and read.key == key
    summary, history = read.summary, read.history
    series = tuple((period_label(period), item, item.n_reviewed < item.n_alerts)
                   for period, _, item in history) if fresh else ()
    questions = tuple(p.questions) if p is not None else ()
    # Drawn only when their inputs change, never on the project commit an auto-save makes.
    figures = solara.use_memo(
        lambda: (overview_data.draw_figures(summary, series, measure=measure, theme=theme,
                                            label_of=lambda q, code: questions_mod.label(q, code),
                                            questions=questions) if fresh else {}),
        dependencies=[summary, series, measure, theme["surface"], locale, questions],
    )

    sets = overview_data.period_sets(p)
    # The export card is built only when its inputs change, so a re-render (a
    # measure click) keeps a write in progress.
    if all_periods:
        gdf = read.alerts if fresh else None
        folder = str(paths.project_dir(p.name))
        sources = tuple(sorted({x for _, a in sets for x in a.sources}, key=str.casefold))
        start = min(period.start for period, _ in sets).isoformat() if sets else ""
        end = max(period.end for period, _ in sets).isoformat() if sets else ""
        stores = [set_store.store_path(p, a) for _, a in sets]
        store = str(max(stores, key=lambda x: x.stat().st_mtime if x.exists() else 0)) if stores else None
        set_ids = tuple(a.id for _, a in sets)

        def _growth(g):
            import pandas as pd

            parts = [growth_index.with_growth_columns(p, sid, part.reset_index(drop=True))
                     for sid, part in g.groupby("set_id", sort=False)] if "set_id" in g.columns else []
            return pd.concat(parts, ignore_index=True) if parts else g
    else:
        gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
        period = p.periods.get(s.period_id) if s is not None else None
        folder = str(set_store.set_folder(p, s)) if s is not None else None
        sources = tuple(s.sources) if s is not None else ()
        start = period.start.isoformat() if period is not None else ""
        end = period.end.isoformat() if period is not None else ""
        store = str(set_store.store_path(p, s)) if s is not None else None
        set_ids = (s.id,) if s is not None else ()

        def _growth(g):
            return growth_index.with_growth_columns(p, s.id, g)
    has_alerts = gdf is not None and len(gdf) > 0
    last_card = solara.use_ref(None)
    export_card = solara.use_memo(
        lambda: build_export_card(gdf, folder, list(sources), start, end, p.name, questions, store=store,
                                  scope=last_card.current.scope if last_card.current is not None else None,
                                  growth=_growth)
        if s is not None and has_alerts else None,
        [gdf, folder, sources, start, end, questions, store, set_ids],
    )
    if export_card is not None:
        last_card.current = export_card

    summary_text = overview_data.summary_csv(p, read.rows) if fresh and read.rows else None
    summary_path = str(paths.project_dir(p.name) / overview_data.SUMMARY_CSV_NAME) if p is not None else None
    summary_file = solara.use_memo(
        lambda: build_summary_file(summary_text, summary_path, p.name, len(read.rows)) if summary_text else None,
        [summary_text, summary_path],
    )

    def _relabel_export():
        if export_card is not None:
            relabel_export_card(export_card)
        if summary_file is not None:
            summary_file.relabel()

    # Their labels follow a language switch on the same widgets (``locale`` read above).
    solara.use_effect(_relabel_export, [export_card, summary_file, locale])

    def _review_alerts():
        # Analysis is behind this dialog: close it first.
        if dismiss is not None:
            dismiss()
        app_state.current_page_view.value = "analysis_tile"

    def _review_growth(alert_id):
        # Analysis opens on this set's alert of the history; the position is
        # its row in the alerts the session holds.
        position = set_store.position_of(gdf, alert_id)
        if position is None:
            return

        def _go():
            if dismiss is not None:
                dismiss()
            set_store.go_to_alert(app_state, p.name, s.id, alert_id, position)

        # an alert change: over unsaved work in Analysis it asks first, in a dialog over this one
        app_state.guard(_go)

    def _switch(reactive):
        """Flip one of the shell's layer booleans. Turning one ON closes this
        dialog, because the map it just changed is behind it."""

        def _on():
            value = not reactive.value
            reactive.set(value)
            if value and dismiss is not None:
                dismiss()

        return _on

    def _show(key, build, content_id, legend):
        # The Prepare step's pattern: never hide a build still in flight.
        if layers.is_on(key):
            if key not in layers.inflight.value:
                layers.hide(key)
            return
        layers.show(key, build, content_id=content_id, notifier=notifications, legend=legend)
        if dismiss is not None:
            dismiss()

    inflight = layers.inflight.value
    on = layers.on.value
    eyes = {}
    if s is not None and not all_periods:
        pdir = paths.project_dir(p.name)
        period = p.periods.get(s.period_id)
        if has_alerts:
            eyes["alerts"] = (app_state.show_alerts.value, _switch(app_state.show_alerts),
                              alert_layers.SET_LAYER_KEY in inflight)
        if alert_layers.has_states(p, s):
            eyes["states"] = (app_state.show_unanalysable.value, _switch(app_state.show_unanalysable),
                              alert_layers.STATES_LAYER_KEY in inflight)
        for name in s.sources:
            art = p.artifacts.get(harm_key(s.period_id, name))
            if art is None or period is None:
                continue
            k = f"src:{name}"
            eyes[k] = (k in on, lambda k=k, name=name, art=art: _show(
                k, source_layers.harmonized_builder(map_, paths.resolve(pdir, art.rel_path), k,
                                                    source_layers.harmonized_name(name, period)),
                art.content_id, source_layers.harmonized_legend(k, name, period)), k in inflight)
        for m in s.masks_used:
            name, art = m.get("name", ""), p.artifacts.get(mask_key(m.get("name", "")))
            if art is None:
                continue
            k = f"mask:{name}"
            eyes[k] = (k in on, lambda k=k, name=name, art=art: _show(
                k, source_layers.mask_builder(map_, paths.resolve(pdir, art.rel_path), k,
                                              source_layers.mask_name(name)),
                art.content_id, source_layers.mask_layer_legend(p, name)), k in inflight)

    def _toggle(reactive, set_id):
        """Add or take ``set_id`` from one of the shell's set-id reactives;
        turning it on closes this dialog, as ``_switch`` does."""

        def _on():
            value = set_id not in reactive.value
            reactive.set(reactive.value | {set_id} if value else reactive.value - {set_id})
            if value and dismiss is not None:
                dismiss()

        return _on

    def _menu_groups():
        """The Map layers menu: for one set, its own layers then every other
        period's alerts; for All periods, every period's alerts and pixels."""
        shown, states = app_state.shown_sets.value, app_state.states_sets.value
        others = [(period, a) for period, a in sets if period.id != s.period_id and a.n_alerts]

        def _sub(a):
            return f"{' + '.join(a.sources)} · {tn('steps.sets.alerts', a.n_alerts)}"

        def _alerts_row(period_id, a):
            label = t("overview_tile.menu_alerts", period=source_layers.period_text(period_id))
            if a.id == s.id:
                return (label, _sub(a), app_state.show_alerts.value, _switch(app_state.show_alerts),
                        alert_layers.SET_LAYER_KEY in inflight)
            return (label, _sub(a), a.id in shown, _toggle(app_state.shown_sets, a.id),
                    alert_layers.earlier_key(a) in inflight)

        def _states_row(period_id, a):
            label = t("overview_tile.menu_states", period=source_layers.period_text(period_id))
            sub = t("overview_tile.menu_states_sub")
            if a.id == s.id:
                return (label, sub, app_state.show_unanalysable.value, _switch(app_state.show_unanalysable),
                        alert_layers.STATES_LAYER_KEY in inflight)
            return (label, sub, a.id in states, _toggle(app_state.states_sets, a.id),
                    alert_layers.states_key(a) in inflight)

        other_ids = frozenset(a.id for _, a in others)
        if other_ids <= shown:
            show_all = (t("overview_tile.hide_all"), lambda: app_state.shown_sets.set(shown - other_ids))
        else:
            def _all():
                app_state.shown_sets.set(shown | other_ids)
                if dismiss is not None:
                    dismiss()

            show_all = (t("overview_tile.show_all"), _all)
        if all_periods:
            every = [(s.period_id, s) if period.id == s.period_id else (period.id, a) for period, a in sets]
            rows = [_alerts_row(pid, a) for pid, a in every if a.n_alerts]
            rows += [_states_row(pid, a) for pid, a in every if alert_layers.has_states(p, a)]
            return [(t("overview_tile.every_period"), show_all if len(others) > 1 else None, rows)]
        mine = [_alerts_row(s.period_id, s)] if has_alerts else []
        if alert_layers.has_states(p, s):
            mine.append(_states_row(s.period_id, s))
        groups = [(t("overview_tile.this_set", period=source_layers.period_text(s.period_id)), None, mine)]
        if others:
            groups.append((t("overview_tile.other_periods"), show_all if len(others) > 1 else None,
                           [_alerts_row(period.id, a) for period, a in others]))
        return groups

    with solara.Column(style="padding: 0 8px 12px; gap: 10px;"):
        with solara.Row(style="gap: 12px; align-items: center; flex-wrap: wrap;"):
            if s is not None:
                with solara.Div(style="flex: 1 1 300px; min-width: 240px;"):
                    n_all = sum(a.n_alerts for _, a in sets)
                    SetSelector(app_state=app_state, all_picked=all_periods, on_all=set_all_on,
                                all_text=t("overview_tile.all_periods", sets=tn("overview_tile.n_sets", len(sets)),
                                           alerts=tn("steps.sets.alerts", n_all)))
                _segment(((charts.MEASURE_AREA, t("overview_tile.measure_area")),
                          (charts.MEASURE_ALERTS, t("overview_tile.measure_alerts"))), measure, set_measure)
            rv.Spacer()
            with rv.Html(tag="span", attributes={"title": t("overview_tile.review_pick_one")} if all_periods else {}):
                solara.Button(t("overview_tile.review_button"), color="primary", small=True, depressed=True,
                              disabled=s is None or not s.n_alerts or all_periods, on_click=_review_alerts)
        if s is None:
            solara.Text(t("overview_tile.no_set"), style=_NOTE)
            return
        status = Status.CURRENT if all_periods else set_status(p, paths.project_dir(p.name), s)
        if status is Status.STALE:
            solara.Text(t("steps.overview.stale"), classes=["warning--text"], style=_NOTE)
        elif status is Status.MISSING:
            solara.Text(t("steps.overview.missing"), classes=["warning--text"], style=_NOTE)
        if read.failure is not None and read.key == key:
            solara.Text(t("overview_tile.read_failed", error=read.failure), classes=["warning--text"], style=_NOTE)
        elif not fresh:
            solara.Text(t("overview_tile.loading"), style=_NOTE)
        elif not summary.n_alerts:
            solara.Text(t("overview_tile.empty_set"), style=_NOTE)
        if not fresh or not summary.n_alerts:
            if export_card is not None:
                rv.Html(tag="div", children=[export_card])
            return

        shown = [k for k in TABS if k != "answers" or questions]
        current = shown[min(tab, len(shown) - 1)]
        counts = {"answers": len(questions), "clusters": summary.n_alerts}
        groups = _menu_groups()
        # the Map layers button sits right of the headline: the tabs keep the whole width
        with solara.Row(style="gap: 12px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap;"):
            _headline(summary, history, measure, all_periods)
            if any(rows for _, _, rows in groups):
                _layers_menu(groups)
        rv.Tabs(v_model=shown.index(current), on_v_model=set_tab, show_arrows=True, height=36,
                children=[rv.Tab(children=[_tab_label(k, counts.get(k))]) for k in shown])
        if current == "status":
            _status_tab(summary, figures, measure)
        elif current == "time":
            _time_tab(history, figures, measure)
        elif current == "place":
            _place_tab(summary, figures, admin_level, set_admin_level)
        elif current == "answers":
            _answers_tab(summary, figures, measure, questions)
        elif current == "pixels":
            px = (overview_data.sum_pixels(overview_data.pixels_of(p, a) for _, a in sets) if all_periods
                  else overview_data.pixels_of(p, s))
            _pixels_tab(px, float(p.grid.resolution) if p.grid is not None else 0.0, eyes)
        elif current == "clusters":
            px = (overview_data.sum_pixels(overview_data.pixels_of(p, a) for _, a in sets) if all_periods
                  else overview_data.pixels_of(p, s))
            _clusters_tab(summary, px, read.nearby, grouping.effective(p, s) is not None, read.growth, measure,
                          p.periods, _review_growth, has_alerts)
        else:
            _files_tab(export_card, read.rows, p.periods, summary_file, all_periods)
