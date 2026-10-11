"""The Evaluators step (spec 2026-10-10 §4): split the active set's alerts
between evaluators, hand each one a review package, and merge the reviewed
copies they send back. In a review copy (§6.4) the step shows the *Return
reviews* panel instead: the evaluator's progress and the reviewed copy to
send back.

The store is the source of truth for the assignments (its ``evaluator``
column); the table counts them from the active set's loaded alerts. Every
store write (split, remove, merge) goes through ``split_service`` /
``merge_service``, which hold the per-store lock (§4.4). Builds, reads and
writes run off the event loop with their button loading (§9). A package or
a reviewed copy lands in the set folder and is offered as a download link,
as the Overview export does (``gui/tile/overview_tile.py``).

``share.build_package`` leaves the project alone: after a build this step
records the split as packaged, with the questionnaire's digest, so a later
re-split warns and a merge can tell the questionnaire changed (§4.1, §7.1).

The services return their split or merge record even when the project was
switched before the record landed (the store write stands). The step checks
the live project afterwards and says so instead of reporting it recorded.
They run through ``to_thread_in_context``: a merge reloads the alert open in
Analysis, which writes widgets, so the worker carries the session's context.

The shell renders this step detached, so toasts go through
``current_notifier``, resolved where they are raised.
"""

import asyncio
import functools
import tomllib
from importlib import metadata
from pathlib import Path
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.scripts.utils import create_download_link
from pysepal.solara.components.inputs import FileInputComponent

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project import paths, share
from deforestation_alerts.project import split as split_mod
from deforestation_alerts.project.review_copy import read_only
from deforestation_alerts.project.review_status import reviewed_mask
from deforestation_alerts.scripts import local_files
from gui.i18n import format_number, t, tn
from gui.scripts import aoi_io, merge_service, remote_facts, set_store, split_service
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
from gui.scripts.setup_guard import update_setup
from gui.scripts.solara_threads import to_thread_in_context
from gui.store.project_writers import writing
from gui.store.workflow_steps import active_set
from gui.tile.questionnaire_step import _StartAnalysis
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import field_group, form_card, use_form_dialog
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.text_style import MUTED

_DIST = "deforestation-alerts-module"
_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
#: a reviewed copy is picked as one zip, and merged one at a time (§7.4)
ZIP = [".zip"]
NO_VALUE = "—"


