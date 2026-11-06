"""The Project rail dialog (spec D5, spatial_risk's ``ProjectPanel``): the open
project's card (Rename and Duplicate by its name), or, with none open, a card
to Continue the project changed last; then New, Manage projects and Import.
Auto-save means there is no Save button (D11): the card shows a "saved" chip,
or "save failed".

These dialogs hang off it, all rendered at its top level:

- the name form of New, Duplicate and Rename (``_NameForm``), on the form frame
  (``gui/widget/form_dialog.py``). The name is checked as it is typed against
  the folders on disk read when the form opened, and against the disk again
  when Create is pressed: a project created in another tab since must never be
  overwritten.
- Manage projects (``gui/widget/manage_projects.py``): search, open, rename,
  delete, and each folder's size, counted off-thread when it opens.
- Import: the package picker (on the form frame) and the re-import question.
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
the dialog (``dismiss``, from the shell). A review copy (spec 2026-10-10
§6.2), imported or opened, lands on Analysis instead, filtered to its
evaluator: ``_land`` is the one place that decides. **Import** reads a
review package's header first: a reviewed copy is refused (it is merged
from the creator's project), and a package already imported as a copy asks
to open that copy or import a new one (never overwrite). Opening the project already open
only closes the dialogs: it is not read again over its in-memory state. Deleting the open project closes it
first, so no auto-save can write its manifest back while the folder goes; a
failed delete opens it again as it now reads.
"""

import asyncio
import logging
import os
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.inputs import FileInputComponent
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.project import io, paths, share
from deforestation_alerts.project.models import Project
from deforestation_alerts.project.review_copy import read_only
from deforestation_alerts.project.split import EVALUATOR_COLUMN
from gui.i18n import t
from gui.scripts.alert_filter import AlertFilter
from gui.scripts.error_text import error_text
from gui.scripts.solara_threads import spawn_in_context, to_thread_in_context
from gui.store.project_writers import is_writing
from gui.tile.analysis.state import apply_start_filter
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import form_card, use_form_dialog
from gui.widget.manage_projects import (
    META,
    ChipRow,
    ConfirmDeleteDialog,
    ManageProjectsDialog,
    aoi_item,
    icon_button,
    relative_time,
)
from gui.widget.text_style import MUTED

logger = logging.getLogger("SEPALUI.deforestation_alerts")

_NAME_ERRORS = {"empty": "steps.project.name_empty", "chars": "steps.project.name_chars",
                "exists": "steps.project.name_exists"}

#: a review package is one zip (spec §6.1)
IMPORT_EXTENSIONS = [".zip"]


def import_failure_text(exc: BaseException, file: str) -> str:
    """A failed import of ``file``: a refusal (``ShareError``) as its own
    text, anything else (the disk's error) naming the file (R4)."""
    if isinstance(exc, share.ShareError):
        return error_text(exc)
    return t("steps.project.import_failed", file=file, error=error_text(exc))


def create_project(name: str) -> Project:
    now = datetime.now()
    return Project(name=name.strip(), created_at=now, updated_at=now)


def duplicate_with_sidecar(p: Project, new_name: str, root: Optional[Path] = None) -> Project:
    """The copy, with its AOI sidecar copied into its new folder.

    Raises ``OSError`` when the sidecar cannot be copied. A folder this call
    made is removed again first, so the name stays free for a retry (the name
    form checks the disk for every folder)."""
    q = io.duplicate_project(p, new_name.strip())
    if p.aoi and p.aoi.geometry_file:
        src = paths.project_dir(p.name, root) / p.aoi.geometry_file
        if src.exists():
            folder = paths.project_dir(q.name, root)
            fresh = not folder.exists()
            dst = folder / p.aoi.geometry_file
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except OSError:
                if fresh:
                    shutil.rmtree(folder, ignore_errors=True)
                raise
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


def _others(names: list[str], own: str) -> list[str]:
    """``names`` without ``own``, the folder a rename moves (empty: none)."""
    return [n for n in names if n != own]


