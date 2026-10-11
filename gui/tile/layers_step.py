"""Optional layers step (spec §6.3): the project's visualization layers (an
Earth Engine image, a GeoTIFF, Planet NICFI), each one shown on the shared
map with its legend (§9.4) under ``vis:<name>``."""

from datetime import date

import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.project.review_copy import read_only
from gui.i18n import t
from gui.scripts import legend_specs, vis_layers
from gui.scripts.setup_guard import refuse_setup, update_setup
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.help import StepIntro
from gui.widget.product_table import ProductTable
from gui.widget.vis_layer_modal import VisLayerModal

LAYER_ACTIONS = 3  # map, edit, delete


def _key(name: str) -> str:
    return f"vis:{name}"


def layer_columns() -> list:
    """Name only (spec D4): the kind and "in Analysis" are chips, the details a note."""
    return [{"label": t("steps.layers.col_name")}]


def layer_cell(layer) -> dict:
    """The name with its kind chip, and an "in Analysis" chip when it is shown there."""
    chips = [{"value": t(f"steps.layers.kind_{layer.kind}")}]
    if layer.show_in_analysis:
        chips.append({"value": t("steps.layers.in_analysis"), "color": "primary"})
    return {"value": layer.name, "chips": chips}


@solara.component
def LayersStep(app_state, map_, gee_interface, layers):
    notifications = use_notifications()
    p = app_state.project.value
    on = layers.on.value  # subscribe: the map-toggle icons follow the registry
    building = layers.inflight.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    # A NICFI layer on the map without a month of its own draws the active
    # period's last month: a period switch (or a new end) redraws it and its
    # legend, the way mask legends follow the active period (§7.3). Keyed on
    # that month only, so a worker publishing the project never runs it.
    month = vis_layers.period_month(p, date.today()) if p is not None else None

    def _follow_period():
        live = app_state.project.value
        if live is not None:
            vis_layers.refresh_nicfi_layers(layers, map_, live, today=date.today(), notifier=notifications)

    solara.use_effect(_follow_period, [p.name if p is not None else None, month])
    if p is None or p.aoi is None:
        solara.Info(t("workflow.lock_layers"))
        return

    # Runs in VisLayerModal's validation task (on the loop, never from an
    # effect); what it raises the modal shows inline, so it raises translated text.
    def _save(layer, previous):
        def _apply(live):
            error = vis_layers.validate_name(layer.name, live, current=previous)
            if error:  # a layer of that name added meanwhile
                raise ValueError(t(error))
            kept = {k: v for k, v in live.vis_layers.items() if k != previous}
            live.vis_layers = {**kept, layer.name: layer}

        if update_setup(app_state, p.name, _apply, notifications) and previous and layers.is_on(_key(previous)):
            layers.hide(_key(previous))  # it showed the previous definition

    def _delete():
        name = pending_delete
        set_pending_delete(None)
        if refuse_setup(app_state, notifications):
            return  # a review copy keeps its layers, shown or not
        layers.hide(_key(name))
        update_setup(app_state, p.name, lambda live: setattr(
            live, "vis_layers", {k: v for k, v in live.vis_layers.items() if k != name}), notifications)

    def _toggle(name):
        key = _key(name)
        if layers.is_on(key):
            if key not in layers.inflight.value:  # never hide a build still in flight (ruling #10)
                layers.hide(key)
            return
        layer = p.vis_layers[name]
        shown_month = vis_layers.nicfi_month(layer, p, date.today()) if layer.kind == "nicfi" else None
        # NICFI: the month is the content id, so a period switch redraws only a changed month
        layers.show(key, vis_layers.builder(map_, layer, key, project=p, aoi_bounds=p.aoi.bounds),
                    content_id=shown_month, notifier=notifications,
                    legend=legend_specs.vis_legend(key, layer, month=shown_month))

    def _edit(name):
        if refuse_setup(app_state, notifications):
            return
        set_editing(name)
        modal_open.set(True)

    def _open_add():
        if refuse_setup(app_state, notifications):
            return
        set_editing(None)
        modal_open.set(True)

    ro = read_only(p)  # a review copy: its layers are shown, never changed (spec §6.3)

    rows = []
    for name, layer in sorted(p.vis_layers.items(), key=lambda kv: kv[0].casefold()):
        key = _key(name)
        rows.append({"key": name, "cells": [layer_cell(layer)],
                     "notes": [{"text": vis_layers.vis_layers_summary(layer), "tone": None}],
                     "actions": [
                         {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
                          "on_click": (lambda n=name: _toggle(n))},
                         {"kind": "edit", "disabled": ro, "on_click": (lambda n=name: _edit(n))},
                         {"kind": "delete", "disabled": ro, "on_click": (lambda n=name: set_pending_delete(n))},
                     ]})

    with solara.Column(style="gap:16px;"):
        solara.Markdown(f"### {t('steps.layers.title')}")
        StepIntro(t("steps.layers.description"), t("steps.layers.info_header"), t("steps.layers.info_md"))
        solara.Button(t("steps.layers.add"), icon_name="mdi-plus", color="primary", small=True, block=True,
                      disabled=ro, on_click=_open_add)
        ProductTable(title=t("steps.layers.list_title"), columns=layer_columns(), rows=rows,
                     empty_text=t("steps.layers.empty"))

    VisLayerModal(open_=modal_open, app_state=app_state, gee_interface=gee_interface, on_save=_save, editing=editing)
    ConfirmDialog(
        open=pending_delete is not None,
        on_cancel=lambda: set_pending_delete(None),
        on_confirm=_delete,
        title=t("steps.layers.delete_title", name=pending_delete or ""),
        message=t("steps.layers.delete_message"),
        confirm_label=t("common.delete"),
    )