@functools.lru_cache(maxsize=1)
def app_version() -> str:
    """The app's version for a package header: the installed distribution's,
    else ``pyproject.toml``'s (the app runs from a checkout), else "unknown"."""
    try:
        return metadata.version(_DIST)
    except metadata.PackageNotFoundError:
        pass
    try:
        with open(_PYPROJECT, "rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def typed_evaluators(mode: str, count_text, names_text) -> list[str]:
    """The evaluators the form names: "Evaluator 1".."Evaluator N" for a
    count, or the typed names. Raises ``SplitError``."""
    if mode == "names":
        return split_mod.normalize_names(str(names_text or "").splitlines())
    try:
        n = int(str(count_text or "").strip())
    except ValueError:
        raise split_mod.SplitError("evaluators.err.bad_count", max=split_mod.MAX_EVALUATORS) from None
    return split_mod.count_names(n, t("evaluators.default_prefix"))


def aoi_ready(p, pdir, result) -> bool:
    """Whether a package can carry the AOI geometry now (§5.3): the sidecar
    is on disk, or the AOI step handed over this AOI's selection. An Earth
    Engine AOI waits for that step's replay, as the scene loader does."""
    if p is None or p.aoi is None:
        return False
    if p.aoi.geometry_file and (Path(pdir) / p.aoi.geometry_file).is_file():
        return True
    return remote_facts.aoi_origin(p, result) == "result"


async def package_aoi(p, pdir, result, gee_interface) -> Optional[dict]:
    """The AOI as GeoJSON for a package: the sidecar (read off the loop), else
    the AOI step's selection fetched as the sidecar loader fetches it; None
    while neither is there."""
    aoi = await asyncio.to_thread(share.aoi_geojson_for_package, p, pdir)
    if aoi is not None:
        return aoi
    if remote_facts.aoi_origin(p, result) != "result":
        return None
    return await gee_interface.get_info_async(aoi_io.sidecar_request(aoi_io.ee_aoi(result, None)))


def build_package_file(p, pdir, set_id: str, evaluator: str, aoi: dict) -> Path:
    """``evaluator``'s package, in the set's folder. Blocking."""
    s = p.alert_sets[set_id]
    with writing(p.name):
        return share.build_package(p, Path(pdir), set_id, evaluator, aoi, set_store.set_folder(p, s),
                                   app_version=app_version())


def build_return_file(p, pdir) -> Path:
    """A review copy's reviewed copy, in its set's folder. Blocking."""
    s = p.alert_sets[p.review_copy.set_id]
    with writing(p.name):
        return share.build_return(p, Path(pdir), set_store.set_folder(p, s), app_version=app_version())


def link_entry(path) -> dict:
    """A built file's download link (``create_download_link`` of its path
    under ``$HOME``). Raises ``ValueError`` outside ``$HOME``."""
    return {"file": Path(path).name, "url": create_download_link(local_files.home_relative(path))}


def mark_packaged(app_state, name: str, set_id: str, split_id: str, digest: str) -> bool:
    """Record that a package of split ``split_id`` was built, with the
    questionnaire's digest it carries. False, recording nothing, when
    another project was opened, the split was replaced or removed meanwhile,
    or the project is a review copy (§6.3)."""
    applied = []

    def _apply(live):
        s = live.alert_sets.get(set_id)
        if s is None or s.split is None or s.split.split_id != split_id:
            return
        made = s.split.model_copy(update={"packaged": True, "questions_digest": digest})
        live.alert_sets = {**live.alert_sets, set_id: s.model_copy(update={"split": made})}
        applied.append(True)

    return bool(update_setup(app_state, name, _apply)) and bool(applied)


def split_recorded(app_state, name: str, set_id: str, made) -> bool:
    """Whether the open project ``name`` holds the split ``made`` on set
    ``set_id`` (``apply_split`` returns it even when the project switched
    before the record landed)."""
    live = app_state.project.value
    s = live.alert_sets.get(set_id) if live is not None and live.name == name else None
    return s is not None and s.split is not None and s.split.split_id == made.split_id


def return_recorded(app_state, name: str, set_id: str, evaluator: str, record) -> bool:
    """Whether the open project ``name`` holds the merge ``record`` of
    ``evaluator`` on set ``set_id`` (``merge_return`` returns it even when
    the project switched before it landed; the merged rows are on disk, so
    merging the same copy again records it)."""
    live = app_state.project.value
    s = live.alert_sets.get(set_id) if live is not None and live.name == name else None
    return s is not None and s.split is not None and s.split.returns.get(evaluator) == record


def evaluator_rows(split, gdf) -> tuple[list[dict], int]:
    """One ``{"name", "assigned", "reviewed", "merged_at"}`` per evaluator of
    ``split`` and the number of unassigned alerts, counted from the store's
    alerts (0 while they are not loaded)."""
    if split is None:
        return [], 0
    assigned = split_mod.assignments(gdf) if gdf is not None else None
    reviewed = reviewed_mask(gdf) if gdf is not None else None
    rows = []
    for name in split.evaluators:
        ret = split.returns.get(name)
        mine = (assigned == name) if assigned is not None else None
        rows.append({"name": name,
                     "assigned": int(mine.sum()) if mine is not None else 0,
                     "reviewed": int((mine & reviewed).sum()) if mine is not None else 0,
                     "merged_at": ret.merged_at if ret is not None else None})
    return rows, int((assigned == "").sum()) if assigned is not None else 0


def return_progress(p, gdf) -> tuple[int, int]:
    """``(reviewed, assigned)`` of a review copy's own alerts."""
    if p is None or p.review_copy is None or gdf is None:
        return 0, 0
    mine = split_mod.assignments(gdf) == p.review_copy.evaluator
    return int((mine & reviewed_mask(gdf)).sum()), int(mine.sum())


def failure_text(exc: BaseException, file: str) -> str:
    """A failed read or merge of the reviewed copy ``file``: a keyed refusal
    (``ShareError`` and the like) as its own text, anything else (an
    ``OSError`` on the parquet, a malformed table) naming the file."""
    if isinstance(exc, KeyedError):
        return error_text(exc)
    return t("evaluators.merge_failed", file=file, error=error_text(exc))


def table_columns() -> list:
    return [{"label": t("evaluators.col_name")},
            {"label": t("evaluators.col_assigned"), "width": "56px"},
            {"label": t("evaluators.col_reviewed"), "width": "56px"},
            {"label": t("evaluators.col_merged"), "width": "76px"}]


def _table_rows(stats, unassigned, *, on_package, building, can_package) -> list:
    rows = [{"key": s["name"],
             "cells": [{"value": s["name"]},
                       {"value": format_number(s["assigned"])},
                       {"value": format_number(s["reviewed"])},
                       {"value": s["merged_at"].strftime("%Y-%m-%d") if s["merged_at"] else NO_VALUE,
                        "muted": s["merged_at"] is None}],
             "actions": [{"kind": "download", "on_click": (lambda n=s["name"]: on_package(n)),
                          "disabled": not can_package, "loading": building == s["name"]}]}
            for s in stats]
    if unassigned:
        rows.append({"key": "", "actions": [],
                     "cells": [{"value": t("evaluators.unassigned"), "muted": True},
                               {"value": format_number(unassigned)},
                               {"value": NO_VALUE, "muted": True}, {"value": NO_VALUE, "muted": True}]})
    return rows


#: a block link whose long zip name wraps inside its panel
LINK_STYLE = ("display:block; margin-top:8px; font-size:0.85rem; white-space:normal; overflow-wrap:anywhere; "
              "word-break:break-all; text-decoration:none;")


def _download_link(entry, label: str, class_: str):
    """A plain ``<a>``, not a v-btn: a v-btn's ``.v-btn__content`` is
    ``flex: 1 0 auto`` and nowrap, so the zip name ran past the panel."""
    return rv.Html(tag="a", children=[rv.Icon(small=True, color="primary", class_="mr-1",
                                              children=["mdi-download"]), label],
                   attributes={"href": entry["url"], "target": "_blank", "download": entry["file"]},
                   class_=class_, style_=LINK_STYLE)


@solara.component
def _PreviewFields(app_state, preview, busy, on_cancel, on_merge):
    """The merge preview's card (§7.3), mounted only while it is open: each
    opening starts on *Keep mine*. Its text is ``merge_service.preview_lines``:
    the counts, then the notes (questionnaire changed, columns ignored, the
    alert open in Analysis that this choice reloads)."""
    choice, set_choice = solara.use_state("mine")
    conflicts = len(preview.conflicts)
    summary, *notes = merge_service.preview_lines(
        preview, merge_service.selected_alert_written(app_state, preview, choice))

    def _actions():
        solara.Button(t("evaluators.merge"), icon_name="mdi-call-merge", color="primary", small=True,
                      loading=busy, disabled=busy, classes=[ENTER_DEFAULT],
                      on_click=lambda: on_merge(choice if conflicts else "mine"))

    with form_card(t("evaluators.preview_title", evaluator=preview.header.evaluator), on_cancel=on_cancel,
                   actions=_actions, cancel_disabled=busy):
        solara.Text(summary)
        for line in notes:
            solara.Text(line, classes=["warning--text"])
        if conflicts:
            with field_group(t("evaluators.conflicts_label")):
                with rv.RadioGroup(v_model=choice, on_v_model=set_choice, row=True, dense=True, hide_details=True,
                                   disabled=busy):
                    rv.Radio(label=t("evaluators.keep_mine"), value="mine")
                    rv.Radio(label=t("evaluators.take_theirs"), value="theirs")


@solara.component
def _PreviewDialog(app_state, open_, pending, busy, on_merge):
    with use_form_dialog(open_, max_width="480px"):
        if open_.value and pending is not None:
            _PreviewFields(app_state=app_state, preview=pending["preview"], busy=busy,
                           on_cancel=lambda: open_.set(False), on_merge=on_merge)


@solara.component
def _CreatorPanel(app_state, app_tile_model, analysis, gee_interface):
    p = app_state.project.value
    s = active_set(p)
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    mode, set_mode = solara.use_state("count")
    count_text, set_count_text = solara.use_state("2")
    names_text, set_names_text = solara.use_state("")
    include, set_include = solara.use_state(False)
    confirm, set_confirm = solara.use_state(None)  # "resplit" / "remove" while asking
    building, set_building = solara.use_state(None)  # the evaluator whose package is being built
    links, set_links = solara.use_state({})  # (split_id, evaluator) -> link entry
    pending, set_pending = solara.use_state(None)  # {"preview", "file"} of the copy being merged
    preview_open = solara.use_reactive(False)
    # Bumped by every pick: the picker is remounted empty, so the next
    # reviewed copy is picked afresh (one zip at a time, §7.4).
    picks, set_picks = solara.use_state(0)
    # Set by the click that starts an action, cleared when its task ends.
    # Solara calls on_click whatever ``disabled`` the last render sent, and a
    # task called again cancels its asyncio run but not the worker thread
    # under it. So a second click (or Enter on Merge) is dropped here, before
    # it can start a second split, merge or build over the first.
    inflight = solara.use_ref(False)
    split = s.split

    def _reset():
        # Another project opened: its step starts from a fresh form, without
        # this project's preview or links. (A ``.key(p.name)`` on the panel
        # does not remount it: reacton re-renders a child subscribed to the
        # same reactive in place, keeping its state.)
        set_mode("count")
        set_count_text("2")
        set_names_text("")
        set_include(False)
        set_confirm(None)
        set_links({})
        set_pending(None)
        preview_open.set(False)

    solara.use_effect(_reset, [p.name])

    def _held(fn):
        async def run(*args):
            try:
                await fn(*args)
            finally:
                inflight.current = False
        return run

    async def _split(names, include_reviewed):
        live = app_state.project.peek()
        target = active_set(live)
        try:
            made = await to_thread_in_context(split_service.apply_split, app_state, app_tile_model, names,
                                              include_reviewed)
        except Exception as exc:  # noqa: BLE001 - a refusal, or the disk's error: toasted
            current_notifier().error(error_text(exc))
            return
        if target is not None and split_recorded(app_state, live.name, target.id, made):
            current_notifier().success(tn("evaluators.split_done", len(made.evaluators)))
        else:  # the store holds the split, the project lost the record: an orphan
            current_notifier().warning(t("evaluators.split_unrecorded", project=live.name if live else ""))

    split_task = solara.lab.use_task(_held(_split), dependencies=None, prefer_threaded=False, raise_error=False)

    async def _remove():
        try:
            await to_thread_in_context(split_service.remove_split, app_state, app_tile_model)
        except Exception as exc:  # noqa: BLE001 - toasted
            current_notifier().error(error_text(exc))
            return
        current_notifier().success(t("evaluators.removed"))

    remove_task = solara.lab.use_task(_held(_remove), dependencies=None, prefer_threaded=False, raise_error=False)

    async def _package(evaluator):
        live = app_state.project.peek()
        snap = live.model_copy(deep=True) if live is not None else None
        shared = active_set(snap)
        if shared is None or shared.split is None:
            return
        set_building(evaluator)
        try:
            pdir = paths.project_dir(snap.name)
            aoi = await package_aoi(snap, pdir, app_state.aoi_result.peek(), gee_interface)
            if aoi is None:
                current_notifier().warning(t("evaluators.aoi_waiting"))
                return
            path = await asyncio.to_thread(build_package_file, snap, pdir, shared.id, evaluator, aoi)
            entry = link_entry(path)
        except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
            current_notifier().error(error_text(exc))
            return
        finally:
            set_building(None)
        recorded = mark_packaged(app_state, snap.name, shared.id, shared.split.split_id,
                                 split_mod.questions_digest(snap.questions))
        set_links(lambda old: {**old, (shared.split.split_id, evaluator): entry})
        if recorded:
            current_notifier().success(t("evaluators.package_ready", evaluator=evaluator))
        else:  # the zip is built; the split it was made for is not the open one any more
            current_notifier().warning(t("evaluators.package_unrecorded", evaluator=evaluator))

    package_task = solara.lab.use_task(_held(_package), dependencies=None, prefer_threaded=False,
                                       raise_error=False)

    async def _preview(path):
        live = app_state.project.peek()
        store = set_store.active_set_alerts(live, app_state.alerts_gdf.peek())
        if live is None or store is None:
            current_notifier().warning(t("evaluators.loading"))
            return
        file = Path(path).name
        try:
            made = await asyncio.to_thread(share.read_return, path, live.model_copy(deep=True), store)
        except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
            current_notifier().error(failure_text(exc, file))
            return
        set_pending({"preview": made, "file": file})
        preview_open.set(True)

    preview_task = solara.lab.use_task(_held(_preview), dependencies=None, prefer_threaded=False,
                                       raise_error=False)

    async def _merge(copy, choice):
        made, live = copy["preview"], app_state.project.peek()
        target = active_set(live)  # the merge refuses any other set
        try:
            # In the session's context: the merge reloads the alert open in
            # Analysis (``reload_alert``), which writes widgets.
            ret = await to_thread_in_context(merge_service.merge_return, app_state, app_tile_model, analysis, made,
                                             choice)
        except Exception as exc:  # noqa: BLE001 - toasted; the preview stays open on the copy
            current_notifier().error(failure_text(exc, copy["file"]))
            return
        preview_open.set(False)
        set_pending(None)
        who = made.header.evaluator
        if target is not None and return_recorded(app_state, live.name, target.id, who, ret):
            current_notifier().success(t("evaluators.merged", taken=ret.taken, evaluator=who))
        else:  # the rows are merged in the store; the return record was not saved
            current_notifier().warning(t("evaluators.merged_unrecorded", evaluator=who,
                                         project=live.name if live else ""))

    merge_task = solara.lab.use_task(_held(_merge), dependencies=None, prefer_threaded=False, raise_error=False)

    def _launch(task, *args):
        """Start ``task`` unless an action of this panel is still running."""
        if inflight.current:
            return
        inflight.current = True
        task(*args)

    busy = split_task.pending or remove_task.pending or package_task.pending or merge_task.pending
    try:
        names, form_error = typed_evaluators(mode, count_text, names_text), None
    except split_mod.SplitError as exc:
        names, form_error = [], error_text(exc)
    typed = (count_text if mode == "count" else names_text).strip()
    shown = [form_error] if form_error and typed else []
    orphan = split is None and gdf is not None and split_service.orphan_assignments(p, gdf)
    stats, unassigned = evaluator_rows(split, gdf)
    ready = aoi_ready(p, paths.project_dir(p.name), app_state.aoi_result.value)

    def _run(action):
        set_confirm(None)
        if action == "remove":
            _launch(remove_task)
        else:
            _launch(split_task, names, include)

    def _ask(action):
        if inflight.current:
            return  # not even a confirmation while an action runs
        # Packages already handed out stop merging once their split goes (§4.1).
        if split is not None and split.packaged:
            set_confirm(action)
        else:
            _run(action)

    def _pick(path):
        set_picks(lambda n: n + 1)
        if path:
            _launch(preview_task, path)

    with solara.Column(style="gap:16px;"):
        with rv.Card(outlined=True, class_="pa-3"):
            solara.Text(t("evaluators.split_title"), style="font-weight:600;")
            with field_group(t("evaluators.mode_label")):
                with rv.RadioGroup(v_model=mode, on_v_model=set_mode, row=True, dense=True, hide_details=True,
                                   disabled=busy):
                    rv.Radio(label=t("evaluators.mode_count"), value="count")
                    rv.Radio(label=t("evaluators.mode_names"), value="names")
            if mode == "count":
                rv.TextField(label=t("evaluators.count_label"), v_model=count_text,
                             on_v_model=lambda value: set_count_text(value or ""), type="number", dense=True,
                             outlined=True, class_="mt-3", error_messages=shown, disabled=busy)
            else:
                rv.Textarea(label=t("evaluators.names_label"), v_model=names_text,
                            on_v_model=lambda value: set_names_text(value or ""), hint=t("evaluators.names_hint"),
                            persistent_hint=not shown, rows=4, auto_grow=True, dense=True, outlined=True,
                            class_="mt-3", error_messages=shown, disabled=busy)
            rv.Checkbox(label=t("evaluators.include_reviewed"), v_model=include,
                        on_v_model=lambda value: set_include(bool(value)), dense=True, hide_details=True,
                        disabled=busy)
            solara.Text(t("evaluators.include_reviewed_hint"), style=MUTED + "font-size:0.75rem;")
            if orphan:
                solara.Text(t("evaluators.orphan"), classes=["warning--text"])
            with solara.Row(style="gap:8px;flex-wrap:wrap;margin-top:8px;"):
                if split is None:
                    label = t("evaluators.split")
                else:
                    label = t("evaluators.resplit")
                solara.Button(label, icon_name="mdi-account-multiple-plus-outline", color="primary", small=True,
                              loading=split_task.pending, disabled=busy or gdf is None or form_error is not None,
                              on_click=lambda: _ask("resplit"))
                if split is not None or orphan:
                    solara.Button(t("evaluators.remove"), icon_name="mdi-account-multiple-remove-outline",
                                  color="error", outlined=True, small=True, loading=remove_task.pending,
                                  disabled=busy, on_click=lambda: _ask("remove"))
        with rv.Card(outlined=True, class_="pa-3"):
            if gdf is None:
                solara.Text(t("evaluators.loading"), style=MUTED)
            ProductTable(title=t("evaluators.table_title"), columns=table_columns(),
                         rows=_table_rows(stats, unassigned, on_package=lambda name: _launch(package_task, name), building=building,
                                          can_package=ready and gdf is not None and not busy),
                         empty_text=t("evaluators.empty"))
            if split is not None and not ready:
                solara.Text(t("evaluators.aoi_waiting"), style=MUTED + "font-size:0.75rem;")
            for name in (split.evaluators if split is not None else []):
                entry = links.get((split.split_id, name))
                if entry is not None:
                    _download_link(entry, t("evaluators.package_link", file=entry["file"]),
                                   "defo-package-link").key(f"package:{name}")
        if split is not None:
            with rv.Card(outlined=True, class_="pa-3"):
                solara.Text(t("evaluators.import_title"), style="font-weight:600;")
                with solara.Column(style="gap:2px;").key(f"import:{picks}"):
                    FileInputComponent(extensions=ZIP, value="", on_value=_pick,
                                       label=t("evaluators.import_label"))
                solara.Text(t("evaluators.import_hint"), style=MUTED + "font-size:0.8rem;")
                if preview_task.pending:
                    rv.ProgressLinear(indeterminate=True, color="primary")

    if confirm == "remove":
        title, confirm_label = t("evaluators.remove_title"), t("evaluators.remove_confirm")
    else:
        title, confirm_label = t("evaluators.resplit_title"), t("evaluators.resplit_confirm")
    ConfirmDialog(open=confirm is not None, on_cancel=lambda: set_confirm(None), on_confirm=lambda: _run(confirm),
                  title=title, message=t("evaluators.packaged_warning"), confirm_label=confirm_label)
    _PreviewDialog(app_state=app_state, open_=preview_open, pending=pending, busy=merge_task.pending,
                   on_merge=lambda choice: _launch(merge_task, pending, choice))


@solara.component
def _ReturnPanel(app_state):
    """A review copy's *Return reviews* (§6.4): its progress and the reviewed
    copy, built any time (each holds the latest state)."""
    p = app_state.project.value
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    link, set_link = solara.use_state(None)
    inflight = solara.use_ref(False)  # as _CreatorPanel's: a second click is dropped
    # Another copy opened: its panel starts without this one's link.
    solara.use_effect(lambda: set_link(None), [p.name if p else None])

    async def _build():
        try:
            live = app_state.project.peek()
            if live is None or live.review_copy is None:
                return
            snap = live.model_copy(deep=True)
            try:
                path = await asyncio.to_thread(build_return_file, snap, paths.project_dir(snap.name))
                entry = link_entry(path)
            except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
                current_notifier().error(error_text(exc))
                return
            set_link({**entry, "project": snap.name})
            current_notifier().success(t("evaluators.return_ready"))
        finally:
            inflight.current = False

    task = solara.lab.use_task(_build, dependencies=None, prefer_threaded=False, raise_error=False)

    def _start():
        if inflight.current:
            return
        inflight.current = True
        task()
    reviewed, assigned = return_progress(p, gdf)

    with rv.Card(outlined=True, class_="pa-3"):
        # solara.Text is an inline span: each line is its own block.
        solara.Text(t("evaluators.return_title"), style="display:block;font-weight:600;")
        if gdf is None:
            solara.Text(t("evaluators.loading"), style="display:block;" + MUTED)
        else:
            solara.Text(t("evaluators.return_progress", reviewed=format_number(reviewed),
                          assigned=format_number(assigned)), style="display:block;")
        solara.Text(t("evaluators.return_note"), style="display:block;" + MUTED + "font-size:0.8rem;")
        solara.Button(t("evaluators.download_return"), icon_name="mdi-package-up", color="primary", outlined=True,
                      small=True, block=True, classes=["mt-2"], loading=task.pending, disabled=task.pending,
                      on_click=_start)
        if link is not None and p is not None and link["project"] == p.name:  # not a build of another copy
            _download_link(link, t("evaluators.return_link", file=link["file"]), "defo-return-link")


@solara.component
def EvaluatorsStep(app_state, app_tile_model, analysis, gee_interface):
    """``app_tile_model`` is the shell's ``SetContext`` and ``analysis`` its
    ``AnalysisState``: the split and merge services write through them."""
    p = app_state.project.value
    s = active_set(p)
    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('evaluators.title')}")
        StepIntro(t("steps.evaluators.description"), t("steps.evaluators.info_header"),
                  t("steps.evaluators.info_md"))
        if p is None:
            solara.Info(t("workflow.lock_project"))
        elif s is not None and read_only(p):
            # A review copy neither splits nor builds packages (§6.3): it returns.
            _ReturnPanel(app_state=app_state)
        elif s is not None:
            _CreatorPanel(app_state=app_state, app_tile_model=app_tile_model, analysis=analysis,
                          gee_interface=gee_interface)
        # Without an active set, Start analysis says why (workflow.lock_analysis).
        _StartAnalysis(app_state=app_state, ready=s is not None)
