"""The Masks card of Download & harmonize (spec §7.3, §5.1, "Masks card").

One row per project mask, in the sources table's grammar: its name over a
subtitle (origin and size, or why it needs a run), its choice in the active
period (one Mode select: off, keep or exclude), one Status cell holding its
state or the verb of its next run, and map · details · ⋮ (Download again,
Edit, Delete mask…). Download again asks first. A switch only edits the
project: it replaces the period's ``masks`` dict and starts no job. Editing is refused once a set used the mask; editing and
deleting wait for the project's writers (when the modal would open, and again
at Save: a job can start while it is open), mirroring the Alerts step's
custom sources (Task 18).
"""

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.notifications import use_notifications

from deforestation_alerts.engine.fingerprint import mask_key, mask_raw_key
from deforestation_alerts.engine.masks import build_mask_image
from deforestation_alerts.engine.why import mask_reason
from deforestation_alerts.errors import ProjectRuleError
from deforestation_alerts.project import cleanup, paths
from deforestation_alerts.project.models import (
    add_mask, remove_mask, set_period_mask, sets_using_mask, update_mask,
)
from deforestation_alerts.project.review_copy import read_only
from gui.i18n import t, tn
from gui.scripts import aoi_io, raster_map, source_layers
from gui.scripts.error_text import error_text
from gui.scripts.prepare_jobs import progress_key
from gui.scripts.prepare_view import mask_details, mask_state, mask_status_label, mask_subtitle
from gui.scripts.setup_guard import refuse_setup, update_setup
from gui.store.project_writers import is_writing
from gui.widget.confirm_dialog import ConfirmDialog
from gui.widget.details_dialog import DetailsDialog
from gui.widget.mask_modal import MaskModal
from gui.widget.product_table import ProductTable
from gui.widget.text_style import MUTED

MASK_ACTIONS = 3  # map, details, the ⋮ menu (Download again, Edit, Delete mask…)


def mask_columns() -> list:
    """Mask | Mode | Status (spec "Masks card"), measured at ``CARD_CONTENT_PX``."""
    return [{"label": t("steps.prepare.col_mask")},
            {"label": t("steps.prepare.col_mode"), "width": "78px"},
            {"label": t("steps.prepare.col_status"), "width": "104px"}]


def switch_mask(app_state, project_name, period_id, name, *, on=None, mode=None) -> bool:
    """A switch only edits the project (spec §7.3): no job starts."""
    return update_setup(app_state, project_name, lambda live: set_period_mask(live, period_id, name, on=on,
                                                                               mode=mode))


