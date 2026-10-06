"""Add mask modal (spec §7.3): a name, an Earth Engine image or collection or a
GeoTIFF, one band, and the mode for the active period. Validated like a
custom source (§6.2) without a coverage share: a mask that misses the AOI
shows up as zero on-pixels after its alignment.

It follows ``source_modal``'s rules (Task 17), on the same form frame
(``gui/widget/form_dialog``): the Add/Save is a ``SubmitTaskButton`` over a
``use_task``; ``on_add``/``on_save`` (which call ``update_project``) run
inside that task, on the loop, never in its completion effect; closing the
dialog cancels a check still running; the fields are frozen while their
values are checked; a project, AOI or period change during the check refuses
inline. What ``on_add``/``on_save`` raise is shown inline, so they raise
translated text.
"""

import asyncio

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import use_task_button

from deforestation_alerts.engine.user_raster import (
    UserRasterError, check_discovery, check_file, discovery_request, file_native, native_from_discovery,
    read_header,
)
from deforestation_alerts.project.names import validate_mask_name
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.widget.form_dialog import SubmitTaskButton, field_group, form_card, use_form_dialog
from gui.widget.raster_input import RasterInput, absolute, band_index, use_band_list


async def validate_mask(gee_interface, project, *, name, origin, ref, asset_type, band, editing=None) -> dict:
    """Check a mask before it is added or saved: ``{"error": key}`` or
    ``{"fields": {name, origin, ref, asset_type, band, native}}``."""
    name = (name or "").strip()
    error = validate_mask_name(name, project, current=editing)
    if error:
        return {"error": error}
    if origin == "file":
        band = band_index(band)  # a GeoTIFF band is its 1-based index (a name is left from an asset)
    if not ref or band is None:
        return {"error": "steps.prepare.mask_err_incomplete"}
    if origin == "gee":
        if asset_type not in ("IMAGE", "IMAGE_COLLECTION"):
            return {"error": "steps.alerts.err_wrong_type"}  # before the probe
        band = str(band)
        info = await gee_interface.get_info_async(discovery_request(ref, asset_type, [band]))
        error = check_discovery(info, "mask", [band])
        if error:
            return {"error": error}
        native = native_from_discovery(info)
    else:
        try:
            header = await asyncio.to_thread(read_header, ref)
        except UserRasterError as exc:
            return {"error": exc.key}
        error = check_file(header, [band])
        if error:
            return {"error": error}
        native = file_native(header)
    return {"fields": dict(name=name, origin=origin, ref=ref, asset_type=asset_type, band=band, native=native)}


@solara.component
def MaskModal(open_, app_state, gee_interface, on_add, on_save, editing=None):
    # The form frame (gui/widget/form_dialog), 560px like every form that picks
    # a raster: persistent, an outside click nudges it, ESC closes it.
    with use_form_dialog(open_, "560px"):
        if open_.value:  # mounted per opening: a clean form, and no asset listing while closed
            _MaskForm(open_=open_, app_state=app_state, gee_interface=gee_interface, on_add=on_add,
                      on_save=on_save, editing=editing)


def _scope(p):
    """What the check must still match when it ends: the project, its AOI and
    its active period (a new mask is turned on in that period)."""
    if p is None:
        return None
    return p.name, p.aoi.digest if p.aoi is not None else None, p.active_period


