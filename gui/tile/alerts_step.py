"""Alerts step (spec §6.2): the project's alert sources, previewed on the map."""

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.fingerprint import harm_key
from deforestation_alerts.engine.plan import plan_prepare
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import add_predefined_source, remove_source
from gui.i18n import t
from gui.scripts import aoi_io, catalogue_meta, raster_map
from gui.store import workflow_steps
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.product_table import ProductTable
from gui.widget.source_modal import SourceModal


def _sets_using(p, name) -> int:
    return sum(1 for s in p.alert_sets.values() if name in s.sources)


@solara.component
def AlertsStep(app_state, map_, gee_interface, layers):
    notifications = use_notifications()
    p = app_state.project.value
    on = layers.on.value  # subscribe: the map-toggle icons follow the registry
    modal_open = solara.use_reactive(False)
    pending_delete, set_pending_delete = solara.use_state(None)
    meta = catalogue_meta.use_catalogue_meta(app_state, gee_interface)

    if p is None or p.aoi is None:
        solara.Info(t("workflow.lock_alerts"))
        return

    pdir = paths.project_dir(p.name)
    period = workflow_steps.active_period(p)
    # Ruling #46: the harmonized status alone misses a stale raw row (e.g.
    # after a grid change); a row is a ready preview only once ``.ready`` says
    # both raw and harmonized are current.
    prepare_by_source = {r.source: r for r in plan_prepare(p, pdir, period)} if period is not None else {}

    def _add(key):
        app_state.update_project(p.name, lambda live: add_predefined_source(live, key))

    def _delete():
        name = pending_delete
        layers.hide(f"src:{name}")
        app_state.update_project(p.name, lambda live: remove_source(live, name))
        set_pending_delete(None)

    def _toggle(name):
        key = f"src:{name}"
        if layers.is_on(key):
            # Ruling #66: check is_on before building anything, and never hide
            # a build that is still in flight (ruling #10's toggle guard).
            if key in layers.inflight.value:
                return
            layers.hide(key)
            return
        src = p.sources[name]
        row = prepare_by_source.get(name)
        if row is not None and row.ready:
            harm = p.artifacts[harm_key(period.id, name)]
            path = paths.resolve(pdir, harm.rel_path)
            build = lambda: raster_map.add_raster_layer(map_, path, key=key, name=name)  # noqa: E731
        else:
            key_meta = meta.get(src.catalogue_key)
            if key_meta is None:
                notifications.info(t("steps.alerts.meta_loading"))
                return
            win = catalogue_meta.preview_window(p, key_meta, src.catalogue_key)
            try:
                aoi = aoi_io.ee_aoi(app_state.aoi_result.value, pdir)
            except ValueError as exc:
                notifications.error(str(exc))
                return

            def build():
                img = catalogue.build_image(src.catalogue_key, win[0], win[1], aoi, key_meta)
                map_.add_ee_layer(img.select("alert"), raster_map.ALERT_EE_VIS, name=name, key=key)

        # show, not toggle: a click behind a superseded build must be queued,
        # not dropped (controller drift notes).
        layers.show(key, build, notifier=notifications)

    rows = []
    for name, src in sorted(p.sources.items(), key=lambda kv: kv[1].digit):
        rows.append({
            "key": name,
            "cells": [
                {"value": t(catalogue.LABEL_KEYS[src.catalogue_key])},
                {"type": "chip", "value": t("steps.alerts.kind_predefined")},
                {"value": catalogue_meta.coverage_text(src.catalogue_key, meta.get(src.catalogue_key)),
                 "muted": True},
            ],
            "actions": [
                {"kind": "map_toggle", "is_on": f"src:{name}" in on,
                 "loading": f"src:{name}" in layers.inflight.value, "on_click": (lambda n=name: _toggle(n))},
                {"kind": "delete", "on_click": (lambda n=name: set_pending_delete(n))},
            ],
        })

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.alerts.title')}")
        solara.Text(t("steps.alerts.description"), classes=["text--secondary"])
        ProductTable(
            title=t("steps.alerts.list_title"),
            columns=[{"label": t("steps.alerts.col_source")},
                     {"label": t("steps.alerts.col_kind"), "width": "90px"},
                     {"label": t("steps.alerts.col_coverage"), "width": "170px"}],
            rows=rows, empty_text=t("steps.alerts.empty"),
        )
        solara.Button(t("steps.alerts.add"), icon_name="mdi-plus", color="primary", small=True,
                      on_click=lambda: modal_open.set(True))

    SourceModal(open_=modal_open, project=app_state.project, on_add=_add)
    used = _sets_using(p, pending_delete) if pending_delete else 0
    ConfirmDialog(
        open=pending_delete is not None,
        on_cancel=lambda: set_pending_delete(None),
        on_confirm=_delete,
        title=t("steps.alerts.delete_title", name=pending_delete or ""),
        message=t("steps.alerts.delete_used", n=used) if used else t("steps.alerts.delete_message"),
        confirm_label=t("common.delete"),
    )
