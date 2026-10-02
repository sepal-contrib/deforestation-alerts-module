"""The Project rail dialog (spec §4.1): the open project, New, Open, Duplicate
and Delete. Auto-save means there is no Save button (D11).

The list is read again whenever the open project is saved, installed or
closed, so its counts and times follow the session. A new name is checked
against the folders on disk when Create is clicked, not against that list: a
project created in another tab since must never be overwritten.

The list is read on a worker started from an effect, never in a
``prefer_threaded=False`` task that depends on the save: the auto-save sets
``last_saved`` in the thread that published the project, and a Prepare row,
an Integrate and a review Save publish from worker threads with no event
loop, where solara starts such a task with a bare ``asyncio.create_task``
(the ``_ActiveSetLoader`` pattern in the shell).

A new, opened or duplicated project starts the workflow at its AOI and closes
the dialog (``dismiss``, from the shell). Deleting the open project closes it
first, so no auto-save can write its manifest back while the folder goes.

The list can be searched by name, and the open project's line reads 'saved X
ago', brought up to date by ``_SavedAgo``'s ticker.
"""

import asyncio
import logging
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.project import io, paths
from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import is_writing
from gui.widget.creation_dialog import CreationDialog
from gui.widget.product_table import ProductTable

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_NAME_ERRORS = {"empty": "steps.project.name_empty", "chars": "steps.project.name_chars",
                "exists": "steps.project.name_exists"}


def create_project(name: str) -> Project:
    now = datetime.now()
    return Project(name=name.strip(), created_at=now, updated_at=now)


def duplicate_with_sidecar(p: Project, new_name: str, root: Optional[Path] = None) -> Project:
    q = io.duplicate_project(p, new_name.strip())
    if p.aoi and p.aoi.geometry_file:
        src = paths.project_dir(p.name, root) / p.aoi.geometry_file
        if src.exists():
            dst = paths.project_dir(q.name, root) / p.aoi.geometry_file
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return q


def delete_blocked(name: str) -> Optional[str]:
    return "steps.project.delete_busy" if is_writing(name) else None


def names_on_disk(root: Optional[Path] = None) -> list[str]:
    """Every folder under the data directory, read now.

    A folder counts even without a readable manifest (it is not listed): a new
    project saved into it would overwrite whatever is there."""
    base = Path(root) if root else paths.data_dir()
    try:
        return [d.name for d in base.iterdir() if d.is_dir()]
    except OSError:  # no data directory yet
        return []


@solara.component
def _NameForm(title, create_label, open_, on_create, hint=None):
    name, set_name = solara.use_state("")

    def validate():
        err = io.validate_project_name(name, names_on_disk())
        return t(_NAME_ERRORS[err]) if err else None

    CreationDialog(
        open_=open_, title=title, create_label=create_label, validate=validate,
        will_replace=lambda: None, launch=lambda: on_create(name.strip()),
        on_close=lambda: set_name(""),
        children=[
            rv.TextField(label=t("steps.project.name_label"), v_model=name, on_v_model=set_name,
                         dense=True, outlined=True, hint=hint or "", persistent_hint=bool(hint)),
        ],
    )


#: How often the open project's "saved X ago" line is brought up to date.
TICK_SECONDS = 30.0


