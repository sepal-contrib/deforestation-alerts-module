"""Alerts step (spec §2, mockup ``alerts-step-v3``): the active period's
alert sources, their coverage of the AOI and of the period, and their
download.

One row per source of the period (``period_sources``), built from the
download/harmonize row parts limited to the raw file
(``prepare_view.download_state`` / ``download_subtitle``): a user file is
read, not downloaded, so its pill reads Read file. Two chips say whether the
source covers the AOI and the period's search window
(``coverage_ui.coverage_chips``); a blocked row (0 % AOI, no data for the
window, not checked) cannot be downloaded.

The rows share the Harmonize step's job state (``PrepareJobs``): one in-flight
key per row (``prep:{project}:{period}:{source}``), so a row downloading here
reads running there and two jobs never run one row. A download runs with
``mode="download"`` and only fetches the raw file: it never chains into
harmonizing, which waits for its own click in Harmonize (option C, user
decision 2026-10-30). The two steps' jobs are independent (scope
``(project, period, "download")``); a row a Harmonize job holds keeps its
download state with its pill and actions off.

🗑 is *Remove from this period*: it drops this period's raw and harmonized
files of the source and its use in the period; the definition stays in the
project (the cap of 6 custom sources is per period), and the period's sets
built from it read out of date.
"""

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, preview_window, record_for, window_coverage
from deforestation_alerts.engine.fingerprint import (
    Status, harm_key, is_file_source, is_vector_source, raw_key,
)
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.engine.why import source_reason
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import (
    add_custom_source, add_predefined_source, period_sources, set_new_period_source, set_period_source,
    sets_using_source, update_custom_source,
)
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import t, tn
from gui.scripts import aoi_io, raster_map, source_layers
from gui.scripts.coverage_ui import TONE_COLORS, CheckAgainButton, coverage_chips, note_text
from gui.scripts.error_text import error_text
from gui.scripts.prepare_jobs import progress_key
from gui.scripts.prepare_scope import inflight_key, start_items
from gui.scripts.prepare_view import download_state, download_subtitle, source_details, source_label
from gui.scripts.setup_guard import refuse_setup, update_setup
from gui.store import workflow_steps
from gui.store.project_writers import is_writing
from gui.tile.prepare_step import (
    mine_keys, refresh_confirm, scope_stages, source_columns,
)
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.details_dialog import DetailsDialog
from gui.widget.product_table import ProductTable
from gui.widget.source_modal import SourceModal

SOURCE_ACTIONS = 4  # map, details, download again, remove from this period


def blocked_reason(p, name):
    """Why ``name`` cannot be previewed for the active window, or None."""
    cov = record_for(p, name)
    if cov is None:
        return t("coverage.unchecked")
    a, b = preview_window(p, name)
    state, notes = window_coverage(cov, name, a, b)
    return note_text(notes[0]) if state in (NOT_COVERED, NO_DATA) else None


def _art(p, key):
    art = p.artifacts.get(key)
    return None if art is None or art.no_data else art


