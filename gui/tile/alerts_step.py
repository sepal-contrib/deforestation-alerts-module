"""Alerts step (spec §6.2): the alert sources, their coverage of the AOI, previews."""

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import NO_DATA, NOT_COVERED, record_for, window_coverage
from deforestation_alerts.engine.fingerprint import harm_key, is_file_source
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import (
    add_custom_source, add_predefined_source, remove_source, sets_using_source, update_custom_source,
)
from gui.i18n import t, tn
from gui.scripts import aoi_io, catalogue_meta, raster_map, source_layers
from gui.scripts.coverage_ui import CheckAgainButton, chip_cell, dates_text, note_text
from gui.scripts.error_text import error_text
from gui.store import workflow_steps
from gui.store.project_writers import is_writing
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.source_modal import SourceModal


def blocked_reason(p, name):
    """Why ``name`` cannot be previewed for the active window, or None."""
    cov = record_for(p, name)
    if cov is None:
        return t("coverage.unchecked")
    a, b = catalogue_meta.preview_window(p, name)
    state, notes = window_coverage(cov, name, a, b)
    return note_text(notes[0]) if state in (NOT_COVERED, NO_DATA) else None


@solara.component
def AlertsStep(app_state, map_, gee_interface, layers):
    notifications = use_notifications()
    p = app_state.project.value
    on = layers.on.value  # subscribe: the map-toggle icons follow the registry
    building = layers.inflight.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    meta = app_state.catalogue_meta.value  # GLAD-L's band list, fetched by RemoteFactsLoader
    pending_cov = app_state.coverage_pending.value

    if p is None or p.aoi is None:
        solara.Info(t("workflow.lock_alerts"))
        return

    pdir = paths.project_dir(p.name)
    period = workflow_steps.active_period(p)
    # Ruling #46: the harmonized status alone misses a stale raw row (e.g.
    # after a grid change); a row is a ready preview only once ``.ready`` says
    # both raw and harmonized are current.
    rows_by_source = {r.source: r for r in plan_prepare(p, pdir, period)} if period is not None else {}

    def _add_predefined(key):
        app_state.update_project(p.name, lambda live: add_predefined_source(live, key))

    # The two custom callbacks run in SourceModal's validation task (on the
    # loop, never from an effect); what they raise the modal shows inline, so
    # they raise translated text, never a key.
    def _add_custom(fields, cov):
        def _apply(live):
            src = add_custom_source(live, **fields)
            live.coverage = {**live.coverage, src.name: cov}

        try:
            app_state.update_project(p.name, _apply)
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
            live.coverage = {**live.coverage, name: cov}

        try:
            saved = app_state.update_project(p.name, _apply)
        except ValueError as exc:
            notifications.warning(error_text(exc))
            raise ValueError(error_text(exc)) from None
        if saved:
            layers.hide(f"src:{name}")  # its preview showed the previous definition

    def _edit(name):
        if sets_using_source(p, name):
            notifications.warning(t("steps.alerts.edit_used"))
            return
        if is_writing(p.name):  # spec §5.1: edit and delete wait for the project's writers
            notifications.warning(t("steps.alerts.edit_busy"))
            return
        set_editing(name)
        modal_open.set(True)

    def _open_add():
        set_editing(None)
        modal_open.set(True)

    def _delete():
        name = pending_delete
        set_pending_delete(None)
        if is_writing(p.name):
            notifications.warning(t("steps.alerts.delete_busy"))
            return
        layers.hide(f"src:{name}")
        gone = []

        def _apply(live):
            gone.extend(cleanup.drop_artifacts(live, cleanup.source_artifact_keys(live, name)))
            remove_source(live, name)

        if app_state.update_project(p.name, _apply):
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
        row = rows_by_source.get(name)
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
        a, b = catalogue_meta.preview_window(p, name)
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

    rows = []
    for name, src in sorted(p.sources.items(), key=lambda kv: kv[1].digit):
        cov = record_for(p, name)
        reason = blocked_reason(p, name)
        notes = []
        if src.kind == "predefined" and src.catalogue_key == "RADD":
            notes.append({"text": t("coverage.radd_note"), "tone": "info"})
        if reason:
            notes.append({"text": reason, "tone": "error"})
        if src.kind == "predefined":
            label, kind = t(catalogue.LABEL_KEYS[src.catalogue_key]), t("steps.alerts.kind_predefined")
        else:
            label, kind = name, t("steps.alerts.kind_custom", format=t(f"steps.alerts.format_{src.format}"))
        key = f"src:{name}"
        actions = [{"kind": "map_toggle", "is_on": key in on, "loading": key in building,
                    "disabled": reason is not None, "on_click": (lambda n=name: _toggle(n))}]
        if src.kind == "custom":
            actions.append({"kind": "edit", "on_click": (lambda n=name: _edit(n))})
        actions.append({"kind": "delete", "on_click": (lambda n=name: set_pending_delete(n))})
        rows.append({"key": name, "notes": notes, "actions": actions,
                     "cells": [{"value": label}, {"type": "chip", "value": kind}, chip_cell(cov, pending_cov),
                               {"value": dates_text(cov), "muted": True}]})

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.alerts.title')}")
        StepIntro(t("steps.alerts.description"), t("steps.alerts.info_header"), t("steps.alerts.info_md"))
        ProductTable(
            title=t("steps.alerts.list_title"),
            columns=[{"label": t("steps.alerts.col_source")},
                     {"label": t("steps.alerts.col_kind"), "width": "120px"},
                     {"label": t("steps.alerts.col_coverage"), "width": "150px"},
                     {"label": t("steps.alerts.col_dates"), "width": "150px"}],
            rows=rows, empty_text=t("steps.alerts.empty"),
        )
        # spec §6.2 "Failure": the sources left without a record can be checked again
        CheckAgainButton(app_state=app_state, show=bool(app_state.coverage_error.value)
                         and any(record_for(p, n) is None for n in p.sources))
        solara.Button(t("steps.alerts.add"), icon_name="mdi-plus", color="primary", small=True, on_click=_open_add)

    SourceModal(open_=modal_open, app_state=app_state, gee_interface=gee_interface, editing=editing,
                on_add_predefined=_add_predefined, on_add_custom=_add_custom, on_save_custom=_save_custom)
    used = len(sets_using_source(p, pending_delete)) if pending_delete else 0
    ConfirmDialog(
        open=pending_delete is not None,
        on_cancel=lambda: set_pending_delete(None),
        on_confirm=_delete,
        title=t("steps.alerts.delete_title", name=pending_delete or ""),
        message=tn("steps.alerts.delete_used", used) if used else t("steps.alerts.delete_message"),
        confirm_label=t("common.delete"),
    )
