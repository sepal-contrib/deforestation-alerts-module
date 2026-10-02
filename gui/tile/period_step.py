"""Period step (spec §6.4): the project's periods as a list.

Each row shows start → end, its search window (``start − look-back … end``),
the number of its alert sets and §5.3's out-of-date badges: the sources whose
download or harmonized file is out of date in that period, and its sets that
are out of date or whose files are missing. They come from the manifest plus
one ``os.stat`` per file, never a raster read, and a period's mask checks run
once for all its sources and sets, so a render on a worker's publish stays
cheap.

- **Select** (the row's radio) sets ``active_period``; the auto-save keeps it.
- **New period** opens ``PeriodModal`` pre-filled from the latest period; the
  new period copies the mask choices of the period with the latest start and
  becomes active (``periods.new_period``).
- **Edit** changes the dates of a period without sets (a period with sets is
  refused with a toast). New dates give it a new id; its downloads and
  harmonized files are deleted.
- **Delete** is refused for a period with sets that is not the latest;
  otherwise a confirmation names what goes, then the period, its sets, their
  records and the period's whole folder are removed.
- Edit and delete are refused while a writer of the project runs (a download,
  Integrate, a review Save), like source and mask deletes.
- **Look-back** is a project field edited here (Apply); it makes the raw
  downloads out of date through their window.

Every change is tried on a deep copy first, so a refused one neither
publishes nor saves (slice-1 ruling #69), then applied through
``update_project``. Files are deleted only after that returned True, outside
the state lock, once the layers drawn from them have left the map.

The step is mounted for the whole session: ``PeriodStep`` is a hook-free gate
and the list is a child keyed by the project and its load signal, so a
switched project never shows another project's typed look-back.
"""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import Status, period_set_statuses
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import cleanup, paths, periods
from deforestation_alerts.project.models import period_id
from gui.i18n import plural, t
from gui.store.project_writers import is_writing
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.help import StepIntro
from gui.widget.period_modal import PeriodModal
from gui.widget.product_table import ProductTable


def period_label(period) -> str:
    return t("steps.period.range", start=period.start.isoformat(), end=period.end.isoformat())


def badges(p, pdir, period) -> dict:
    states = list(period_set_statuses(p, pdir, period).values())
    stale_sources = 0
    if p.grid is not None:  # without a grid nothing was harmonized (an AOI change dropped every record)
        stale_sources = sum(1 for r in plan_prepare(p, pdir, period) if Status.STALE in (r.raw, r.harmonized))
    return {"sets": len(states), "stale_sets": states.count(Status.STALE),
            "missing_sets": states.count(Status.MISSING), "stale_sources": stale_sources}


def period_rows(p, pdir, *, select, edit, delete) -> list:
    rows = []
    for period in periods.periods_newest_first(p):
        b = badges(p, pdir, period)
        ws, we = period.search_window(p.look_back_days)
        notes = [{"text": t("steps.period.window", start=ws.isoformat(), end=we.isoformat()), "tone": None}]
        if b["stale_sources"]:
            notes.append({"text": plural(b["stale_sources"], "steps.period.note_sources_stale_one",
                                         "steps.period.note_sources_stale_other"), "tone": "warning"})
        if b["stale_sets"]:
            notes.append({"text": plural(b["stale_sets"], "steps.period.note_sets_stale_one",
                                         "steps.period.note_sets_stale_other"), "tone": "warning"})
        if b["missing_sets"]:
            notes.append({"text": plural(b["missing_sets"], "steps.period.note_sets_missing_one",
                                         "steps.period.note_sets_missing_other"), "tone": "error"})
        if b["sets"]:
            notes.append({"text": t("steps.period.note_frozen"), "tone": None})
        if b["stale_sources"] or b["stale_sets"]:
            state = {"type": "chip", "value": t("steps.period.state_stale"), "color": "warning"}
        elif b["missing_sets"]:
            state = {"type": "chip", "value": t("steps.period.state_missing"), "color": "grey"}
        else:
            state = {"value": "—", "muted": True}
        rows.append({
            "key": period.id,
            "cells": [{"value": period_label(period)}, {"value": str(b["sets"])}, state],
            "notes": notes,
            "actions": [
                {"kind": "select", "is_on": period.id == p.active_period,
                 "on_click": (lambda pid=period.id: select(pid))},
                {"kind": "edit", "on_click": (lambda pid=period.id: edit(pid))},
                {"kind": "delete", "on_click": (lambda pid=period.id: delete(pid))},
            ],
        })
    return rows


def dropped_content(p, keys) -> set:
    """The content ids of the records about to be dropped: the layers drawn from them must leave the map."""
    return {p.artifacts[k].content_id for k in keys if k in p.artifacts}


def hide_dropped_layers(layers, content_ids, set_ids=()) -> None:
    """Take off the map every layer drawn from a dropped file (a period's
    harmonized rasters) and the Overview layers of removed sets, before the
    files are deleted."""
    removed = {f"overview:set:{sid}" for sid in set_ids}
    for key in sorted(layers.on.value):
        content = layers.content_of(key)
        if key in removed or (content is not None and content in content_ids):
            layers.hide(key)


@solara.component
def PeriodStep(app_state, layers):
    """Hook-free gate: every hook lives in the keyed child below."""
    p = app_state.project.value
    if p is None or not p.sources:
        solara.Info(t("workflow.lock_period"))
        return
    _PeriodList(app_state=app_state, layers=layers).key(
        f"periods-{p.name}-{app_state.project_loaded_signal.value}")