def alert_rows(p, pdir, period, *, mine, errors, stages, progress, on, building, locked, writing,
               run, toggle, on_info, on_refresh, on_remove, rows=None, info_open=None, pending_cov=False,
               held=None) -> list:
    """The ``ProductTable`` rows of the Alerts step (spec §2); pure. One per
    source of ``period``; ``rows``: its ``plan_prepare`` (read when None).

    ``mine``/``stages``/``progress``/``errors``: the step's own
    (download) job state, as ``prepare_step.build_rows`` reads it; ``held``:
    every claimed key (None: ``mine``): a row a Harmonize job holds keeps its
    download state, its pill and actions off; ``on``/``building``: the
    map keys shown and being built; ``locked``: a review copy; ``writing``: a
    job writes into the project (Remove waits). ``run``/``toggle``/
    ``on_info``/``on_refresh``/``on_remove``: the pill and the actions, called
    with the source. ``pending_cov``: the coverage check runs."""
    rows = plan_prepare(p, pdir, period) if rows is None else rows
    out = []
    for r in rows:
        src = p.sources[r.source]
        key_inflight = inflight_key(p.name, period.id, r.source)
        downloading = key_inflight in mine  # only a download shows here (option C)
        busy = key_inflight in (mine if held is None else held)
        pkey = progress_key(p.name, period.id, "source", r.source)
        running = stages.get(pkey) if downloading else None
        pct = progress.get(pkey) if downloading else None
        queued = downloading and running is None
        file_source = is_file_source(src)
        reason = source_reason(p, period, r.source) if not (r.raw is Status.CURRENT or r.blocked) else None
        subtitle, tone = download_subtitle(r, file_source=file_source, raw_art=_art(p, raw_key(period.id, r.source)),
                                           reason=reason, queued=queued, vector=is_vector_source(src))
        chips = [{"value": c["label"], "color": TONE_COLORS[c["tone"]]}
                 for c in coverage_chips(p, r.source, period, pending=pending_cov)]
        title = {"type": "title", "value": source_label(src), "subtitle": subtitle, "chips": chips}
        if tone:
            title["subtitle_tone"] = tone
        state = download_state(r, file_source=file_source, stage=running, queued=queued, pct=pct,
                               failed=r.source in errors, locked=locked, on_run=(lambda s=r.source: run(s)))
        if busy and state["type"] == "verb":  # a Harmonize job holds the row
            state["disabled"] = True
        notes = []
        if r.file_missing:
            notes.append({"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"})
        if src.kind == "predefined" and src.catalogue_key == "RADD":
            notes.append({"text": t("coverage.radd_note"), "tone": "info"})
        map_key = f"src:{r.source}"
        out.append({"key": r.source, "cells": [title, state], "notes": notes, "error": errors.get(r.source),
                    "actions": [
                        # a layer already on can always be switched off
                        {"kind": "map_toggle", "is_on": map_key in on, "loading": map_key in building,
                         "disabled": blocked_reason(p, r.source) is not None and map_key not in on,
                         "on_click": (lambda s=r.source: toggle(s))},
                        {"kind": "info", "is_on": info_open == r.source, "on_click": (lambda s=r.source: on_info(s))},
                        {"kind": "refresh", "disabled": busy or locked or r.blocked or file_source,
                         "title": t("steps.prepare.action_nothing_to_fetch" if file_source
                                    else "steps.prepare.action_download_again"),
                         "on_click": (lambda s=r.source: on_refresh(s))},
                        # the confirm re-checks: a job can start while it is open
                        {"kind": "delete", "disabled": busy or writing or locked,
                         "title": t("steps.alerts.delete_busy" if (busy or writing) and not locked
                                    else "steps.alerts.action_remove"),
                         "on_click": (lambda s=r.source: on_remove(s))},
                    ]})
    return out


