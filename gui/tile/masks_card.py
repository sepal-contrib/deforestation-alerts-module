"""The Masks card of Download & harmonize (spec §7.3, §5.1).

One row per project mask, with its choice in the active period (on, keep or
exclude), its download and alignment status, its size and its actions. A
switch only edits the project: it replaces the period's ``masks`` dict and
starts no job. Editing is refused once a set used the mask; editing and
deleting wait for the project's writers (when the modal would open, and again
at Save: a job can start while it is open), mirroring the Alerts step's
custom sources (Task 18).
"""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import mask_key, mask_raw_key
from deforestation_alerts.engine.masks import build_mask_image
from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import (
    add_mask, remove_mask, set_period_mask, sets_using_mask, update_mask,
)
from gui.i18n import t, tn
from gui.scripts import aoi_io, raster_map, source_layers
from gui.scripts.error_text import error_text
from gui.scripts.prepare_jobs import _CELL_STATUS
from gui.store.project_writers import is_writing
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.mask_modal import MaskModal
from gui.widget.product_table import ProductTable


def switch_mask(app_state, project_name, period_id, name, *, on=None, mode=None) -> bool:
    """A switch only edits the project (spec §7.3): no job starts."""
    return app_state.update_project(project_name, lambda live: set_period_mask(live, period_id, name, on=on,
                                                                                mode=mode))


def _status(s, busy):
    return {"type": "status", "status": "running" if busy else _CELL_STATUS[s]}


def mask_table_rows(p, pdir, period, rows, *, running, errors, on, building, run, rerun, toggle, edit, delete,
                    switch) -> list:
    """The ``ProductTable`` rows of the Masks card (spec §7.3); pure.

    ``running``: every claimed in-flight key (a mask row is busy whichever
    period's job claimed it); ``errors``: the step's row errors (a mask's is
    keyed ``@mask:<name>``); ``on``/``building``: the map keys shown and being
    built; the callbacks take the mask name (``switch`` also ``on=``/``mode=``).
    """
    from gui.tile.prepare_step import mask_inflight_key, size_text  # prepare_step renders this card

    out = []
    for r in rows:
        busy = mask_inflight_key(p.name, r.name) in running
        size = sum(a.size_bytes for k in (mask_raw_key(r.name), mask_key(r.name))
                   if (a := p.artifacts.get(k)) is not None)
        notes = []
        if r.file_missing:
            notes.append({"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"})
        if r.on and r.mode == "keep" and r.covers_none:
            notes.append({"text": t("steps.prepare.mask_covers_none_line"), "tone": "warning"})
        key = f"mask:{r.name}"
        actions = [{"kind": "download", "disabled": busy or r.file_missing is not None, "loading": busy,
                    "on_click": (lambda n=r.name: run(n))}]
        if r.origin == "gee":
            actions.append({"kind": "redownload", "disabled": busy, "on_click": (lambda n=r.name: rerun(n))})
        actions += [
            # a GeoTIFF mask is shown once aligned; a layer already on can always be switched off
            {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
             "disabled": r.origin == "file" and not r.ready and key not in on,
             "on_click": (lambda n=r.name: toggle(n))},
            {"kind": "edit", "on_click": (lambda n=r.name: edit(n))},
            {"kind": "delete", "on_click": (lambda n=r.name: delete(n))},
        ]

        def _switch_cell(n=r.name, value=r.on):
            rv.Switch(v_model=value, on_v_model=lambda v: switch(n, on=bool(v)), dense=True, hide_details=True,
                      class_="ma-0")

        def _mode_cell(n=r.name, value=r.mode, enabled=r.on):
            rv.Select(v_model=value, on_v_model=lambda v: switch(n, mode=v), dense=True, hide_details=True,
                      disabled=not enabled,
                      items=[{"text": t("steps.prepare.mask_mode_keep"), "value": "keep"},
                             {"text": t("steps.prepare.mask_mode_exclude"), "value": "exclude"}])

        out.append({
            "key": r.name,
            "cells": [
                {"value": r.name},
                {"type": "chip", "value": t(f"steps.prepare.mask_origin_{r.origin}")},
                {"type": "render", "fn": _switch_cell},
                {"type": "render", "fn": _mode_cell},
                _status(r.raw, busy) if r.raw is not None else {"value": "—", "muted": True},
                _status(r.aligned, busy),
                {"value": size_text(size), "muted": True},
            ],
            "notes": notes,
            "error": errors.get(f"@mask:{r.name}"),
            "actions": actions,
        })
    return out


