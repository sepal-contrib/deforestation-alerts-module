"""The Evaluators step (spec 2026-10-10 §4): split the active set's alerts
between evaluators, hand each one a review package, and merge the reviewed
copies they send back. In a review copy (§6.4) the step shows the *Return
reviews* panel instead: the evaluator's progress and the reviewed copy to
send back.

The store is the source of truth for the assignments (its ``evaluator``
column); the table counts them from the active set's loaded alerts. Every
store write (split, remove, merge) goes through ``split_service`` /
``merge_service``, which hold the per-store lock (§4.4). Builds, reads and
writes run off the event loop with their button loading (§9). **Generate
packages** builds every evaluator's zip into the set folder; each row's
cloud icon is then that zip's download (``package_files`` finds the zips
again by their header, so they survive a reload).

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
from gui.scripts import aoi_io, merge_service, package_files, remote_facts, set_store, split_service
from gui.scripts.error_text import error_text
from gui.scripts.notify_bridge import current_notifier
from gui.scripts.setup_guard import update_setup
from gui.scripts.solara_threads import to_thread_in_context
from gui.store.project_writers import writing
from gui.store.workflow_steps import active_set
from gui.tile.questionnaire_step import _StartAnalysis
from gui.widget.enter_key import ENTER_DEFAULT
from gui.widget.form_dialog import field_group, form_card, use_form_dialog
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable, link_action
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


def split_removed(app_state, name: str, set_id: str) -> bool:
    """Whether the open project ``name`` holds no split on set ``set_id``
    (``remove_split`` can end without recording anything: no evaluator
    column and no alerts on hand, or another project opened meanwhile)."""
    live = app_state.project.value
    s = live.alert_sets.get(set_id) if live is not None and live.name == name else None
    return s is not None and s.split is None


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


def found_links(zips) -> dict:
    """The download of each built zip (``package_files.BuiltZip``) by
    evaluator; a zip outside ``$HOME`` has no link and is left out."""
    out = {}
    for name, z in zips.items():
        try:
            entry = link_entry(z.path)
        except ValueError:
            continue
        out[name] = {**entry, "outdated": z.outdated, "built_at": z.built_at}
    return out


def return_link(p) -> Optional[dict]:
    """A review copy's reviewed copy as a download, once one is built."""
    z = package_files.return_zip(p)
    if z is None:
        return None
    try:
        entry = link_entry(z.path)
    except ValueError:  # outside $HOME: no link to give
        return None
    return {**entry, "built_at": z.built_at}


def building_here(building, p, split) -> Optional[str]:
    """The evaluator whose package is being built for ``p``'s ``split``, from
    the panel's ``(project, split_id, evaluator)``: None while a run started
    in another project, or for another split, is building (the panel is not
    remounted on a project switch), or while the run is still fetching the
    AOI (evaluator None)."""
    if building is None or p is None or split is None or building[:2] != (p.name, split.split_id):
        return None
    return building[2]


def package_action(name: str, found, building_name: Optional[str]) -> dict:
    """A row's cloud icon (spec 2026-10-14 §4.4): it spins while that
    evaluator's zip is built (``building_name``, already resolved for this
    project and split by ``building_here``), is dim until one exists for this
    split, then downloads it, amber when the questionnaire changed since."""
    if building_name == name:
        return {"kind": "download", "href": None, "loading": True,
                "title": t("evaluators.package_download", evaluator=name)}
    if found is None:
        return {"kind": "download", "href": None, "title": t("evaluators.generate_first")}
    return {"kind": "download", "href": found["url"], "file": found["file"],
            "color": "warning" if found["outdated"] else "primary",
            "title": t("evaluators.package_outdated") if found["outdated"]
            else t("evaluators.package_download", evaluator=name)}