@solara.component
def _NameFields(title, open_, on_create, on_open_existing, infos, hint, current, action_label, action_icon,
                initial):
    """The name form's card. Mounted only while the form is open, so every
    opening starts from ``initial`` and reads the folders again.
    ``current``: the open project's name, taken but never offered to open.
    ``initial``: Rename's current name, filled in; its own folder does not
    count as taken, and keeping it is not a rename."""
    name, set_name = solara.use_state(initial)
    error, set_error = solara.use_state(None)
    # Enter and a click can both press Create before the form closes.
    launched = solara.use_ref(False)
    # The live check's folders, read once when the form opens. Create reads
    # the disk again: another tab may make the folder in between (Review Focus 4).
    existing = solara.use_memo(lambda: _others(names_on_disk(), initial), [])
    live = io.validate_project_name(name, existing)
    typed = name.strip()
    unchanged = bool(initial) and typed == initial
    # A listed project of that name (folders without a manifest are not listed,
    # so nothing to open there): offered instead of a name that cannot be used.
    # Not the open project: it is open already, and opening it again would
    # read it back from disk over what is only in memory.
    match = next((i.name for i in infos if typed and i.name.casefold() == typed.casefold()
                  and i.name != current), None)

    def _type(value):
        set_name(value or "")
        set_error(None)

    def _create():
        if launched.current:
            return
        if unchanged:
            return
        err = io.validate_project_name(name, _others(names_on_disk(), initial))
        if err:
            set_error(t(_NAME_ERRORS[err]))
            return
        launched.current = True
        if not on_create(typed):
            launched.current = False  # failed (the caller toasted): Create works again
            return
        open_.set(False)

    def _actions():
        solara.Button(action_label or t("common.create"), icon_name=action_icon, color="primary", small=True,
                      disabled=live is not None or unchanged, classes=[ENTER_DEFAULT], on_click=_create)

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
def _NameForm(title, open_, on_create, on_open_existing, infos, hint=None, current=None, action_label=None,
              action_icon="mdi-plus", initial=""):
    """New, Duplicate and Rename's name form (420px, on the form frame).

    ``on_create(name)`` installs the project and returns whether it did; the
    form closes itself after, or stays open on the name when it did not.
    ``on_open_existing(name)`` opens the listed project the typed name matches.
    """
    with use_form_dialog(open_, max_width="420px"):
        if open_.value:
            _NameFields(title=title, open_=open_, on_create=on_create, on_open_existing=on_open_existing,
                        infos=infos, hint=hint, current=current, action_label=action_label, action_icon=action_icon,
                        initial=initial)


@solara.component
def _ImportForm(open_, on_pick, busy):
    """Import's package picker (420px, on the form frame). A pick starts the
    import at once; the picker is remounted empty after each one, so a
    refused file does not stay on screen."""
    picks, set_picks = solara.use_state(0)

    def _pick(path):
        set_picks(lambda n: n + 1)
        if path:
            on_pick(path)

    with use_form_dialog(open_, max_width="420px"):
        if open_.value:
            with form_card(t("steps.project.import_title"), on_cancel=lambda: open_.set(False),
                           actions=lambda: None, cancel_disabled=busy):
                with solara.Column(style="gap:2px;").key(f"import:{picks}"):
                    FileInputComponent(extensions=IMPORT_EXTENSIONS, value="", on_value=_pick,
                                       label=t("steps.project.import_pick"))
                solara.Text(t("steps.project.import_hint"), style=MUTED + "font-size:0.8rem;")
                if busy:
                    rv.ProgressLinear(indeterminate=True, color="primary")


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


#: The open project's card and the Continue card: a translucent primary layer
#: and a primary left edge, from the theme's ``primary`` class (the theme does
#: not publish ``--v-primary-base``; see ``gui/tile/analysis/file_tile.py``).
_CARD = "position:relative;overflow:hidden;border-radius:6px;padding:12px 10px 12px 16px;"
_TINT = "position:absolute;top:0;right:0;bottom:0;left:0;opacity:0.12;pointer-events:none;"
_EDGE = "position:absolute;top:0;bottom:0;left:0;width:3px;pointer-events:none;"
_CARD_GRID = ("position:relative;display:grid;grid-template-columns:minmax(0, 1fr) auto;gap:6px 12px;"
              "align-items:center;")
_CARD_META = META + "font-size:12px;" + MUTED
_NAME = "font-weight:700;font-size:1.1rem;overflow-wrap:anywhere;"


@contextmanager
def _tinted_card():
    with rv.Html(tag="div", style_=_CARD):
        rv.Html(tag="span", class_="primary", style_=_TINT)
        rv.Html(tag="span", class_="primary", style_=_EDGE)
        yield


def _reopen(name: str) -> Optional[Project]:
    """The project as its manifest now reads, or None once that is gone."""
    try:
        return io.load_project(name)
    except io.ProjectLoadError:
        return None


