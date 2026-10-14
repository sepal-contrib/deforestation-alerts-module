"""The Analysis filter's control in the screen's top bar (spec §8.3): a funnel
button that opens a menu (*Attribute*, *Values* with their counts, *Clear
filter* / *Apply*) and, while a filter is on, a closable chip that names it
(``Evaluator: Ana``).

Architecture layer: view (raw-widget builder, no ``@solara.component``), built
once per session with the top bar (``context.build_topnav``). The filter is
``AnalysisState.alert_filter``, bound into ``AppState``; this module reads and
sets it through ``app_state`` only (``alert_filter``, ``set_alert_filter``),
so what follows a filter change -- the move to its start
(``analysis_tile.follow_filter``) and block A's counter -- runs as for any
other change. The attributes and their counts are read from the live alerts
each time the menu opens: a save that changes a status changes its count.

The menu is drawn at the app's root, outside the screen's type rules, so its
sizes and casing are inline (as the jump menu's, ``context._build_nav``).
"""

import threading
from types import SimpleNamespace

import ipyvuetify as v

from gui.i18n import format_number, t
from gui.scripts import set_store
from gui.scripts.alert_filter import AlertFilter, filter_columns, value_choices
from gui.widget.outside_click import OutsideClickWidget

#: How many values the chip names before it counts the rest ("Ana, Bruno +2").
CHIP_VALUES = 2

_BTN = "text-transform: none; letter-spacing: normal; font-size: 14px;"

#: The panel's menu content, for ``OutsideClickWidget``.
_PANEL_CLASS = "defo-filter-panel"


def _context(app_state):
    """``(alerts, questions)`` the filter is offered on: the active set's
    alerts only (another set's, until the new set's are published, are none)."""
    p = app_state.project.value
    if p is None:
        return None, []
    return set_store.active_set_alerts(p, app_state.alerts_gdf.value), list(p.questions)


def _choice_of(column, gdf, questions):
    return next((c for c in filter_columns(gdf, questions) if c.column == column), None)


def filter_label(app_state, f, limit=CHIP_VALUES) -> str:
    """The chip's words for filter ``f``: the attribute's label and the
    values' labels, in the menu's order (``Evaluator: Ana, Bruno +1``).
    ``limit=None`` names every value (the chip's tooltip)."""
    gdf, questions = _context(app_state)
    choice = _choice_of(f.column, gdf, questions)
    known = value_choices(gdf, choice, questions) if choice is not None else []
    names = [label for value, label, _ in known if value in f.values]
    names += sorted((x for x in f.values if x not in {value for value, _, _ in known}), key=str.casefold)
    shown = ", ".join(names[:limit])
    if limit is not None and len(names) > limit:
        shown = t("analysis_tile.filter.more", values=shown, count=format_number(len(names) - limit))
    return t("analysis_tile.filter.chip", attribute=choice.label if choice is not None else f.column, values=shown)