def _table_rows(stats, unassigned, *, found, building_name) -> list:
    rows = [{"key": s["name"],
             "cells": [{"value": s["name"]},
                       {"value": format_number(s["assigned"])},
                       {"value": format_number(s["reviewed"])},
                       {"value": s["merged_at"].strftime("%Y-%m-%d") if s["merged_at"] else NO_VALUE,
                        "muted": s["merged_at"] is None}],
             "actions": [package_action(s["name"], found.get(s["name"]), building_name)]}
            for s in stats]
    if unassigned:
        rows.append({"key": "", "actions": [],
                     "cells": [{"value": t("evaluators.unassigned"), "muted": True},
                               {"value": format_number(unassigned)},
                               {"value": NO_VALUE, "muted": True}, {"value": NO_VALUE, "muted": True}]})
    return rows


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
def _SplitFields(form, form_error, split, orphan, packaged, busy, on_cancel, on_run):
    """The split modal's card (spec 2026-10-14 §4.3), mounted while it is
    open. ``form`` is the panel's ``{"mode", "count", "names", "include"}``
    and their setters, so a reopened modal shows what was last typed. With a
    split, or assignments without one, it offers Remove split on the left;
    a split that packages were built for says so here, and one click on
    Re-split or Remove split proceeds. ``packaged`` is the split's record or
    zips of it found on disk: a run that ended after a project switch built
    zips the split does not record, and they may have been sent."""
    mode = form["mode"]
    typed = (form["count"] if mode == "count" else form["names"]).strip()
    shown = [form_error] if form_error and typed else []
    editing = split is not None or orphan

    def _leading():
        if editing:
            solara.Button(t("evaluators.remove"), icon_name="mdi-account-multiple-remove-outline", color="error",
                          text=True, small=True, disabled=busy, on_click=lambda: on_run("remove"))

    def _actions():
        label = t("evaluators.resplit") if split is not None else t("evaluators.split")
        solara.Button(label, icon_name="mdi-account-multiple-plus-outline", color="primary", small=True,
                      disabled=busy or form_error is not None, classes=[ENTER_DEFAULT],
                      on_click=lambda: on_run("split"))

    title = t("evaluators.edit_title") if editing else t("evaluators.split_title")
    with form_card(title, on_cancel=on_cancel, actions=_actions, leading=_leading, cancel_disabled=busy):
        with field_group(t("evaluators.mode_label")):
            with rv.RadioGroup(v_model=mode, on_v_model=form["set_mode"], row=True, dense=True, hide_details=True,
                               disabled=busy):
                rv.Radio(label=t("evaluators.mode_count"), value="count")
                rv.Radio(label=t("evaluators.mode_names"), value="names")
        if mode == "count":
            rv.TextField(label=t("evaluators.count_label"), v_model=form["count"],
                         on_v_model=lambda value: form["set_count"](value or ""), type="number", dense=True,
                         outlined=True, class_="mt-3", error_messages=shown, disabled=busy)
        else:
            rv.Textarea(label=t("evaluators.names_label"), v_model=form["names"],
                        on_v_model=lambda value: form["set_names"](value or ""), hint=t("evaluators.names_hint"),
                        persistent_hint=not shown, rows=4, auto_grow=True, dense=True, outlined=True,
                        class_="mt-3", error_messages=shown, disabled=busy)
        rv.Checkbox(label=t("evaluators.include_reviewed"), v_model=form["include"],
                    on_v_model=lambda value: form["set_include"](bool(value)), dense=True, hide_details=True,
                    disabled=busy)
        solara.Text(t("evaluators.include_reviewed_hint"), style=MUTED + "font-size:0.75rem;")
        if split is not None and packaged:
            solara.Text(t("evaluators.packaged_warning"), classes=["warning--text"],
                        style="display:block;font-size:0.8rem;")


@solara.component
def _SplitDialog(open_, form, form_error, split, orphan, packaged, busy, on_run):
    with use_form_dialog(open_, max_width="480px"):
        if open_.value:
            _SplitFields(form=form, form_error=form_error, split=split, orphan=orphan, packaged=packaged, busy=busy,
                         on_cancel=lambda: open_.set(False), on_run=on_run)