@solara.component
def _MaskForm(open_, app_state, gee_interface, on_add, on_save, editing):
    p = app_state.project.value
    m0 = p.masks.get(editing) if p is not None and editing else None
    name, set_name = solara.use_state(m0.name if m0 else "")
    origin, set_origin = solara.use_state(m0.origin if m0 else "gee")
    asset = solara.use_reactive({"asset_id": m0.ref, "type": m0.asset_type, "column": "ALL", "value": None}
                                if m0 is not None and m0.origin == "gee" else None)
    path = solara.use_reactive(m0.ref if m0 is not None and m0.origin == "file" else "")
    band, set_band = solara.use_state(m0.band if m0 else None)
    mode, set_mode = solara.use_state("keep")
    error, set_error = solara.use_state(None)
    submitted = solara.use_ref(False)  # one Add per opening, held through the validation

    ref = (asset.value or {}).get("asset_id") if origin == "gee" else absolute(path.value)
    asset_type = (asset.value or {}).get("type") if origin == "gee" else "FILE"
    bands_task = use_band_list(gee_interface, origin, ref, asset_type)
    names, header = bands_task.value if bands_task.finished and bands_task.value else ((), None)
    items = ([{"text": b.name, "value": b.index} for b in header.bands] if header is not None
             else [{"text": n, "value": n} for n in names])

    async def _validate():
        live = app_state.project.value
        scope = _scope(live)
        if live is None:
            return {"error": "steps.prepare.mask_err_changed"}  # closed while the form was open
        out = await validate_mask(gee_interface, live, name=name, origin=origin, ref=ref, asset_type=asset_type,
                                  band=band, editing=editing)
        if not open_.value:
            return None  # closed during the check (ESC or Cancel): the user cancelled
        if _scope(app_state.project.value) != scope:
            return {"error": "steps.prepare.mask_err_changed"}
        if "error" not in out:
            # The project changes here, on the loop, never in the completion effect
            # below: an effect runs inside the render lock, and update_project
            # takes AppState._lock (state_manager's lock order; Global Constraints).
            if m0 is not None:
                on_save(editing, {k: v for k, v in out["fields"].items() if k != "name"})
            else:
                on_add(out["fields"], mode)
        return out

    validation = solara.lab.use_task(_validate, dependencies=None, prefer_threaded=False, raise_error=False)

    def _on_validated():
        if validation.cancelled:
            submitted.current = False  # the user stopped the check: the form can be sent again
        elif validation.error:
            set_error(error_text(validation.exception))
            submitted.current = False
        elif validation.finished and validation.value is not None:
            out = validation.value
            if "error" in out:
                set_error(t(out["error"]))
                submitted.current = False
                return
            open_.set(False)  # the task added or saved the mask

    solara.use_effect(_on_validated, [validation.pending, validation.finished, validation.error,
                                      validation.cancelled])

    def _cancel_on_unmount():
        def cleanup():
            # closing the dialog (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a check still running, or it
            # would add the mask the user just cancelled
            if validation.pending:
                try:
                    validation.cancel()
                except RuntimeError:
                    pass

        return cleanup

    solara.use_effect(_cancel_on_unmount, [])
    busy = validation.pending  # fields are frozen while their values are being checked

    def _submit():
        if submitted.current:
            return
        submitted.current = True
        set_error(None)
        validation()

    name_error = validate_mask_name(name, p, current=editing) if name and p is not None else None
    title = t("steps.prepare.mask_edit_title", name=editing) if m0 is not None else t("steps.prepare.mask_add_title")
    button = use_task_button(validation, on_start=_submit)  # a plain function, not a hook

    def _actions():
        # spec §11: an async button is a task button. While the check runs it
        # reads Cancel and stops the check, and it carries no Enter class.
        SubmitTaskButton(label=t("steps.alerts.save") if m0 is not None else t("steps.prepare.mask_add"),
                         icon="mdi-content-save-outline" if m0 is not None else "mdi-plus",
                         external_busy=bool(name_error), **button)

    # a validation error stays inline in the form (spec §11; form_card shows it)
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        rv.TextField(label=t("steps.prepare.mask_name"), v_model=name, on_v_model=set_name, dense=True,
                     outlined=True, disabled=m0 is not None or busy, hint=t("steps.prepare.mask_name_hint"),
                     persistent_hint=True, error_messages=[t(name_error)] if name_error else [])
        RasterInput(gee_interface=gee_interface, origin=origin, on_origin=set_origin, asset=asset, path=path,
                    disabled=busy)
        if bands_task.pending:
            solara.Text(t("steps.raster_input.bands_loading"), classes=["text--secondary"])
        elif bands_task.error:
            solara.Text(t("steps.raster_input.bands_failed", error=error_text(bands_task.exception)),
                        classes=["error--text"])
        rv.Select(label=t("steps.prepare.mask_band"), items=items, v_model=band, on_v_model=set_band, dense=True,
                  outlined=True, loading=bands_task.pending, disabled=busy)
        if m0 is None:  # an edit changes the definition; each period keeps its own mode
            with field_group(t("steps.prepare.mask_mode_label")):
                with rv.RadioGroup(v_model=mode, on_v_model=set_mode, row=True, dense=True, hide_details=True,
                                   disabled=busy):
                    rv.Radio(label=t("steps.prepare.mask_mode_keep"), value="keep")
                    rv.Radio(label=t("steps.prepare.mask_mode_exclude"), value="exclude")
        solara.Text(t("steps.prepare.masks_note"), classes=["text--secondary"])
