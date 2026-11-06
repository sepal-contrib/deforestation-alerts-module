"""The alert-set selector shared by Overview and Analysis (spec §9.3).

"period · sources · N alerts · M reviewed", newest first. A pick changes only
the project's ``active_set``; the shell's ``_ActiveSetLoader`` then loads that
set's ``alert_db.parquet`` on a worker thread and publishes it, at the set's
saved review position (``set_store.publish_set_alerts``). ``active_period``
stays where the Period step left it.

Two renderings of the same items: ``SetSelector`` for Solara views (Overview)
and ``build_set_selector`` for the raw ipyvuetify Analysis controls, which are
built once per session.
"""

import threading

import ipyvuetify as v
import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.project.periods import sets_newest_first
from gui.i18n import t, tn
from gui.widget.labels import Labels


def set_text(project, alert_set) -> str:
    period = project.periods.get(alert_set.period_id)
    when = (t("steps.period.range", start=period.start.isoformat(), end=period.end.isoformat())
            if period is not None else alert_set.period_id)
    return t("steps.sets.item", period=when, sources=" + ".join(alert_set.sources),
             alerts=tn("steps.sets.alerts", alert_set.n_alerts),
             reviewed=tn("steps.sets.reviewed", alert_set.n_reviewed))


def set_items(project) -> list:
    if project is None:
        return []
    return [{"text": set_text(project, s), "value": s.id} for s in sets_newest_first(project)]


def select_set(app_state, project_name: str, set_id: str) -> bool:
    """Make ``set_id`` the active set of the open project ``project_name``."""
    live = app_state.project.value
    if live is None or live.name != project_name or set_id not in live.alert_sets:
        return False
    if live.active_set == set_id:
        return True
    return app_state.update_project(project_name, lambda p: setattr(p, "active_set", set_id))


#: The value of the Overview's "All periods" item.
ALL_SETS = "__all__"


@solara.component
def SetSelector(app_state, all_text=None, all_picked: bool = False, on_all=None):
    """The selector for a Solara view. ``all_text``, when given, is an "All
    periods" item offered first (the Overview's, never Analysis's): picking it
    calls ``on_all(True)`` and leaves the active set alone; a set picked after
    it calls ``on_all(False)`` once the switch has happened. ``all_picked``
    shows that item as the selection."""
    p = app_state.project.value
    redraw, set_redraw = solara.use_state(0)
    items = set_items(p)
    if not items:
        return
    if all_text:
        items = [{"text": all_text, "value": ALL_SETS}, {"divider": True}, *items]

    def _picked(set_id):
        if not set_id:
            return
        if set_id == ALL_SETS:
            if on_all is not None:
                on_all(True)
            return
        moved = []

        def _move():
            moved.append(select_set(app_state, p.name, set_id))
            if on_all is not None:
                on_all(False)

        # A set switch changes the alert: it asks first over unsaved work.
        app_state.guard(_move)
        if not moved:
            # asked first (a dialog over this one): the select goes back to
            # the active set until the user answers
            set_redraw(lambda n: n + 1)

    rv.Select(label=t("steps.sets.label"), items=items,
              v_model=ALL_SETS if all_text and all_picked else p.active_set, on_v_model=_picked, dense=True,
              outlined=True, hide_details=True).key(f"sets-{redraw}")


def build_set_selector(app_state, *, labels=None, guard=None) -> v.Select:
    """The selector as a raw widget for the Analysis controls.

    It follows every project publish, whichever thread publishes (a worker's
    record included), by setting its own traits only, like
    ``build_set_status_banner``. A trait set by that sync fires the pick
    observer synchronously in the same thread; the thread-local ``syncing``
    flag keeps it from reading as the user's pick (it would otherwise select
    the set the sync just showed, undoing a newer change made meanwhile).

    ``labels`` is the screen's ``labels.Labels`` (a fresh one when none is
    given): its hook words the label and re-words the items. ``guard(move)``
    runs a set switch, asking first over unsaved work (the dock's prompt;
    ``app_state.guard`` without one)."""
    labels = labels if labels is not None else Labels()
    guard = guard if guard is not None else (lambda move: app_state.guard(move))
    select = v.Select(label="", items=[], v_model=None, dense=True, outlined=True,
                      hide_details=True, class_="px-2 pt-2", style_="display: none;")
    local = threading.local()

    def _sync(*_):
        p = app_state.project.value
        items = set_items(p)
        local.syncing = True
        try:
            select.items = items
            select.v_model = p.active_set if p is not None and items else None
        finally:
            local.syncing = False
        select.style_ = "" if items else "display: none;"

    def _picked(change):
        p = app_state.project.value
        if getattr(local, "syncing", False) or p is None or not change["new"]:
            return
        set_id = change["new"]
        # A set switch changes the alert: it asks first over unsaved work.
        guard(lambda: select_set(app_state, p.name, set_id))
        _sync()  # back on the active set while the prompt asks (or after *Stay*)

    select.observe(_picked, "v_model")
    app_state.project.subscribe(_sync)

    def _label():
        select.label = t("steps.sets.label")
        _sync()  # the items' text is translated by ``set_items``

    labels.add(_label)
    return select