@solara.component
def _PeriodList(app_state, layers):
    notifications = use_notifications()
    p = app_state.project.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    look_back, set_look_back = solara.use_state(str(p.look_back_days) if p is not None else "")
    if p is None:
        # Closed: this child renders once more before the gate unmounts it.
        # Every hook ran already; reading the closed project would raise.
        return
    name, pdir = p.name, paths.project_dir(p.name)

    def _change(fn):
        """``fn(project) -> (error, files)``: tried on a deep copy, then applied
        to the live project. Returns ``(error, files, applied)``."""
        live = app_state.project.value
        if live is None or live.name != name:
            return None, [], False
        error = fn(live.model_copy(deep=True))[0]
        if error:
            return error, [], False
        out = []
        if not app_state.update_project(name, lambda q: out.append(fn(q))):
            return None, [], False
        error, files = out[0]
        return error, files, error is None

    def _select(pid):
        if pid != p.active_period:
            _change(lambda q: (periods.select_period(q, pid), []))

    def _new():
        set_editing(None)
        modal_open.set(True)

    def _edit(pid):
        if periods.sets_of(p, pid):  # the live project is checked again when the edit is applied
            notifications.warning(t("steps.period.error_locked"))
        elif is_writing(name):
            notifications.warning(t("steps.period.busy"))
        else:
            set_editing(pid)
            modal_open.set(True)

    def _remove(pid, gone, content_ids, set_ids=()):
        hide_dropped_layers(layers, content_ids, set_ids)
        cleanup.delete_files(pdir, gone)
        cleanup.delete_period_dir(pdir, pid)

    def _submit(start, end):
        if editing is None:
            error, _, applied = _change(lambda q: (periods.new_period(q, start, end), []))
            if error:
                return f"steps.period.error_{error}"
            if applied:
                notifications.success(t("steps.period.created"))
            return None
        if is_writing(name):
            return "steps.period.busy"
        pid, live = editing, app_state.project.value
        if live is None or live.name != name:
            return None  # closed or switched meanwhile: nothing to change
        ids = dropped_content(live, cleanup.period_artifact_keys(live, pid))
        error, gone, applied = _change(lambda q: periods.edit_period(q, pid, start, end))
        if error:
            return f"steps.period.error_{error}"
        if applied and period_id(*periods.parse_dates(start, end)[1]) != pid:  # same dates: nothing moved
            _remove(pid, gone, ids)
            notifications.success(t("steps.period.saved"))
        return None

    def _ask_delete(pid):
        error = periods.delete_error(p, pid)  # checked again on the live project by _delete
        if is_writing(name):
            notifications.warning(t("steps.period.busy"))
        elif error:
            notifications.warning(t(f"steps.period.error_{error}"))
        else:
            set_pending_delete(pid)

    def _delete():
        pid = pending_delete
        set_pending_delete(None)
        live = app_state.project.value
        if pid is None or live is None or live.name != name:
            return
        if is_writing(name):
            notifications.warning(t("steps.period.busy"))
            return
        set_ids = periods.sets_of(live, pid)
        ids = dropped_content(live, cleanup.period_artifact_keys(live, pid) + cleanup.set_artifact_keys(live, set_ids))
        error, gone, applied = _change(lambda q: periods.delete_period(q, pid))
        if error:
            notifications.warning(t(f"steps.period.error_{error}"))
        elif applied:
            _remove(pid, gone, ids, set_ids)
            notifications.success(t("steps.period.deleted"))

    def _apply_look_back():
        error, _, applied = _change(lambda q: (periods.set_look_back(q, look_back), []))
        if error:
            notifications.error(t(f"steps.period.error_{error}"))
        elif applied:
            notifications.success(t("steps.period.look_back_saved"))

    with solara.Column(style="gap:12px;"):
        solara.Markdown(f"### {t('steps.period.title')}")
        StepIntro(t("steps.period.description"), t("steps.period.info_header"), t("steps.period.info_md"))
        with solara.Row(style="gap:8px;align-items:flex-start;"):
            rv.TextField(label=t("steps.period.look_back"), type="number", v_model=look_back,
                         on_v_model=set_look_back, dense=True, outlined=True,
                         hint=t("steps.period.look_back_hint"), persistent_hint=True)
            solara.Button(t("steps.period.apply"), small=True, outlined=True,
                          disabled=str(look_back).strip() == str(p.look_back_days), on_click=_apply_look_back)
        ProductTable(
            title=t("steps.period.list_title"),
            columns=[{"label": t("steps.period.col_period")},
                     {"label": t("steps.period.col_sets"), "width": "40px"},
                     {"label": t("steps.period.col_status"), "width": "84px"}],
            rows=period_rows(p, pdir, select=_select, edit=_edit, delete=_ask_delete),
            empty_text=t("steps.period.empty"),
        )
        solara.Button(t("steps.period.new"), icon_name="mdi-plus", color="primary", small=True, on_click=_new)
    PeriodModal(open_=modal_open, app_state=app_state, on_submit=_submit, editing=editing)
    pending = p.periods.get(pending_delete) if pending_delete else None
    doomed = periods.sets_of(p, pending_delete) if pending is not None else []
    reviewed = sum(p.alert_sets[s].n_reviewed for s in doomed)
    ConfirmDialog(
        open=pending is not None,
        on_cancel=lambda: set_pending_delete(None),
        on_confirm=_delete,
        title=t("steps.period.delete_title", period=period_label(pending)) if pending is not None else None,
        message=(plural(len(doomed), "steps.period.delete_sets_one", "steps.period.delete_sets_other",
                        reviewed=reviewed) if doomed else t("steps.period.delete_message")),
        confirm_label=t("common.delete"),
        details=[t("steps.period.delete_set_line", sources=" + ".join(p.alert_sets[s].sources),
                   n=p.alert_sets[s].n_alerts, reviewed=p.alert_sets[s].n_reviewed) for s in doomed] or None,
    )
