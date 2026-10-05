"""The Project rail dialog (spec D5, spatial_risk's ``ProjectPanel``): the open
project's status, New, Manage projects and Duplicate. Auto-save means there is
no Save button (D11): the status shows a "saved" chip, or "save failed".

Three dialogs hang off it, all rendered at its top level:

- the name form of New and Duplicate (``_NameForm``), on the form frame
  (``gui/widget/form_dialog.py``). The name is checked as it is typed against
  the folders on disk read when the form opened, and against the disk again
  when Create is pressed: a project created in another tab since must never be
  overwritten.
- Manage projects (``gui/widget/manage_projects.py``): search, open, delete.
- the delete confirmation, which shows the folder's size, counted off-thread.

The list is read again whenever the open project is saved, installed or
closed, after a delete and when Manage opens, so its counts and times follow
the session. It is read on a worker started from an effect, never in a
``prefer_threaded=False`` task that depends on the save: the auto-save sets
``last_saved`` in the thread that published the project, and a Prepare row,
an Integrate and a review Save publish from worker threads with no event
loop, where solara starts such a task with a bare ``asyncio.create_task``
(the ``_ActiveSetLoader`` pattern in the shell).

A new, opened or duplicated project starts the workflow at its AOI and closes
the dialog (``dismiss``, from the shell). Deleting the open project closes it
first, so no auto-save can write its manifest back while the folder goes; a
failed delete opens it again as it now reads.
"""

import asyncio
import logging
import os
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
from gui.scripts.error_text import error_text
from gui.scripts.solara_threads import spawn_in_context
from gui.store.project_writers import is_writing
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import form_card, use_form_dialog
from gui.widget.manage_projects import (
    ConfirmDeleteDialog,
    ManageProjectsDialog,
    relative_time,
)
from gui.widget.text_style import MUTED

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