@solara.component
def _MergePick(picks, reading, busy, on_cancel, on_pick):
    """The merge modal's first stage (spec 2026-10-14 §4.5): one reviewed copy,
    picked afresh after each pick (``picks`` remounts the picker)."""
    with form_card(t("evaluators.merge_title"), on_cancel=on_cancel, actions=lambda: None, cancel_disabled=busy):
        with solara.Column(style="gap:2px;").key(f"import:{picks}"):
            FileInputComponent(extensions=ZIP, value="", on_value=on_pick, label=t("evaluators.import_label"))
        solara.Text(t("evaluators.import_hint"), style=MUTED + "font-size:0.8rem;")
        if reading:
            rv.ProgressLinear(indeterminate=True, color="primary")


@solara.component
def _MergeDialog(app_state, open_, pending, picks, reading, busy, on_pick, on_merge, on_cancel):
    """One modal, two stages: the picker, then the preview of the copy read
    (``_PreviewFields``, mounted afresh for each copy so it starts on Keep
    mine). Cancel, or ESC, drops the preview."""
    with use_form_dialog(open_, max_width="480px", on_close=on_cancel):
        if open_.value:
            if pending is None:
                _MergePick(picks=picks, reading=reading, busy=busy, on_cancel=on_cancel, on_pick=on_pick)
            else:
                _PreviewFields(app_state=app_state, preview=pending["preview"], busy=busy, on_cancel=on_cancel,
                               on_merge=on_merge)