def build_filter_menu(app_state) -> SimpleNamespace:
    """The funnel button, its menu and the chip. Returns
    ``SimpleNamespace(root, button, menu, attribute, values, empty, apply,
    clear, chip, outside)``; ``root`` goes into the top bar, left of the set
    selector."""
    button = v.Btn(icon=True, v_on="menu.on", class_="defo-filter-btn",
                   attributes={"aria-label": t("analysis_tile.filter.button"),
                               "title": t("analysis_tile.filter.button")},
                   children=[v.Icon(children=["mdi-filter-outline"])])
    title = v.Html(tag="div", style_="font-size: 12px; opacity: 0.7;", children=[t("analysis_tile.filter.button")])
    attribute = v.Select(label=t("analysis_tile.filter.attribute"), items=[], v_model=None,
                         dense=True, outlined=True, hide_details=True)
    values = v.Select(label=t("analysis_tile.filter.values"), items=[], v_model=[], multiple=True,
                      chips=True, small_chips=True, deletable_chips=True, dense=True, outlined=True,
                      hide_details=True)
    empty = v.Html(tag="div", style_="font-size: 12px; display: none;", children=[])
    clear = v.Btn(text=True, style_="height: 36px; " + _BTN, children=[t("analysis_tile.filter.clear")])
    apply = v.Btn(color="primary", depressed=True, style_="height: 36px; " + _BTN,
                  children=[t("analysis_tile.filter.apply")])
    body = v.Card(style_="width: 320px; padding: 12px; display: flex; flex-direction: column; gap: 10px;",
                  children=[
                      title, attribute, values, empty,
                      v.Html(tag="div", style_="display: flex; justify-content: flex-end; gap: 8px;",
                             children=[clear, apply]),
                  ])
    # Outside clicks are ``outside``'s to handle (below), not the menu's.
    menu = v.Menu(v_model=False, offset_y=True, nudge_bottom=4, close_on_content_click=False,
                  close_on_click=False, content_class=_PANEL_CLASS,
                  v_slots=[{"name": "activator", "variable": "menu", "children": button}],
                  children=[body])
    chip = v.Chip(small=True, close=True, outlined=True, class_="defo-filter-chip",
                  style_="display: none;", children=[""])
    # Set while the menu writes its own selects: the attribute's observer
    # must not read that as the reviewer's pick (``build_set_selector``'s rule).
    local = threading.local()

    def _fill_values(column, keep):
        """The values of ``column`` with their counts, ``keep`` selected (a
        value of the filter in force that no alert has any more stays listed,
        so the reviewer can take it out)."""
        gdf, questions = _context(app_state)
        choice = _choice_of(column, gdf, questions) if column else None
        found = value_choices(gdf, choice, questions) if choice is not None else []
        items = [{"text": t("analysis_tile.filter.value_count", value=label, count=format_number(n)),
                  "value": value} for value, label, n in found]
        listed = {value for value, _, _ in found}
        items += [{"text": x, "value": x} for x in sorted(keep - listed, key=str.casefold)]
        values.items = items
        values.v_model = [item["value"] for item in items if item["value"] in keep]

    def _on_open(change):
        if not change["new"]:
            return
        gdf, questions = _context(app_state)
        columns = filter_columns(gdf, questions)
        f = app_state.alert_filter
        column = f.column if f is not None and any(c.column == f.column for c in columns) else None
        local.syncing = True
        try:
            attribute.items = [{"text": c.label, "value": c.column} for c in columns]
            attribute.v_model = column
            _fill_values(column, set(f.values) if column is not None else set())
        finally:
            local.syncing = False
        empty.children = [] if columns else [t("analysis_tile.filter.no_columns")]
        empty.style_ = "font-size: 12px;" + ("" if not columns else " display: none;")
        attribute.disabled = values.disabled = apply.disabled = not columns

    def _on_attribute(change):
        if getattr(local, "syncing", False):
            return
        _fill_values(change["new"], set())

    def _on_apply(*_):
        column, chosen = attribute.v_model, frozenset(values.v_model or [])
        # An attribute with no value picked is no filter: Apply clears.
        app_state.set_alert_filter(AlertFilter(column, chosen) if column and chosen else None)
        menu.v_model = False

    def _on_clear(*_):
        app_state.clear_alert_filter()
        menu.v_model = False

    def _sync(*_):
        f = app_state.alert_filter
        button.children = [v.Icon(children=["mdi-filter" if f is not None else "mdi-filter-outline"])]
        button.color = "primary" if f is not None else ""
        chip.children = [filter_label(app_state, f) if f is not None else ""]
        # In a review copy the chip is the only statement of whose alerts
        # these are: its tooltip names the filter in full.
        chip.attributes = {"title": filter_label(app_state, f, limit=None)} if f is not None else {}
        chip.style_ = "" if f is not None else "display: none;"

    # A click outside while a select's list is open closes that list only:
    # the panel stays, so the reviewer can still press Apply. Vuetify would
    # close both on that click, so the browser decides (``outside_click``).
    outside = OutsideClickWidget(content_class=_PANEL_CLASS, activator_class="defo-filter-btn")
    outside.observe(lambda _: setattr(menu, "v_model", False), "outside_clicks")

    menu.observe(_on_open, "v_model")
    attribute.observe(_on_attribute, "v_model")
    apply.on_event("click", _on_apply)
    clear.on_event("click", _on_clear)
    chip.on_event("click:close", lambda *_: app_state.clear_alert_filter())
    # Built once per session: the subscription lives as long as the kernel.
    if app_state.alert_filter_reactive is not None:
        app_state.alert_filter_reactive.subscribe(_sync)
    # The label reads the alerts (attribute and value names): a filter set
    # before they are published (a review copy's start filter) is relabelled
    # when they arrive.
    app_state.alerts_gdf.subscribe(_sync)
    _sync()

    root = v.Html(tag="div", class_="defo-topnav-filter", children=[menu, chip, outside])
    return SimpleNamespace(root=root, button=button, menu=menu, attribute=attribute, values=values,
                           empty=empty, apply=apply, clear=clear, chip=chip, outside=outside)