@solara.component
def MasksCard(app_state, map_, gee_interface, layers, period, rows, running, errors, on_run, on_rerun):
    from gui.tile.prepare_step import mask_inflight_key

    notifications = use_notifications()
    p = app_state.project.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    on, building = layers.on.value, layers.inflight.value  # subscribe: the toggles follow the registry
    # The claimed keys as of the latest render: a Save made from the modal's
    # task reads them then, not as they were when the modal opened.
    claimed = solara.use_ref(frozenset())
    claimed.current = frozenset(running)
    if p is None:
        return  # closed: the card subscribes to the project, so it may render once before its step unmounts it
    pdir = paths.project_dir(p.name)

    def _busy(name) -> bool:
        """A job writes into the project, or one has claimed this mask and is
        about to (spec §5.1: edit and delete wait for the project's writers)."""
        return is_writing(p.name) or mask_inflight_key(p.name, name) in claimed.current

    def _switch(name, on=None, mode=None):
        if switch_mask(app_state, p.name, period.id, name, on=on, mode=mode):
            notifications.info(t("steps.prepare.masks_switched"))

    # The two modal callbacks run in MaskModal's validation task (on the loop,
    # never from an effect); what they raise the modal shows inline, so they
    # raise translated text, never a key.
    def _add(fields, mode):
        def _apply(live):
            if period.id not in live.periods:
                raise ProjectRuleError(key="steps.prepare.mask_err_changed")
            add_mask(live, **fields, mode=mode, period_id=period.id)

        try:
            added = app_state.update_project(p.name, _apply)
        except ValueError as exc:  # a name taken meanwhile
            raise ValueError(error_text(exc)) from None
        if not added:
            raise ValueError(t("steps.prepare.mask_err_changed"))

    def _refuse_save(text):
        notifications.warning(text)
        raise ValueError(text)

    def _save(name, changes):
        if _busy(name):  # a job started while the modal was open (spec §5.1)
            _refuse_save(t("steps.prepare.mask_edit_busy"))

        def _apply(live):
            if name not in live.masks:  # deleted meanwhile
                raise ProjectRuleError(key="steps.prepare.mask_err_changed")
            update_mask(live, name, **changes)  # refused once a set used it meanwhile

        refused = None
        try:
            saved = app_state.update_project(p.name, _apply)
        except ValueError as exc:
            refused = error_text(exc)
        if refused is not None:
            _refuse_save(refused)
        if not saved:  # the project was closed or switched
            raise ValueError(t("steps.prepare.mask_err_changed"))
        layers.hide(f"mask:{name}")  # its layer showed the previous definition

    def _edit(name):
        if sets_using_mask(p, name):
            notifications.warning(t("steps.prepare.mask_edit_used"))
            return
        if _busy(name):
            notifications.warning(t("steps.prepare.mask_edit_busy"))
            return
        set_editing(name)
        modal_open.set(True)

    def _open_add():
        set_editing(None)
        modal_open.set(True)

    def _delete():
        name = pending_delete
        set_pending_delete(None)
        if _busy(name):
            notifications.warning(t("steps.prepare.mask_delete_busy"))
            return
        layers.hide(f"mask:{name}")
        gone = []

        def _apply(live):
            gone.extend(cleanup.drop_artifacts(live, cleanup.mask_artifact_keys(live, name)))
            remove_mask(live, name)

        if app_state.update_project(p.name, _apply):
            cleanup.delete_files(pdir, gone)

    def _toggle(name):
        key = f"mask:{name}"
        if layers.is_on(key):
            if key not in layers.inflight.value:  # never hide a build still in flight
                layers.hide(key)
            return
        mask, art = p.masks.get(name), p.artifacts.get(mask_key(name))
        row = next((r for r in rows if r.name == name), None)
        if mask is None or row is None:
            return
        layer_name, content_id = source_layers.mask_name(name), None
        if row.ready and art is not None:
            # its content id: a re-run's refresh redraws the new file (spec §9.1)
            build = source_layers.mask_builder(map_, paths.resolve(pdir, art.rel_path), key, layer_name)
            content_id = art.content_id
        elif mask.origin == "gee":  # before alignment: Earth Engine tiles of its non-zero pixels
            try:
                aoi = aoi_io.ee_aoi(app_state.aoi_result.value, pdir)
            except ValueError as exc:
                notifications.error(error_text(exc))
                return

            def build():
                map_.add_ee_layer(build_mask_image(mask, aoi).selfMask(), raster_map.MASK_EE_VIS, name=layer_name,
                                  key=key, opacity=raster_map.MASK_OPACITY)
        else:
            return  # a GeoTIFF mask is shown once aligned (its toggle is disabled until then)
        # show, not toggle: a click behind a superseded build is queued, not dropped
        layers.show(key, build, content_id=content_id, notifier=notifications,
                    legend=source_layers.mask_layer_legend(p, name))

    table_rows = mask_table_rows(p, pdir, period, rows, running=running, errors=errors, on=on, building=building,
                                 run=on_run, rerun=on_rerun, toggle=_toggle, edit=_edit,
                                 delete=set_pending_delete, switch=_switch)
    with rv.Card(outlined=True, class_="pa-3"):
        ProductTable(
            title=t("steps.prepare.masks_title"),
            columns=[{"label": t("steps.prepare.col_mask")},
                     {"label": t("steps.prepare.col_origin"), "width": "90px"},
                     {"label": t("steps.prepare.col_on"), "width": "52px"},
                     {"label": t("steps.prepare.col_mode"), "width": "100px"},
                     {"label": t("steps.prepare.col_download"), "width": "90px"},
                     {"label": t("steps.prepare.col_align"), "width": "90px"},
                     {"label": t("steps.prepare.col_size"), "width": "70px"}],
            rows=table_rows, empty_text=t("steps.prepare.masks_empty"),
        )
        solara.Text(t("steps.prepare.masks_note"), classes=["text--secondary"], style="font-size:0.78rem;")
        solara.Button(t("steps.prepare.mask_add"), icon_name="mdi-plus", small=True, outlined=True,
                      on_click=_open_add)
    MaskModal(open_=modal_open, app_state=app_state, gee_interface=gee_interface, on_add=_add, on_save=_save,
              editing=editing)
    used = len(sets_using_mask(p, pending_delete)) if pending_delete else 0
    ConfirmDialog(open=pending_delete is not None, on_cancel=lambda: set_pending_delete(None), on_confirm=_delete,
                  title=t("steps.prepare.mask_delete_title", name=pending_delete or ""),
                  message=tn("steps.prepare.mask_delete_used", used) if used
                  else t("steps.prepare.mask_delete_message"),
                  confirm_label=t("common.delete"))