@solara.component
def _CreatorPanel(app_state, app_tile_model, analysis, gee_interface):
    """The evaluators table under the switch (spec 2026-10-14 §4.2), with the
    split form and the reviewed-copy import in modals (§4.3, §4.5)."""
    p = app_state.project.value
    s = active_set(p)
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    mode, set_mode = solara.use_state("count")
    count_text, set_count_text = solara.use_state("2")
    names_text, set_names_text = solara.use_state("")
    include, set_include = solara.use_state(False)
    # (project, split_id, evaluator) of the package being built; evaluator
    # None while the run fetches the AOI. The project and split say whose
    # run it is: the panel outlives a project switch (see ``_reset``).
    building, set_building = solara.use_state(None)
    scans, set_scans = solara.use_state(0)  # bumped by each Generate run: rescan the set folder
    pending, set_pending = solara.use_state(None)  # {"preview", "file"} of the copy being merged
    split_open = solara.use_reactive(False)
    merge_open = solara.use_reactive(False)
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
        # this project's preview or open modal. (A ``.key(p.name)`` on
        # the panel does not remount it: reacton re-renders a child subscribed
        # to the same reactive in place, keeping its state.)
        set_mode("count")
        set_count_text("2")
        set_names_text("")
        set_include(False)
        set_pending(None)
        split_open.set(False)
        merge_open.set(False)

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
        live = app_state.project.peek()
        target = active_set(live)
        try:
            await to_thread_in_context(split_service.remove_split, app_state, app_tile_model)
        except Exception as exc:  # noqa: BLE001 - toasted
            current_notifier().error(error_text(exc))
            return
        if target is not None and split_removed(app_state, live.name, target.id):
            current_notifier().success(t("evaluators.removed"))
        else:  # the project still holds the split, or another one is open
            current_notifier().warning(t("evaluators.remove_unrecorded", project=live.name if live else ""))

    remove_task = solara.lab.use_task(_held(_remove), dependencies=None, prefer_threaded=False, raise_error=False)

    async def _generate():
        """Every evaluator's package of the current split, in split order
        (spec 2026-10-14 §4.4). The first failure is toasted and stops the
        run; the zips built before it stay and are offered."""
        live = app_state.project.peek()
        snap = live.model_copy(deep=True) if live is not None else None
        shared = active_set(snap)
        if shared is None or shared.split is None:
            return
        pdir = paths.project_dir(snap.name)
        built = 0
        mine = (snap.name, shared.split.split_id)
        set_building((*mine, None))
        try:
            aoi = await package_aoi(snap, pdir, app_state.aoi_result.peek(), gee_interface)
            if aoi is None:
                current_notifier().warning(t("evaluators.aoi_waiting"))
                return
            for name in shared.split.evaluators:
                set_building((*mine, name))
                await asyncio.to_thread(build_package_file, snap, pdir, shared.id, name, aoi)
                built += 1
        except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
            current_notifier().error(error_text(exc))
        finally:
            set_building(None)
            set_scans(lambda n: n + 1)
        if not built:
            return
        recorded = mark_packaged(app_state, snap.name, shared.id, shared.split.split_id,
                                 split_mod.questions_digest(snap.questions))
        if not recorded:  # the zips are built; the split they were made for is not the open one any more
            current_notifier().warning(t("evaluators.packages_unrecorded"))
        elif built == len(shared.split.evaluators):
            current_notifier().success(tn("evaluators.packages_ready", built))

    generate_task = solara.lab.use_task(_held(_generate), dependencies=None, prefer_threaded=False,
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
        merge_open.set(False)
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

    busy = split_task.pending or remove_task.pending or generate_task.pending or merge_task.pending
    try:
        names, form_error = typed_evaluators(mode, count_text, names_text), None
    except split_mod.SplitError as exc:
        names, form_error = [], error_text(exc)
    orphan = bool(split is None and gdf is not None and split_service.orphan_assignments(p, gdf))
    stats, unassigned = evaluator_rows(split, gdf)
    ready = aoi_ready(p, paths.project_dir(p.name), app_state.aoi_result.value)
    digest = split_mod.questions_digest(p.questions)
    # The set folder is read again only when what decides a zip's fate
    # changes: the project, the set, the split, the questionnaire or a run.
    found = solara.use_memo(lambda: found_links(package_files.package_zips(p, s)),
                            [p.name, s.id, split.split_id if split is not None else None, digest, scans])
    newest = max((f["built_at"] for f in found.values()), default=None)
    building_name = building_here(building, p, split) if generate_task.pending else None
    # Before its first build the run fetches the AOI: the button spins.
    preparing = bool(generate_task.pending and split is not None
                     and building == (p.name, split.split_id, None))
    if building_name in (split.evaluators if split is not None else []):
        generate_label = t("evaluators.generating", i=split.evaluators.index(building_name) + 1,
                           n=len(split.evaluators))
    elif found:
        generate_label = t("evaluators.regenerate")
    else:
        generate_label = t("evaluators.generate")
    editing = split is not None or orphan

    def _run(action):
        """The split modal's Split, Re-split or Remove split: the modal closes
        as the action starts; its toast says how it ended."""
        if inflight.current:
            return
        split_open.set(False)
        if action == "remove":
            _launch(remove_task)
        else:
            _launch(split_task, names, include)

    def _pick(path):
        set_picks(lambda n: n + 1)
        if path:
            _launch(preview_task, path)

    def _close_merge():
        # ESC while a merge runs: the modal stays on the running merge (a pick
        # made now would be dropped by ``_launch``). Cancel is disabled then.
        # ``use_form_dialog`` has already set ``open_`` False: undo that.
        if merge_task.pending:
            merge_open.set(True)
            return
        # A copy still being read is dropped too: cancelled, its run stops
        # before it sets the preview (``_held`` still clears ``inflight``).
        if preview_task.pending:
            preview_task.cancel()
        merge_open.set(False)
        set_pending(None)

    def _header_action():
        solara.Button(t("evaluators.edit_split") if editing else t("evaluators.open_split"),
                      icon_name="mdi-pencil-outline" if editing else "mdi-account-multiple-plus-outline",
                      color="primary", text=True, x_small=True, disabled=busy or gdf is None,
                      loading=split_task.pending or remove_task.pending,
                      classes=["defo-split-open"], on_click=lambda: split_open.set(True))

    form = {"mode": mode, "count": count_text, "names": names_text, "include": include, "set_mode": set_mode,
            "set_count": set_count_text, "set_names": set_names_text, "set_include": set_include}

    with solara.Column(style="gap:8px;margin-top:8px;"):
        rv.Divider()
        if gdf is None:
            solara.Text(t("evaluators.loading"), style=MUTED)
        if orphan:
            solara.Text(t("evaluators.orphan"), classes=["warning--text"])
        ProductTable(title=t("evaluators.table_title"), columns=table_columns(),
                     rows=_table_rows(stats, unassigned, found=found, building_name=building_name),
                     empty_text=t("evaluators.empty"), header_action=_header_action)
        if split is not None and not ready:
            solara.Text(t("evaluators.aoi_waiting"), style=MUTED + "font-size:0.75rem;")
        if newest is not None:
            solara.Text(t("evaluators.built_at", when=newest.strftime("%Y-%m-%d %H:%M")),
                        style="display:block;" + MUTED + "font-size:0.75rem;")
        if split is not None:
            with solara.Row(style="gap:8px;flex-wrap:wrap;"):
                solara.Button(generate_label, icon_name="mdi-package-variant-closed", color="primary", outlined=True,
                              small=True, loading=preparing, disabled=busy or not ready or gdf is None,
                              classes=["defo-generate"],
                              style="flex:1 1 150px;", on_click=lambda: _launch(generate_task))
                solara.Button(t("evaluators.merge_open"), icon_name="mdi-call-merge", color="primary", outlined=True,
                              small=True, disabled=busy, style="flex:1 1 150px;",
                              on_click=lambda: merge_open.set(True))

    _SplitDialog(open_=split_open, form=form, form_error=form_error, split=split, orphan=orphan,
                 packaged=split is not None and (split.packaged or bool(found)), busy=busy, on_run=_run)
    _MergeDialog(app_state=app_state, open_=merge_open, pending=pending, picks=picks, reading=preview_task.pending,
                 busy=merge_task.pending, on_pick=_pick, on_cancel=_close_merge,
                 on_merge=lambda choice: _launch(merge_task, pending, choice))


@solara.component
def _ReturnPanel(app_state):
    """A review copy's *Return reviews* (§6.4, spec 2026-10-14 §4.6): its
    progress with the reviewed copy's cloud icon, and *Generate reviewed
    copy*. A reviewed copy holds the reviews as of its build; "Built …" says
    when, so the evaluator generates it again after reviewing more."""
    p = app_state.project.value
    gdf = set_store.active_set_alerts(p, app_state.alerts_gdf.value)
    scans, set_scans = solara.use_state(0)  # bumped by each build: look in the set folder again
    inflight = solara.use_ref(False)  # as _CreatorPanel's: a second click is dropped
    # The copy a build was started for: the panel outlives a project switch,
    # so another copy opened mid-build shows no spinner and no "ready" toast.
    built_for = solara.use_ref(None)
    found = solara.use_memo(lambda: return_link(p), [p.name if p else None, scans])

    async def _build():
        try:
            live = app_state.project.peek()
            if live is None or live.review_copy is None:
                return
            snap = live.model_copy(deep=True)
            try:
                await asyncio.to_thread(build_return_file, snap, paths.project_dir(snap.name))
            except Exception as exc:  # noqa: BLE001 - a refusal (ShareError) or the disk's error: toasted
                current_notifier().error(error_text(exc))
                return
            now = app_state.project.peek()
            if now is not None and now.name == snap.name:  # else its cloud shows when that copy is reopened
                current_notifier().success(t("evaluators.return_ready"))
        finally:
            set_scans(lambda n: n + 1)
            inflight.current = False

    task = solara.lab.use_task(_build, dependencies=None, prefer_threaded=False, raise_error=False)

    def _start():
        if inflight.current:
            return
        inflight.current = True
        live = app_state.project.peek()
        built_for.current = live.name if live is not None else None
        task()

    reviewed, assigned = return_progress(p, gdf)
    building = task.pending and p is not None and built_for.current == p.name
    with rv.Card(outlined=True, class_="pa-3"):
        # solara.Text is an inline span: each line is its own block.
        solara.Text(t("evaluators.return_title"), style="display:block;font-weight:600;")
        with solara.Row(style="align-items:center;gap:8px;flex-wrap:nowrap;"):
            if gdf is None:
                solara.Text(t("evaluators.loading"), style="flex:1 1 auto;" + MUTED)
            else:
                solara.Text(t("evaluators.return_progress", reviewed=format_number(reviewed),
                              assigned=format_number(assigned)), style="flex:1 1 auto;")
            link_action({"kind": "download", "href": found["url"] if found else None,
                         "file": found["file"] if found else "", "loading": building,
                         "title": t("evaluators.return_download") if found else t("evaluators.generate_return_first")})
        if found is not None:
            solara.Text(t("evaluators.built_at", when=found["built_at"].strftime("%Y-%m-%d %H:%M")),
                        style="display:block;" + MUTED + "font-size:0.75rem;")
        solara.Text(t("evaluators.return_note"), style="display:block;" + MUTED + "font-size:0.8rem;")
        solara.Button(t("evaluators.regenerate_return") if found else t("evaluators.generate_return"),
                      icon_name="mdi-package-up", color="primary", outlined=True, small=True, block=True,
                      classes=["mt-2", "defo-generate-return"], loading=building, disabled=task.pending,
                      on_click=_start)


@solara.component
def _ReviewMode(app_state):
    """Solo or multiple evaluators, as a select whose note says what the chosen
    mode means, so the step always shows which one is on. A split holds it on
    Multiple: switching to solo would leave alerts assigned to evaluators the
    step no longer shows."""
    p = app_state.project.value
    split = active_set(p).split
    on = bool(p.multi_evaluator)

    def _set(value):
        update_setup(app_state, p.name, lambda live: setattr(live, "multi_evaluator", value == "multi"))

    if split is not None:
        note = t("evaluators.multi_locked")
    elif on:
        note = t("evaluators.multi_note")
    else:
        note = t("evaluators.solo_note")
    rv.Select(label=t("evaluators.review_mode_label"), v_model="multi" if on else "solo", on_v_model=_set,
              items=[{"text": t("evaluators.review_mode_solo"), "value": "solo"},
                     {"text": t("evaluators.review_mode_multi"), "value": "multi"}],
              prepend_inner_icon="mdi-account-group" if on else "mdi-account", disabled=split is not None,
              dense=True, outlined=True, hide_details=True)
    rv.Html(tag="div", class_="defo-mode-note", style_="margin-top:4px;" + MUTED + "font-size:0.75rem;",
            children=[note])


@solara.component
def EvaluatorsStep(app_state, app_tile_model, analysis, gee_interface):
    """``app_tile_model`` is the shell's ``SetContext`` and ``analysis`` its
    ``AnalysisState``: the split and merge services write through them. The
    step reads "Evaluation"; its key, page view and module keep "evaluators"."""
    p = app_state.project.value
    s = active_set(p)
    with solara.Column(style="gap:16px;"):
        StepIntro(t("steps.evaluators.description"), t("steps.evaluators.info_header"),
                  t("steps.evaluators.info_md"))
        if p is None:
            solara.Info(t("workflow.lock_project"))
        elif s is not None and read_only(p):
            # A review copy neither splits nor builds packages (§6.3): it returns.
            _ReturnPanel(app_state=app_state)
        elif s is not None:
            with rv.Card(outlined=True, class_="pa-3"):
                _ReviewMode(app_state=app_state)
                if p.multi_evaluator:
                    _CreatorPanel(app_state=app_state, app_tile_model=app_tile_model, analysis=analysis,
                                  gee_interface=gee_interface)
        # Without an active set, Start analysis says why (workflow.lock_analysis).
        _StartAnalysis(app_state=app_state, ready=s is not None)