def relative_time(when: datetime, now: datetime) -> str:
    """'just now', 'N minutes ago', 'N hours ago', 'N days ago' (spatial_risk's
    ``format_relative``). A time a little ahead of ``now`` reads 'just now'."""
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return t("steps.project.ago_now")
    if seconds < 3600:
        return t("steps.project.ago_minutes", count=seconds // 60)
    if seconds < 86400:
        return t("steps.project.ago_hours", count=seconds // 3600)
    return t("steps.project.ago_days", count=seconds // 86400)


def filter_infos(infos: list, query) -> list:
    """The projects whose name contains ``query``, ignoring case; every project
    for a blank query (spatial_risk's ``filter_project_infos``). Order kept."""
    needle = (query or "").strip().lower()
    return list(infos) if not needle else [i for i in infos if needle in i.name.lower()]


@solara.component
def _SavedAgo(app_state):
    """The open project's save state, kept true while the dialog stays open.

    A leaf with its own ticker: a thread started from an effect re-renders this
    line every ``TICK_SECONDS`` and stops with the effect's cleanup, so nothing
    but this line re-renders on a tick (spec §11).
    """
    _, set_tick = solara.use_state(0)

    def _ticker():
        stop = threading.Event()

        def _run():
            n = 0
            while not stop.wait(TICK_SECONDS):
                n += 1
                set_tick(n)

        spawn_in_context(_run)
        return stop.set

    solara.use_effect(_ticker, [])
    # KEPT INLINE ON PURPOSE (ruling #62): the persistent save state of the
    # open project (spec §4.1), not a one-off outcome a toast would fit.
    if app_state.save_error.value:
        solara.Text(t("steps.project.save_failed", error=app_state.save_error.value), classes=["error--text"])
    elif app_state.last_saved.value:
        solara.Text(t("steps.project.saved_ago",
                      time_ago=relative_time(app_state.last_saved.value, datetime.now())),
                    classes=["text--secondary"])


def _reopen(name: str) -> Optional[Project]:
    """The project as its manifest now reads, or None once that is gone."""
    try:
        return io.load_project(name)
    except io.ProjectLoadError:
        return None


@solara.component
def ProjectDialog(app_state, dismiss=lambda: None):
    """``dismiss`` closes the dialog (the shell's MapApp handle)."""
    notifications = use_notifications()
    p = app_state.project.value
    refresh, set_refresh = solara.use_state(0)
    new_open = solara.use_reactive(False)
    dup_open = solara.use_reactive(False)
    pending_delete, set_pending_delete = solara.use_state(None)
    typed, set_typed = solara.use_state("")
    query, set_query = solara.use_state("")

    infos, set_infos = solara.use_state([])
    # The number of the last read started: a read overtaken by a newer one
    # (a Run all saves a row every few seconds) must not publish an older list.
    reads = solara.use_ref(0)

    # Read again on every save (counts and time of the open project), every
    # install or close, and after a delete. On a worker, from an effect: see
    # the module docstring.
    def _start_read():
        reads.current += 1
        run = reads.current

        def _read():
            try:
                listed = io.list_projects()
            except Exception:  # noqa: BLE001 - the list on screen stays as it was
                logger.exception("could not list the projects")
                return
            if reads.current == run:
                set_infos(listed)

        spawn_in_context(_read)

    solara.use_effect(_start_read, [refresh, app_state.project_loaded_signal.value, app_state.last_saved.value])

    def _show_workflow():
        # From any step: the navigation alone closes the dialog only when it
        # moves the workflow, which it does not when AOI is already showing.
        app_state.current_page_view.value = "aoi_tile"
        dismiss()

    async def _load(name):
        try:
            loaded = await asyncio.to_thread(io.load_project, name)
        except io.ProjectLoadError as exc:
            notifications.error(t("steps.project.open_failed", name=name, error=str(exc)))
            return
        app_state.load_project_state(loaded)
        _show_workflow()
        notifications.success(t("steps.project.opened", name=name))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _open(name):
        load_task(name)

    def _ask_delete(name):
        set_typed("")
        set_pending_delete(name)

    def _create(name):
        app_state.new_project_state(create_project(name))
        _show_workflow()

    def _duplicate(name):
        # The copy is where another area gets monitored (spec D10): start at its AOI.
        app_state.new_project_state(duplicate_with_sidecar(p, name))
        _show_workflow()

    async def _delete():
        name = pending_delete
        blocked = delete_blocked(name)
        if blocked:
            notifications.warning(t(blocked))
            return
        open_ = app_state.project.peek()
        was_open = open_ is not None and open_.name == name
        if was_open:
            # Closed before the folder goes: an auto-save landing in between
            # would write the manifest back into a half-deleted folder.
            app_state.close_project_state()
        try:
            await asyncio.to_thread(io.delete_project, name)
        except OSError as exc:
            # delete_project raises (FileNotFoundError, PermissionError, ...) on
            # disk trouble (ruling #62): a toast, not a crash, and the dialog
            # stays open on the name so the user can retry or cancel. The
            # manifest goes last, so a failed delete usually leaves the project
            # readable: it is opened again as it now reads.
            if was_open:
                reopened = await asyncio.to_thread(_reopen, name)
                if reopened is not None:
                    app_state.load_project_state(reopened)
            notifications.error(t("steps.project.delete_failed", name=name, error=str(exc)))
            return
        set_pending_delete(None)
        set_typed("")
        set_refresh(refresh + 1)
        notifications.success(t("steps.project.deleted", name=name))

    delete_task = solara.lab.use_task(_delete, dependencies=None, prefer_threaded=False, raise_error=False)

    with solara.Column(style="gap:16px;padding:8px;"):
        solara.Markdown(f"### {t('steps.project.title')}")
        if p is None:
            solara.Text(t("steps.project.none_open"), classes=["text--secondary"])
        else:
            solara.Text(p.name, style="font-weight:600;font-size:1.1rem;")
            solara.Text(t(
                "steps.project.summary",
                aoi=p.aoi.name if p.aoi else t("steps.project.no_aoi"),
                sources=t("steps.project.count_sources", count=len(p.sources)),
                periods=t("steps.project.count_periods", count=len(p.periods)),
                sets=t("steps.project.count_sets", count=len(p.alert_sets)),
            ))
            _SavedAgo(app_state=app_state)
        with solara.Row(style="gap:8px;"):
            solara.Button(t("steps.project.new"), icon_name="mdi-plus", color="primary", small=True,
                          on_click=lambda: new_open.set(True))
            solara.Button(t("steps.project.duplicate"), icon_name="mdi-content-copy", small=True,
                          outlined=True, disabled=p is None, on_click=lambda: dup_open.set(True))
        rows = [
            {
                "key": i.name,
                "cells": [
                    {"value": i.name},
                    {"value": f"{i.n_sources} / {i.n_periods} / {i.n_sets}", "muted": True},
                    {"value": i.updated_at.strftime("%Y-%m-%d %H:%M") if i.updated_at else "", "muted": True},
                ],
                "actions": [
                    {"kind": "open", "on_click": (lambda n=i.name: _open(n))},
                    {"kind": "delete", "on_click": (lambda n=i.name: _ask_delete(n))},
                ],
            }
            for i in filter_infos(infos, query)
        ]
        if infos:
            rv.TextField(label=t("steps.project.search"), v_model=query, on_v_model=set_query,
                         prepend_inner_icon="mdi-magnify", dense=True, outlined=True, clearable=True,
                         hide_details=True)
        needle = (query or "").strip()
        ProductTable(
            title=t("steps.project.list_title"),
            columns=[{"label": t("steps.project.col_name")},
                     {"label": t("steps.project.col_counts"), "width": "140px"},
                     {"label": t("steps.project.col_updated"), "width": "130px"}],
            rows=rows,
            empty_text=t("steps.project.no_match", query=needle) if infos and needle else t("steps.project.list_empty"),
        )

    _NameForm(t("steps.project.new"), t("common.create"), new_open, _create)
    if p is not None:
        _NameForm(t("steps.project.duplicate_title", name=p.name), t("common.create"), dup_open,
                  _duplicate, hint=t("steps.project.duplicate_hint"))

    with rv.Dialog(v_model=pending_delete is not None,
                   on_v_model=lambda v: None if v else set_pending_delete(None),
                   max_width="420px", eager=True):
        with rv.Card():
            with rv.CardTitle():
                solara.Text(t("steps.project.delete_title", name=pending_delete or ""))
            with rv.CardText():
                solara.Text(t("steps.project.delete_message"))
                rv.TextField(v_model=typed, on_v_model=set_typed, dense=True, outlined=True)
            with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
                solara.Button(t("common.cancel"), text=True, small=True,
                              on_click=lambda: set_pending_delete(None))
                solara.Button(t("common.delete"), color="error", small=True,
                              disabled=typed != (pending_delete or "") or delete_task.pending,
                              loading=delete_task.pending,
                              on_click=lambda: delete_task())