@solara.component
def AlertsStep(app_state, map_, gee_interface, layers, jobs):
    """``jobs``: the session's ``PrepareJobs``, shared with the Harmonize step."""
    notifications = use_notifications()
    p = app_state.project.value
    on = layers.on.value  # subscribe: the map-toggle icons follow the registry
    building = layers.inflight.value
    running = jobs.inflight.value
    modal_open = solara.use_reactive(False)
    new_periods = solara.use_reactive(True)  # the modal's Add to new periods
    editing, set_editing = solara.use_state(None)
    info_source, set_info_source = solara.use_state(None)
    pending_refresh, set_pending_refresh = solara.use_state(None)  # a source to download again
    pending_remove, set_pending_remove = solara.use_state(None)
    meta = app_state.catalogue_meta.value  # GLAD-L's band list, fetched by RemoteFactsLoader
    pending_cov = app_state.coverage_pending.value
    # a row's error belongs to its project and period (the Harmonize step's rule)
    scope = (app_state.project_loaded_signal.value, p.name if p else None, p.active_period if p else None)
    solara.use_effect(lambda: jobs.row_errors.set({}), [scope])
    errors = jobs.row_errors.value.get(scope, {})

    period = workflow_steps.active_period(p)
    if p is None or p.aoi is None or period is None:
        solara.Info(t("workflow.lock_alerts"))
        return

    pdir = paths.project_dir(p.name)
    ro = read_only(p)  # a review copy: no source changes (spec §6.3)
    rows = plan_prepare(p, pdir, period)
    by_source = {r.source: r for r in rows}
    step_scope = (p.name, period.id, "download")
    mine = mine_keys(running, jobs.scopes, *step_scope)
    stages = scope_stages(jobs.row_stage.value, p.name, period.id)

    def _start(items, force=False):
        start_items(jobs, app_state=app_state, layers=layers, map_=map_, notifications=notifications,
                    items=items, force=force, mode="download")

    def _add_predefined(key):
        """A catalogue source, or a project source not in this period, joins the active period."""
        def _apply(live):
            if key in live.sources:
                set_period_source(live, live.active_period, key, True)
            else:
                add_predefined_source(live, key, period_id=live.active_period)
            set_new_period_source(live, key, new_periods.value)

        try:
            update_setup(app_state, p.name, _apply, notifications)
        except ValueError as exc:  # the period's cap
            notifications.warning(error_text(exc))

    # The two custom callbacks run in SourceModal's validation task (on the
    # loop, never from an effect); what they raise the modal shows inline, so
    # they raise translated text, never a key. A vector source has no coverage
    # record (``cov`` None): ``record_for`` makes one.
    def _add_custom(fields, cov):
        def _apply(live):
            src = add_custom_source(live, **fields, period_id=live.active_period)
            set_new_period_source(live, src.name, new_periods.value)
            if cov is not None:
                live.coverage = {**live.coverage, src.name: cov}

        try:
            update_setup(app_state, p.name, _apply, notifications)
        except ValueError as exc:  # a name taken meanwhile, or the cap
            raise ValueError(error_text(exc)) from None

    def _refuse_save(key):
        notifications.warning(t(key))
        raise ValueError(t(key))

    def _save_custom(name, fields, cov):
        if is_writing(p.name):  # a job started while the modal was open (spec §5.1)
            _refuse_save("steps.alerts.edit_busy")
        changes = {k: v for k, v in fields.items() if k != "name"}

        def _apply(live):
            update_custom_source(live, name, **changes)  # refused once a set used it meanwhile
            if cov is not None:
                live.coverage = {**live.coverage, name: cov}
            elif name in live.coverage:  # now a vector source: the raster's record is moot
                live.coverage = {k: c for k, c in live.coverage.items() if k != name}

        try:
            saved = update_setup(app_state, p.name, _apply, notifications)
        except ValueError as exc:
            notifications.warning(error_text(exc))
            raise ValueError(error_text(exc)) from None
        if saved:
            layers.hide(f"src:{name}")  # its preview showed the previous definition

    def _edit(name):
        if refuse_setup(app_state, notifications):
            return
        if sets_using_source(p, name):
            notifications.warning(t("steps.alerts.edit_used"))
            return
        if is_writing(p.name):  # spec §5.1: edit and remove wait for the project's writers
            notifications.warning(t("steps.alerts.edit_busy"))
            return
        set_info_source(None)
        set_editing(name)
        modal_open.set(True)

    def _open_add():
        if refuse_setup(app_state, notifications):
            return
        set_editing(None)
        modal_open.set(True)

    removing = pending_remove if pending_remove in by_source else None  # the confirm goes with its row

    def _remove():
        name = removing
        set_pending_remove(None)
        # spec §5.1: wait for the project's writers, and for a job that claimed the row since the dialog opened
        if is_writing(p.name) or inflight_key(p.name, period.id, name) in jobs.inflight:
            notifications.warning(t("steps.alerts.delete_busy"))
            return
        layers.hide(f"src:{name}")  # its layer reads this period's files
        gone = []

        def _apply(live):
            gone.extend(cleanup.drop_artifacts(live, cleanup.source_period_keys(live, period.id, name)))
            set_period_source(live, period.id, name, False)

        if update_setup(app_state, p.name, _apply, notifications):
            cleanup.delete_files(pdir, gone)

    def _toggle(name):
        key = f"src:{name}"
        if layers.is_on(key):
            # Ruling #66: check is_on before building anything, and never hide
            # a build that is still in flight (ruling #10's toggle guard).
            if key in layers.inflight.value:
                return
            layers.hide(key)
            return
        reason = blocked_reason(p, name)
        if reason:  # the toggle is disabled; a stale click builds nothing either
            notifications.info(reason)
            return
        src = p.sources[name]
        row = by_source.get(name)
        if row is not None and row.ready:
            # spec §4.3: the harmonized raster under the preview's key, named by its period
            harm = p.artifacts[harm_key(period.id, name)]
            path = paths.resolve(pdir, harm.rel_path)
            layers.show(key, source_layers.harmonized_builder(map_, path, key,
                                                              source_layers.harmonized_name(name, period)),
                        content_id=harm.content_id, notifier=notifications,
                        legend=source_layers.harmonized_legend(key, name, period))
            return
        if is_file_source(src):
            notifications.info(t("steps.alerts.file_preview_after_harmonize"))
            return
        glad_l = meta.get("GLAD-L")
        if src.kind == "predefined" and src.catalogue_key == "GLAD-L" and glad_l is None:
            notifications.info(t("steps.alerts.meta_loading"))
            return
        a, b = preview_window(p, name)
        try:
            aoi = aoi_io.ee_aoi(app_state.aoi_result.value, pdir)
        except ValueError as exc:
            notifications.error(error_text(exc))
            return

        def build():
            if src.kind == "predefined":
                img = catalogue.build_image(src.catalogue_key, a, b, aoi, glad_l or {})
            else:
                img = catalogue.build_custom_image(src, a, b, aoi)
            map_.add_ee_layer(img.select("alert"), raster_map.ALERT_EE_VIS, name=source_layers.preview_name(name),
                              key=key)

        # show, not toggle: a click behind a superseded build must be queued,
        # not dropped (controller drift notes).
        layers.show(key, build, notifier=notifications, legend=source_layers.preview_legend(key, name, a, b))

    shown = info_source if info_source in by_source else None  # the dialog goes with its row
    table_rows = alert_rows(p, pdir, period, rows=rows, mine=mine, errors=errors, stages=stages,
                            progress=jobs.row_progress.value, on=on, building=building, locked=ro,
                            writing=is_writing(p.name), run=lambda s: _start([("source", s)]),
                            toggle=_toggle, on_info=lambda s: set_info_source(None if s == shown else s),
                            on_refresh=set_pending_refresh, on_remove=set_pending_remove, info_open=shown,
                            pending_cov=pending_cov, held=set(running))

    details = None
    if shown is not None:
        lead = next(r for r in table_rows if r["key"] == shown)["cells"][1]["label"]
        details = source_details(p, period, shown, by_source[shown], source_reason(p, period, shown), lead)
        if by_source[shown].raw is Status.CURRENT:
            details["why"] = None  # what is left (harmonize) is Prepare's business, not this step's

    def _set_new_periods(on_):
        update_setup(app_state, p.name, lambda live: set_new_period_source(live, shown, on_), notifications)

    refreshing = pending_refresh if pending_refresh in by_source else None  # a confirm goes with its row

    def _confirm_refresh():
        set_pending_refresh(None)
        _start([("source", refreshing)], force=True)

    refresh_title, refresh_message = refresh_confirm(p, period, refreshing) if refreshing else ("", "")
    used = [s for s in sets_using_source(p, removing) if p.alert_sets[s].period_id == period.id] if removing else []
    start, end = period.search_window()[0], period.end

    with solara.Column(style="gap:16px;"):
        solara.Text(tn("steps.alerts.period_box", period.look_back_days, start=period.start.isoformat(),
                       end=end.isoformat(), window=start.isoformat()),
                    style="padding: 8px 12px; border: 1px solid rgba(128,128,128,0.4); border-radius: 4px;"
                          "font-size: 13px;")
        # spec D4: a step list's Add button sits above it, full width
        solara.Button(t("steps.alerts.add"), icon_name="mdi-plus", color="primary", small=True, block=True,
                      disabled=ro, on_click=_open_add)
        ProductTable(title=t("steps.alerts.list_title"), columns=source_columns(), rows=table_rows,
                     empty_text=t("steps.alerts.empty"))
        # spec §6.2 "Failure": the sources left without a record can be checked again
        # (a GeoTIFF's download never waits for the AOI sidecar: only Harmonize offers its Check again)
        CheckAgainButton(app_state=app_state, show=(
            bool(app_state.coverage_error.value) and any(record_for(p, n) is None for n in period_sources(p, period))))

    SourceModal(open_=modal_open, app_state=app_state, gee_interface=gee_interface, editing=editing,
                on_add_predefined=_add_predefined, on_add_custom=_add_custom, on_save_custom=_save_custom,
                new_periods=new_periods)
    DetailsDialog(open=shown is not None, details=details, on_close=lambda: set_info_source(None),
                  new_periods=(shown in p.new_period_sources) if shown else None,
                  on_new_periods=None if ro else _set_new_periods,
                  on_edit=(lambda: _edit(shown)) if shown and p.sources[shown].kind == "custom" and not ro else None)
    ConfirmDialog(open=refreshing is not None, on_cancel=lambda: set_pending_refresh(None),
                  on_confirm=_confirm_refresh, title=refresh_title, message=refresh_message,
                  confirm_label=t("steps.prepare.redownload_label"), confirm_color="primary")
    ConfirmDialog(open=removing is not None, on_cancel=lambda: set_pending_remove(None), on_confirm=_remove,
                  title=t("steps.alerts.remove_from_period_title", name=source_label(p.sources[removing]))
                  if removing else "",
                  message=t("steps.alerts.remove_from_period_message"),
                  note=tn("steps.alerts.delete_used", len(used)) if used else None,
                  confirm_label=t("steps.alerts.action_remove"))