def mask_table_rows(p, pdir, period, rows, *, running, errors, on, building, run, rerun, toggle, info, edit, delete,
                    switch, refusals=0, locked=False, progress=None, stages=None, info_open=None,
                    stopped=frozenset()) -> list:
    """The ``ProductTable`` rows of the Masks card (spec "Masks card"); pure.

    ``running``: every claimed in-flight key (a mask row is busy whichever
    period's job claimed it); ``errors``: the step's row errors (a mask's is
    keyed ``@mask:<name>``); ``on``/``building``: the map keys shown and being
    built; the callbacks take the mask name (``switch`` also ``on=``/``mode=``):
    ``run`` is the Status pill's, ``rerun`` the menu's Download again (the card
    asks first). ``locked``: a review copy (spec §6.3): no pill, and the
    menu's items and the Mode select are disabled; the map toggle stays for a
    ready local raster. ``progress``/``stages``: the downloads' percents and
    the running job's stage (``download`` / ``align``), by
    ``prepare_jobs.progress_key``: a busy mask without a stage is queued.
    ``info_open``: the mask whose Details dialog is open. ``stopped``: the
    in-flight keys of the jobs Stop reached: a mask they hold waiting for its
    turn will not run, so it reads its state again (its pill disabled).

    As the sources table: the name's subtitle says what the mask holds or why
    it needs a run (``prepare_view.mask_subtitle``), and only the warning and
    error notes stay as lines under the row.
    """
    from gui.tile.prepare_step import _art, mask_inflight_key  # prepare_step renders this card

    out = []
    for r in rows:
        busy = mask_inflight_key(p.name, r.name) in running
        pkey = progress_key(p.name, period.id, "mask", r.name)
        stage = (stages or {}).get(pkey) if busy else None
        pct = (progress or {}).get(pkey) if busy else None
        queued = busy and stage is None and mask_inflight_key(p.name, r.name) not in stopped
        raw_art, aligned_art = _art(p, mask_raw_key(r.name)), _art(p, mask_key(r.name))
        reason = mask_reason(p, r.name) if not (r.ready or r.file_missing) else None
        subtitle, tone = mask_subtitle(r, raw_art=raw_art, aligned_art=aligned_art, reason=reason, stage=stage,
                                       queued=queued, pct=pct)
        title = {"type": "title", "value": r.name, "subtitle": subtitle}
        if tone:
            title["subtitle_tone"] = tone
        state = mask_state(r, stage=stage, queued=queued, pct=pct, failed=f"@mask:{r.name}" in errors,
                           locked=locked, on_run=(lambda n=r.name: run(n)))
        if busy and state["type"] == "verb":  # a stopped job still holds the mask
            state["disabled"] = True
        notes = []
        if r.file_missing:
            notes.append({"text": t("steps.prepare.file_missing", path=r.file_missing), "tone": "error"})
        if r.on and r.mode == "keep" and r.covers_none:
            notes.append({"text": t("steps.prepare.mask_covers_none_line"), "tone": "warning"})
        items = []
        if r.origin == "gee":
            items.append({"label": t("steps.prepare.action_download_again"), "icon": "mdi-refresh",
                          "disabled": locked or busy, "on_click": (lambda n=r.name: rerun(n))})
        items += [
            {"label": t("widgets.product_table.action_edit"), "icon": "mdi-pencil-outline", "disabled": locked,
             "on_click": (lambda n=r.name: edit(n))},
            {"label": t("steps.prepare.mask_delete_action"), "icon": "mdi-delete-outline", "color": "error",
             "disabled": locked, "on_click": (lambda n=r.name: delete(n))},
        ]
        key = f"mask:{r.name}"
        actions = [
            # a GeoTIFF mask is shown once aligned; a layer already on can always be switched off.
            # A copy shows an EE mask only from its local raster: its Earth Engine
            # preview would send the creator's reference (spec §6.3).
            {"kind": "map_toggle", "is_on": key in on, "loading": key in building,
             "disabled": (r.origin == "file" or locked) and not r.ready and key not in on,
             "on_click": (lambda n=r.name: toggle(n))},
            {"kind": "info", "is_on": info_open == r.name, "on_click": (lambda n=r.name: info(n))},
            {"kind": "menu", "items": items},
        ]

        def _choose(v, n=r.name):
            if v == "off":
                switch(n, on=False)  # the mode is kept for when it is on again
            elif v:
                switch(n, on=True, mode=v)

        # One select for the old On switch and Mode select (spec D4). Keyed by
        # the refusals: a refused switch leaves the props unchanged, so only a
        # remount puts the user's choice back to what the project holds.
        def _mode_cell(n=r.name, value=r.mode if r.on else "off"):
            rv.Select(v_model=value, on_v_model=_choose, dense=True, hide_details=True, disabled=locked,
                      items=[{"text": t("steps.prepare.mask_mode_off"), "value": "off"},
                             {"text": t("steps.prepare.mask_mode_keep"), "value": "keep"},
                             {"text": t("steps.prepare.mask_mode_exclude"), "value": "exclude"}],
                      ).key(f"mask-mode:{n}:{refusals}")

        out.append({
            "key": r.name,
            "cells": [title, {"type": "render", "fn": _mode_cell}, state],
            "notes": notes,
            "error": errors.get(f"@mask:{r.name}"),
            "actions": actions,
        })
    return out