def folder_size(name: str, root: Optional[Path] = None) -> int:
    """The bytes of the regular files in a project's folder, for the delete
    confirmation. A symlink is never followed (it may point at a file outside
    the project), and what cannot be read is skipped: the figure is shown, not
    relied on. A folder that is not there is 0."""
    total = 0
    stack = [paths.project_dir(name, root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


@solara.component
def _NameFields(title, open_, on_create, on_open_existing, infos, hint):
    """The name form's card. Mounted only while the form is open, so every
    opening starts with an empty name and reads the folders again."""
    name, set_name = solara.use_state("")
    error, set_error = solara.use_state(None)
    # Enter and a click can both press Create before the form closes.
    launched = solara.use_ref(False)
    # The live check's folders, read once when the form opens. Create reads
    # the disk again: another tab may make the folder in between (Review Focus 4).
    existing = solara.use_memo(names_on_disk, [])
    live = io.validate_project_name(name, existing)
    typed = name.strip()
    # A listed project of that name (folders without a manifest are not listed,
    # so nothing to open there): offered instead of a name that cannot be used.
    match = next((i.name for i in infos if typed and i.name.casefold() == typed.casefold()), None)

    def _type(value):
        set_name(value or "")
        set_error(None)

    def _create():
        if launched.current:
            return
        err = io.validate_project_name(name, names_on_disk())
        if err:
            set_error(t(_NAME_ERRORS[err]))
            return
        launched.current = True
        on_create(typed)
        open_.set(False)

    def _actions():
        solara.Button(t("common.create"), icon_name="mdi-plus", color="primary", small=True,
                      disabled=live is not None, classes=[ENTER_DEFAULT], on_click=_create)

    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        rv.TextField(label=t("steps.project.name_label"), v_model=name, on_v_model=_type, dense=True,
                     outlined=True, autofocus=True, hint=hint or t("steps.project.name_hint"),
                     persistent_hint=True,
                     # Live, but not before anything is typed: an empty form is not wrong yet.
                     error_messages=[t(_NAME_ERRORS[live])] if name and live else [])
        if match:
            with rv.Html(tag="div"):
                solara.Button(t("steps.project.open_instead", name=match), icon_name="mdi-folder-open-outline",
                              color="primary", text=True, small=True, on_click=lambda: on_open_existing(match))


@solara.component
def _NameForm(title, open_, on_create, on_open_existing, infos, hint=None):
    """New and Duplicate's name form (420px, on the form frame).

    ``on_create(name)`` installs the project; the form closes itself after.
    ``on_open_existing(name)`` opens the listed project the typed name matches.
    """
    with use_form_dialog(open_, max_width="420px"):
        if open_.value:
            _NameFields(title=title, open_=open_, on_create=on_create, on_open_existing=on_open_existing,
                        infos=infos, hint=hint)


#: How often the open project's "saved X ago" line is brought up to date.
TICK_SECONDS = 30.0


@solara.component
def _SaveChip(app_state):
    """spatial_risk's saved chip, after the auto-save (D11): "saved" once the
    project is on disk, "save failed" while the last save failed."""
    if app_state.save_error.value:
        rv.Chip(children=[t("steps.project.chip_save_failed")], color="error", text_color="white", x_small=True)
    elif app_state.last_saved.value:
        rv.Chip(children=[t("steps.project.chip_saved")], color="primary", text_color="white", x_small=True)


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
                    style=MUTED + "font-size:12px;")


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
    manage_open = solara.use_reactive(False)
    # A reactive, not state: Open reads it when pressed, right after the row
    # click that set it, before the dialog has re-rendered.
    selected = solara.use_reactive(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    size, set_size = solara.use_state(None)

    infos, set_infos = solara.use_state([])
    # The number of the last read started: a read overtaken by a newer one
    # (a Run all saves a row every few seconds) must not publish an older list.
    reads = solara.use_ref(0)

    # Read again on every save (counts and time of the open project), every
    # install or close, after a delete and when Manage opens. On a worker,
    # from an effect: see the module docstring.
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

    # The size of the folder to delete, counted on a worker: a project of
    # several GB takes seconds on SEPAL's disk. None reads "measuring" until
    # then; a count overtaken by another target is dropped.
    counts = solara.use_ref(0)

    def _start_count():
        counts.current += 1
        run = counts.current
        set_size(None)
        if pending_delete is None:
            return
        name = pending_delete

        def _count():
            total = folder_size(name)
            if counts.current == run:
                set_size(total)

        spawn_in_context(_count)

    solara.use_effect(_start_count, [pending_delete])

    def _show_workflow():
        # From any step: the navigation alone closes the dialog only when it
        # moves the workflow, which it does not when AOI is already showing.
        app_state.current_page_view.value = "aoi_tile"
        dismiss()

    async def _load(name):
        try:
            loaded = await asyncio.to_thread(io.load_project, name)
        except io.ProjectLoadError as exc:
            notifications.error(t("steps.project.open_failed", name=name, error=error_text(exc)))
            return
        app_state.load_project_state(loaded)
        manage_open.set(False)
        _show_workflow()
        notifications.success(t("steps.project.opened", name=name))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _open(name):
        if name:
            load_task(name)

    def _open_manage():
        selected.set(p.name if p is not None else None)
        set_refresh(lambda n: n + 1)  # another tab may have made or removed a project
        manage_open.set(True)

    def _open_instead(name):
        new_open.set(False)
        dup_open.set(False)
        _open(name)

    def _ask_delete(name):
        if delete_task.pending:
            return  # one delete at a time: the confirmation is its only view
        set_pending_delete(name)

    def _create(name):
        app_state.new_project_state(create_project(name))
        _show_workflow()

    def _duplicate(name):
        if p is None:  # closed (a delete) while the form was open
            return
        # The copy is where another area gets monitored (spec D10): start at its AOI.
        app_state.new_project_state(duplicate_with_sidecar(p, name))
        _show_workflow()

    async def _delete():
        name = pending_delete
        # The confirmation's gate is a render behind: a writer may have started
        # since. Checked again here, on the kernel thread.
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
            notifications.error(t("steps.project.delete_failed", name=name, error=error_text(exc)))
            return
        if selected.peek() == name:
            selected.set(None)
        set_pending_delete(None)  # closes the confirmation; Manage stays open
        set_refresh(lambda n: n + 1)
        notifications.success(t("steps.project.deleted", name=name))

    delete_task = solara.lab.use_task(_delete, dependencies=None, prefer_threaded=False, raise_error=False)

    def _confirm_delete():
        # Never call a running delete again: that would cancel its
        # continuation, not the removal of the folder.
        if not delete_task.pending:
            delete_task()

    with solara.Column(style="gap:8px;padding:8px;"):
        if p is None:
            rv.Icon(children=["mdi-folder-plus-outline"], style_="font-size:40px;opacity:0.5;")
            solara.Text(t("steps.project.empty_heading"), style="font-weight:600;")
            solara.Text(t("steps.project.none_open"), style=MUTED)
            solara.Button(t("steps.project.new"), icon_name="mdi-plus", color="primary", small=True, block=True,
                          on_click=lambda: new_open.set(True))
            solara.Button(t("steps.project.manage_count", count=len(infos)),
                          icon_name="mdi-folder-multiple-outline", color="primary", outlined=True, small=True,
                          block=True, disabled=not infos, on_click=_open_manage)
        else:
            with solara.Row(style="gap:8px;align-items:center;"):
                solara.Text(p.name, style="font-weight:600;font-size:1.1rem;")
                _SaveChip(app_state=app_state)
            solara.Text(t(
                "steps.project.summary",
                aoi=p.aoi.name if p.aoi else t("steps.project.no_aoi"),
                sources=t("steps.project.count_sources", count=len(p.sources)),
                periods=t("steps.project.count_periods", count=len(p.periods)),
                sets=t("steps.project.count_sets", count=len(p.alert_sets)),
            ), style=MUTED + "font-size:12px;")
            _SavedAgo(app_state=app_state)
            with solara.Row(style="gap:8px;flex-wrap:wrap;"):
                solara.Button(t("steps.project.new"), icon_name="mdi-plus", color="primary", outlined=True,
                              small=True, on_click=lambda: new_open.set(True))
                solara.Button(t("steps.project.manage"), icon_name="mdi-folder-multiple-outline", color="primary",
                              outlined=True, small=True, on_click=_open_manage)
                solara.Button(t("steps.project.duplicate"), icon_name="mdi-content-copy", color="primary",
                              outlined=True, small=True, on_click=lambda: dup_open.set(True))

    # Every dialog below is always mounted, the Duplicate form too: one that
    # came and went with the open project would shift the ones after it, and
    # reacton would remount them (the delete confirmation would lose its
    # typed name when the open project is closed for its delete).
    _NameForm(title=t("steps.project.new"), open_=new_open, on_create=_create, on_open_existing=_open_instead,
              infos=infos)
    _NameForm(title=t("steps.project.duplicate_title", name=p.name) if p is not None else "", open_=dup_open,
              on_create=_duplicate, on_open_existing=_open_instead, infos=infos,
              hint=t("steps.project.duplicate_hint"))

    ManageProjectsDialog(
        open=manage_open.value, infos=infos, selected=selected.value, on_select=selected.set,
        on_open=lambda: _open(selected.peek()), on_delete=_ask_delete, on_cancel=lambda: manage_open.set(False),
        current=p.name if p is not None else None, busy=load_task.pending or delete_task.pending,
    )
    ConfirmDeleteDialog(
        open=pending_delete is not None, name=pending_delete or "", size_bytes=size,
        on_cancel=lambda: set_pending_delete(None), on_confirm=_confirm_delete,
        is_open_project=p is not None and p.name == pending_delete,
        # Read only while a delete is asked for: the writers change on every
        # Prepare row, and the dialog need not re-render for them otherwise.
        writer_active=pending_delete is not None and is_writing(pending_delete),
        busy=delete_task.pending,
    )