@solara.component
def ProjectDialog(app_state, dismiss=lambda: None, analysis=None):
    """``dismiss`` closes the dialog (the shell's MapApp handle);
    ``analysis`` is the shell's ``AnalysisState``, where a review copy's
    evaluator filter goes."""
    notifications = use_notifications()
    p = app_state.project.value
    refresh, set_refresh = solara.use_state(0)
    new_open = solara.use_reactive(False)
    dup_open = solara.use_reactive(False)
    rename_open = solara.use_reactive(False)
    manage_open = solara.use_reactive(False)
    import_open = solara.use_reactive(False)
    # The package a re-import asks about: {"path", "name", "evaluator"}.
    existing, set_existing = solara.use_state(None)
    # Set by the pick or answer that starts an import, cleared when its task
    # ends. Solara runs on_click / on_value whatever ``disabled`` the last
    # render sent, and a task called again cancels its asyncio run but not
    # the worker under it: a second pick is dropped here (evaluators_step).
    importing = solara.use_ref(False)
    # A reactive, not state: Open reads it when pressed, right after the row
    # click that set it, before the dialog has re-rendered.
    selected = solara.use_reactive(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    size, set_size = solara.use_state(None)
    # The project the Rename form renames: the open one, or a row of Manage.
    rename_target, set_rename_target = solara.use_state(None)
    # {name: bytes} of the listed folders, measured each time Manage opens.
    sizes, set_sizes = solara.use_state({})

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

    # Every listed folder's size for Manage's rows, counted on a worker each
    # time it opens (or the list changes while it is open): a stat per file,
    # so cheap for a project's few dozen files. A size lands as soon as its
    # folder is counted; an older count is dropped once a newer one starts.
    measures = solara.use_ref(0)
    listed_names = tuple(i.name for i in infos)

    def _start_measure():
        measures.current += 1
        run = measures.current
        if not manage_open.value:
            return

        def _measure():
            for name in listed_names:
                if measures.current != run:
                    return
                total = folder_size(name)
                if measures.current == run:
                    set_sizes(lambda d, name=name, total=total: {**d, name: total})

        spawn_in_context(_measure)

    solara.use_effect(_start_measure, [manage_open.value, listed_names])

    def _show_workflow():
        # From any step: the navigation alone closes the dialog only when it
        # moves the workflow, which it does not when AOI is already showing.
        app_state.current_page_view.value = "aoi_tile"
        dismiss()

    def _land(loaded):
        """Where an opened or imported project starts, after ``_install``
        (which resets the screen to the workflow, ``state_manager.py:138``):
        a review copy on Analysis, filtered to its evaluator before its
        alerts are published (spec §6.2); any other project at its AOI."""
        copy = loaded.review_copy
        if copy is None:
            _show_workflow()
            return
        if analysis is not None:
            apply_start_filter(analysis, AlertFilter(EVALUATOR_COLUMN, frozenset({copy.evaluator})))
        app_state.current_page_view.value = "analysis_tile"
        dismiss()

    async def _load(name):
        try:
            loaded = await asyncio.to_thread(io.load_project, name)
        except io.ProjectLoadError as exc:
            notifications.error(t("steps.project.open_failed", name=name, error=error_text(exc)))
            return
        app_state.load_project_state(loaded)
        manage_open.set(False)
        _land(loaded)
        notifications.success(t("steps.project.opened", name=name))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _held(fn):
        async def run(*args):
            try:
                await fn(*args)
            finally:
                importing.current = False
        return run

    def _launch(task, *args):
        """Start ``task`` unless an import is still running."""
        if importing.current:
            return
        importing.current = True
        task(*args)

    async def _import(path):
        try:
            made = await to_thread_in_context(share.import_package, path)
        except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
            notifications.error(import_failure_text(exc, Path(path).name))
            return
        import_open.set(False)
        app_state.load_project_state(made)
        set_refresh(lambda n: n + 1)
        _land(made)
        notifications.success(t("steps.project.imported", name=made.name))

    import_task = solara.lab.use_task(_held(_import), dependencies=None, prefer_threaded=False, raise_error=False)

    async def _peek(path):
        """The package's header, read before anything is written: a
        reviewed copy is refused, a package imported before asks first."""
        try:
            header = await to_thread_in_context(share.peek_header, path)
            if header.kind == share.KIND_RETURN:
                notifications.error(t("share.err.is_return"))
                return
            found = await to_thread_in_context(share.find_existing_copy, header)
        except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
            notifications.error(import_failure_text(exc, Path(path).name))
            return
        if found:
            import_open.set(False)
            set_existing({"path": path, "name": found, "evaluator": header.evaluator})
            return
        await _import(path)

    peek_task = solara.lab.use_task(_held(_peek), dependencies=None, prefer_threaded=False, raise_error=False)

    def _open_existing():
        if existing is None:
            return
        name = existing["name"]
        set_existing(None)
        open_now = app_state.project.peek()
        if open_now is not None and open_now.name == name:
            # The copy is open already: not read again (``_open``), but it
            # still lands as a review copy does (spec §6.2), on Analysis with
            # its evaluator's filter. Manage → Open of the open project keeps
            # whatever filter the user chose instead.
            _land(open_now)
            return
        _open(name)

    def _import_new():
        if existing is None:
            return
        path = existing["path"]
        set_existing(None)
        _launch(import_task, path)

    def _open(name):
        if not name:
            return
        open_now = app_state.project.peek()
        if open_now is not None and open_now.name == name:
            # Manage opens on the open project, with Open as Enter's default.
            # It is not read again: after a failed auto-save the disk is behind
            # what is in memory, and a reload would silently drop that.
            manage_open.set(False)
            dismiss()
            return
        load_task(name)

    def _open_manage():
        selected.set(p.name if p is not None else None)
        set_refresh(lambda n: n + 1)  # another tab may have made or removed a project
        manage_open.set(True)

    def _ask_rename(name):
        set_rename_target(name)
        rename_open.set(True)

    def _open_instead(name):
        new_open.set(False)
        dup_open.set(False)
        rename_open.set(False)
        _open(name)

    def _ask_delete(name):
        if delete_task.pending:
            return  # one delete at a time: the confirmation is its only view
        set_pending_delete(name)

    def _create(name):
        app_state.new_project_state(create_project(name))
        _show_workflow()
        return True

    def _duplicate(name):
        if p is None:  # closed (a delete) while the form was open
            return True  # nothing to copy: the form just closes
        if read_only(p):  # spec §6.3: a review copy is not duplicated (its button is hidden)
            return True
        try:
            copy = duplicate_with_sidecar(p, name)
        except OSError as exc:  # the sidecar's folder or copy (disk full, permissions)
            notifications.error(t("steps.project.duplicate_failed", name=p.name, error=error_text(exc)))
            return False
        # The copy is where another area gets monitored (spec D10): start at its AOI.
        app_state.new_project_state(copy)
        _show_workflow()
        return True

    def _rename(name):
        old = rename_target
        if not old:
            return True
        # Under the publish lock: no commit can save into the old folder between the move and the install.
        with app_state._lock:
            if is_writing(old):  # a job would keep writing into the old folder
                notifications.warning(t("steps.project.rename_busy"))
                return False
            live = app_state.project.peek()
            try:
                io.rename_project(old, name)
            except OSError as exc:
                notifications.error(t("steps.project.rename_failed", name=old, error=error_text(exc)))
                return False
            if live is not None and live.name == old:
                # Installed, not just published, so every path held for the old
                # folder is read again; saved at once, unsaved changes included.
                # Another project is just a folder move: loading takes the name
                # from the folder (io.load_project).
                app_state.new_project_state(live.model_copy(update={"name": name}))
        if selected.peek() == old:
            selected.set(name)
        set_refresh(lambda n: n + 1)
        notifications.success(t("steps.project.renamed", name=name))
        return True

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

    # The project changed last, offered to Continue while none is open.
    recent = max(infos, key=lambda i: i.updated_at or datetime.min, default=None)

    with solara.Column(style="gap:8px;padding:8px;"):
        if p is None and recent is None:
            # Centred, not stretched: a full-width icon's ::after overlay
            # (Vuetify scales it 1.3x) overflows the dialog into a scrollbar.
            rv.Icon(children=["mdi-folder-plus-outline"], style_="font-size:40px;opacity:0.5;align-self:center;")
            solara.Text(t("steps.project.empty_heading"), style="font-weight:600;")
            solara.Text(t("steps.project.none_open"), style=MUTED)
        elif p is None:
            with _tinted_card():
                with rv.Html(tag="div", style_=_CARD_GRID):
                    with rv.Html(tag="div", style_="display:grid;gap:4px;min-width:0;"):
                        rv.Html(tag="div", class_="primary--text", style_="font-size:12.5px;font-weight:500;",
                                children=[t("steps.project.continue_lead")])
                        solara.Text(recent.name, style=_NAME)
                        ChipRow(info=recent)
                        with rv.Html(tag="div", style_=_CARD_META):
                            aoi_item(recent.aoi_name)
                            if recent.updated_at is not None:
                                solara.Text(t("steps.project.modified",
                                              time_ago=relative_time(recent.updated_at, datetime.now())))
                    solara.Button(t("steps.project.continue"), icon_name="mdi-folder-open-outline", color="primary",
                                  small=True, disabled=load_task.pending, on_click=lambda: _open(recent.name))
        else:
            with _tinted_card():
                with rv.Html(tag="div", style_=_CARD_GRID):
                    with rv.Html(tag="div", style_="display:flex;gap:8px;align-items:center;flex-wrap:wrap;"
                                                   "min-width:0;"):
                        solara.Text(p.name, style=_NAME)
                        _SaveChip(app_state=app_state)
                    with rv.Html(tag="div", style_="display:flex;"):
                        icon_button("mdi-pencil-outline", t("steps.project.rename"), lambda: _ask_rename(p.name))
                        if not read_only(p):  # spec §6.3: a review copy is not duplicated
                            icon_button("mdi-content-copy", t("steps.project.duplicate"),
                                         lambda: dup_open.set(True))
                    with rv.Html(tag="div", style_="grid-column:1 / -1;"):
                        ChipRow(info=io.ProjectInfo(name=p.name, n_sources=len(p.sources),
                                                    n_periods=len(p.periods), n_sets=len(p.alert_sets),
                                                    updated_at=p.updated_at))
                    with rv.Html(tag="div", style_="grid-column:1 / -1;justify-content:space-between;"
                                                   + _CARD_META):
                        aoi_item(p.aoi.name if p.aoi else None)
                        _SavedAgo(app_state=app_state)
        # The same three buttons with or without an open project; New is the
        # filled one only when there is nothing to continue.
        solara.Button(t("steps.project.new"), icon_name="mdi-plus", color="primary", small=True, block=True,
                      outlined=p is not None or recent is not None, on_click=lambda: new_open.set(True))
        solara.Button(t("steps.project.manage_count", count=len(infos)),
                      icon_name="mdi-folder-multiple-outline", color="primary", outlined=True, small=True,
                      block=True, disabled=not infos, on_click=_open_manage)
        solara.Button(t("steps.project.import"), icon_name="mdi-import", color="primary", outlined=True,
                      small=True, block=True, on_click=lambda: import_open.set(True))

    # Every dialog below is always mounted, the Duplicate form too: one that
    # came and went with the open project would shift the ones after it, and
    # reacton would remount them (the delete confirmation would lose its
    # typed name when the open project is closed for its delete).
    current = p.name if p is not None else None
    _NameForm(title=t("steps.project.new"), open_=new_open, on_create=_create, on_open_existing=_open_instead,
              infos=infos, current=current)
    _NameForm(title=t("steps.project.duplicate_title", name=p.name) if p is not None else "", open_=dup_open,
              on_create=_duplicate, on_open_existing=_open_instead, infos=infos,
              hint=t("steps.project.duplicate_hint"), current=current)
    _NameForm(title=t("steps.project.rename_title", name=rename_target or ""), open_=rename_open,
              # no "Open X instead": a rename is not a search for a project to open
              on_create=_rename, on_open_existing=_open_instead, infos=[], current=current,
              action_label=t("steps.project.rename"), action_icon="mdi-pencil-outline",
              initial=rename_target or "")

    ManageProjectsDialog(
        open=manage_open.value, infos=infos, selected=selected.value, on_select=selected.set,
        on_open=lambda: _open(selected.peek()), on_delete=_ask_delete, on_cancel=lambda: manage_open.set(False),
        current=current, busy=load_task.pending or delete_task.pending or import_task.pending or peek_task.pending,
        covered=pending_delete is not None or rename_open.value, on_rename=_ask_rename, sizes=sizes,
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
    _ImportForm(open_=import_open, on_pick=lambda path: _launch(peek_task, path),
                busy=peek_task.pending or import_task.pending)
    ConfirmDialog(
        open=existing is not None,
        on_cancel=lambda: set_existing(None),
        on_confirm=_open_existing,
        title=t("steps.project.import_existing_title"),
        message=t("steps.project.import_existing_message", name=existing["name"], evaluator=existing["evaluator"])
        if existing else "",
        confirm_label=t("steps.project.import_open_existing"),
        confirm_color="primary",
        secondary_label=t("steps.project.import_as_new"),
        on_secondary=_import_new,
    )