@solara.component
def MasksCard(app_state, map_, gee_interface, layers, period, rows, running, errors, on_run, on_rerun,
              progress=None, stages=None, stopped=frozenset()):
    from gui.tile.prepare_step import mask_inflight_key

    notifications = use_notifications()
    p = app_state.project.value
    modal_open = solara.use_reactive(False)
    editing, set_editing = solara.use_state(None)
    pending_delete, set_pending_delete = solara.use_state(None)
    pending_rerun, set_pending_rerun = solara.use_state(None)  # the Earth Engine mask to download again
    info_name, set_info_name = solara.use_state(None)  # the mask whose Details dialog is open
    on, building = layers.on.value, layers.inflight.value  # subscribe: the toggles follow the registry
    # The claimed keys as of the latest render: a Save made from the modal's
    # task reads them then, not as they were when the modal opened.
    claimed = solara.use_ref(frozenset())
    claimed.current = frozenset(running)
    # Bumped by every refused switch (the remount key of the row's cells).
    refusals, set_refusals = solara.use_state(0)
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
        else:
            set_refusals(lambda n: n + 1)

    # The two modal callbacks run in MaskModal's validation task (on the loop,
    # never from an effect); what they raise the modal shows inline, so they
    # raise translated text, never a key.
    def _add(fields, mode):
        def _apply(live):
            if period.id not in live.periods:
                raise ProjectRuleError(key="steps.prepare.mask_err_changed")
            add_mask(live, **fields, mode=mode, period_id=period.id)

        try:
            added = update_setup(app_state, p.name, _apply, notifications)
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
            saved = update_setup(app_state, p.name, _apply, notifications)
        except ValueError as exc:
            refused = error_text(exc)
        if refused is not None:
            _refuse_save(refused)
        if not saved:  # the project was closed or switched
            raise ValueError(t("steps.prepare.mask_err_changed"))
        layers.hide(f"mask:{name}")  # its layer showed the previous definition

    def _edit(name):
        if refuse_setup(app_state, notifications):
            return
        if sets_using_mask(p, name):
            notifications.warning(t("steps.prepare.mask_edit_used"))
            return
        if _busy(name):
            notifications.warning(t("steps.prepare.mask_edit_busy"))
            return
        set_editing(name)
        modal_open.set(True)

    def _open_add():
        if refuse_setup(app_state, notifications):
            return
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

        if update_setup(app_state, p.name, _apply, notifications):
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
            if refuse_setup(app_state, notifications):  # a copy sends no creator reference (spec §6.3)
                return
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

    by_name = {r.name: r for r in rows}
    shown = info_name if info_name in by_name else None  # the dialog goes with its row
    rerunning = pending_rerun if pending_rerun in by_name else None  # and the confirm

    def _toggle_info(name):
        set_info_name(None if name == shown else name)

    def _confirm_rerun():
        set_pending_rerun(None)
        on_rerun(rerunning)

    details = None
    if shown is not None:
        row = by_name[shown]
        shown_busy = mask_inflight_key(p.name, shown) in running
        shown_key = progress_key(p.name, period.id, "mask", shown)
        shown_stage = (stages or {}).get(shown_key) if shown_busy else None
        lead = mask_status_label(row, stage=shown_stage, queued=(shown_busy and shown_stage is None
                                                                  and mask_inflight_key(p.name, shown) not in stopped),
                                 pct=(progress or {}).get(shown_key) if shown_busy else None)
        details = mask_details(p, shown, row, mask_reason(p, shown), lead)

    table_rows = mask_table_rows(p, pdir, period, rows, running=running, errors=errors, on=on, building=building,
                                 run=on_run, rerun=set_pending_rerun, toggle=_toggle, info=_toggle_info, edit=_edit,
                                 delete=set_pending_delete, switch=_switch, refusals=refusals,
                                 locked=read_only(p), progress=progress, stages=stages, info_open=shown,
                                 stopped=stopped)
    with rv.Card(outlined=True, class_="pa-3"):
        # spec D4: a nested list's Add button sits above it, outlined and full width
        solara.Button(t("steps.prepare.mask_add"), icon_name="mdi-plus", color="primary", small=True, outlined=True,
                      block=True, disabled=read_only(p), on_click=_open_add)
        ProductTable(title=t("steps.prepare.masks_title"), columns=mask_columns(), rows=table_rows,
                     empty_text=t("steps.prepare.masks_empty"))
        solara.Text(t("steps.prepare.masks_note"), style=MUTED + "font-size:0.78rem;")
    MaskModal(open_=modal_open, app_state=app_state, gee_interface=gee_interface, on_add=_add, on_save=_save,
              editing=editing)
    DetailsDialog(open=shown is not None, details=details, on_close=lambda: set_info_name(None))
    ConfirmDialog(open=rerunning is not None, on_cancel=lambda: set_pending_rerun(None), on_confirm=_confirm_rerun,
                  title=t("steps.prepare.mask_redownload_title", name=rerunning or ""),
                  message=t("steps.prepare.mask_redownload_message"),
                  confirm_label=t("steps.prepare.redownload_label"), confirm_color="primary")
    used = len(sets_using_mask(p, pending_delete)) if pending_delete else 0
    ConfirmDialog(open=pending_delete is not None, on_cancel=lambda: set_pending_delete(None), on_confirm=_delete,
                  title=t("steps.prepare.mask_delete_title", name=pending_delete or ""),
                  message=tn("steps.prepare.mask_delete_used", used) if used
                  else t("steps.prepare.mask_delete_message"),
                  confirm_label=t("common.delete"))
