"""Visualization-layer modal (spec §6.3): an Earth Engine image (one band and a
palette, or three bands as RGB), a GeoTIFF in the user's SEPAL folder (a band
and a colormap) or Planet NICFI (a month, the region from the AOI centroid,
access checked).

It follows ``source_modal``'s rules (Task 17), as ``mask_modal`` does: the
Save is a ``TaskButtonComponent`` over a ``use_task``; ``on_save`` (which calls
``update_project``) runs inside that task, on the loop, never in its
completion effect; closing the dialog cancels a check still running; the
fields are frozen while their values are checked; a project or AOI change
during the check refuses inline. What ``on_save`` raises is shown inline, so
it raises translated text.
"""

import asyncio
import re
from pathlib import Path

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button

from deforestation_alerts.engine.user_raster import UserRasterError, band_list_request, check_file, read_header
from deforestation_alerts.project.models import FileVisLayer, GeeVisLayer, NicfiVisLayer
from gui.i18n import t
from gui.scripts import vis_layers
from gui.scripts.error_text import ee_no_access, error_text
from gui.widget.raster_input import RasterInput, absolute, use_band_list
from gui.widget.source_modal import _index

_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_HEX = re.compile(r"[0-9a-fA-F]{6}|[0-9a-fA-F]{3}")


def parse_range(vmin, vmax):
    """``(min, max)`` as floats, or None unless both are numbers and min < max."""
    try:
        lo, hi = float(vmin), float(vmax)
    except (TypeError, ValueError):
        return None
    return (lo, hi) if lo < hi else None


def parse_palette(text):
    """Comma-separated hex colours (``#`` optional) as a list without ``#``;
    an empty text is no palette; None when one entry is not a hex colour."""
    entries = [e.strip().lstrip("#") for e in str(text or "").split(",") if e.strip()]
    return entries if all(_HEX.fullmatch(e) for e in entries) else None


def _number_text(value) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value)


async def validate_vis_layer(gee_interface, project, *, kind, name, ref=None, asset_type=None, rgb=False,
                             bands=(), band=None, colormap="viridis", vmin=None, vmax=None, palette="", month="",
                             show_in_analysis=False, editing=None) -> dict:
    """Check a visualization layer before it is saved: ``{"error": key,
    "args": {...}}`` or ``{"layer": GeeVisLayer | FileVisLayer | NicfiVisLayer}``.

    ``bands`` are the three RGB bands (``rgb``); ``band`` is the one band of a
    single-band image or of a GeoTIFF."""
    name = (name or "").strip()
    error = vis_layers.validate_name(name, project, current=editing)
    if error:
        return {"error": error}
    if kind == "nicfi":
        month = (month or "").strip() or None
        if month is not None and not _MONTH.fullmatch(month):
            return {"error": "steps.layers.err_month"}
        region = vis_layers.nicfi_region(project.aoi.bounds)
        try:
            count = await gee_interface.get_info_async(vis_layers.nicfi_access_request(region))
        except Exception as exc:  # noqa: BLE001 - no access reads as an Earth Engine error
            if not ee_no_access(exc):
                raise
            count = 0
        if not isinstance(count, (int, float)) or count <= 0:
            return {"error": "steps.layers.err_nicfi", "args": {"region": vis_layers.region_label(region)}}
        return {"layer": NicfiVisLayer(name=name, month=month, show_in_analysis=bool(show_in_analysis))}
    span = parse_range(vmin, vmax)
    if span is None:
        return {"error": "steps.layers.err_range"}
    if not ref:
        return {"error": "steps.layers.err_incomplete"}
    if kind == "gee":
        if asset_type != "IMAGE":
            return {"error": "steps.layers.err_wrong_type"}
        chosen = [str(b) if b is not None else None for b in (bands if rgb else (band,))]
        if len(chosen) != (3 if rgb else 1) or None in chosen:
            return {"error": "steps.layers.err_incomplete"}
        colours = [] if rgb else parse_palette(palette)  # a palette applies to one band only
        if colours is None:
            return {"error": "steps.layers.err_palette"}
        names = await gee_interface.get_info_async(band_list_request(ref, "IMAGE"))
        if any(b not in names for b in chosen):
            return {"error": "steps.layers.err_bands"}
        return {"layer": GeeVisLayer(name=name, asset=ref, bands=chosen, min=span[0], max=span[1], palette=colours,
                                     show_in_analysis=bool(show_in_analysis))}
    band = _index(band)  # a GeoTIFF band is its 1-based index (a name is left from an asset)
    if band is None or colormap not in vis_layers.COLORMAPS:
        return {"error": "steps.layers.err_incomplete"}
    try:
        header = await asyncio.to_thread(read_header, ref)
    except UserRasterError as exc:
        return {"error": exc.key}
    error = check_file(header, [band])
    if error:
        return {"error": error}
    if not vis_layers.folder_writable(ref):  # spec §9.1: its .ovr sidecar goes next to it
        return {"error": "steps.layers.err_folder", "args": {"folder": str(Path(ref).parent)}}
    return {"layer": FileVisLayer(name=name, path=ref, band=band, colormap=colormap, min=span[0], max=span[1],
                                  show_in_analysis=bool(show_in_analysis))}


@solara.component
def VisLayerModal(open_, app_state, gee_interface, on_save, editing=None):
    dialog = rv.Dialog(v_model=open_.value, on_v_model=open_.set, max_width="520px", eager=True, persistent=True)
    # persistent blocks ESC too; restore it as a plain cancel. A hook: unconditional.
    rv.use_event(dialog, "keydown.esc", lambda *_: open_.set(False))
    with dialog:
        if open_.value:  # mounted per opening: a clean form, and no asset listing while closed
            _VisLayerForm(open_=open_, app_state=app_state, gee_interface=gee_interface, on_save=on_save,
                          editing=editing)


def _scope(p):
    """What the check must still match when it ends: the project and its AOI
    (the NICFI region comes from the AOI)."""
    if p is None:
        return None
    return p.name, p.aoi.digest if p.aoi is not None else None


@solara.component
def _VisLayerForm(open_, app_state, gee_interface, on_save, editing):
    p = app_state.project.value
    l0 = p.vis_layers.get(editing) if p is not None and editing else None
    gee0 = l0 if l0 is not None and l0.kind == "gee" else None
    file0 = l0 if l0 is not None and l0.kind == "file" else None
    rgb0 = gee0 is not None and len(gee0.bands) == 3
    kind, set_kind = solara.use_state(l0.kind if l0 is not None else "gee")
    name, set_name = solara.use_state(l0.name if l0 is not None else "")
    asset = solara.use_reactive({"asset_id": gee0.asset, "type": "IMAGE", "column": "ALL", "value": None}
                                if gee0 is not None else None)
    path = solara.use_reactive(file0.path if file0 is not None else "")
    rgb, set_rgb = solara.use_state(rgb0)
    band, set_band = solara.use_state(file0.band if file0 is not None
                                      else gee0.bands[0] if gee0 is not None and gee0.bands and not rgb0 else None)
    red, set_red = solara.use_state(gee0.bands[0] if rgb0 else None)
    green, set_green = solara.use_state(gee0.bands[1] if rgb0 else None)
    blue, set_blue = solara.use_state(gee0.bands[2] if rgb0 else None)
    vmin, set_vmin = solara.use_state(_number_text(l0.min) if l0 is not None and l0.kind != "nicfi" else "0")
    vmax, set_vmax = solara.use_state(_number_text(l0.max) if l0 is not None and l0.kind != "nicfi" else "1")
    palette, set_palette = solara.use_state(", ".join(gee0.palette) if gee0 is not None else "")
    colormap, set_colormap = solara.use_state(file0.colormap if file0 is not None else vis_layers.COLORMAPS[0])
    month, set_month = solara.use_state((l0.month or "") if l0 is not None and l0.kind == "nicfi" else "")
    show, set_show = solara.use_state(bool(l0.show_in_analysis) if l0 is not None else False)
    error, set_error = solara.use_state(None)
    submitted = solara.use_ref(False)  # one Save per opening, held through the validation

    def _set_kind(value):
        # A band is a name in Earth Engine and an index in a GeoTIFF: one kind's
        # band is never a choice of the other's.
        if value and value != kind:
            set_kind(value)
            set_band(None)


    raster = kind in ("gee", "file")
    ref = ((asset.value or {}).get("asset_id") if kind == "gee" else absolute(path.value) if kind == "file"
           else None)
    asset_type = (asset.value or {}).get("type") if kind == "gee" else "FILE"
    bands_task = use_band_list(gee_interface, kind if raster else "gee", ref, asset_type)
    names, header = bands_task.value if bands_task.finished and bands_task.value else ((), None)
    band_items = ([{"text": b.name, "value": b.index} for b in header.bands] if header is not None
                  else [{"text": n, "value": n} for n in names])
    region = vis_layers.nicfi_region(p.aoi.bounds) if p is not None and p.aoi is not None else None

    async def _validate():
        live = app_state.project.value
        scope = _scope(live)
        if live is None or live.aoi is None:
            return {"error": "steps.layers.err_changed"}  # closed while the form was open
        out = await validate_vis_layer(
            gee_interface, live, kind=kind, name=name, ref=ref, asset_type=asset_type, rgb=rgb,
            bands=(red, green, blue), band=band, colormap=colormap, vmin=vmin, vmax=vmax, palette=palette,
            month=month, show_in_analysis=show, editing=editing)
        if not open_.value:
            return None  # closed during the check (ESC or Cancel): the user cancelled
        if _scope(app_state.project.value) != scope:
            return {"error": "steps.layers.err_changed"}
        if "layer" in out:
            # The project changes here, on the loop, never in the completion effect
            # below: an effect runs inside the render lock, and update_project
            # takes AppState._lock (state_manager's lock order; Global Constraints).
            on_save(out["layer"], editing)
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
                set_error(t(out["error"], **out.get("args", {})))
                submitted.current = False
                return
            open_.set(False)  # the task saved the layer

    solara.use_effect(_on_validated, [validation.pending, validation.finished, validation.error,
                                      validation.cancelled])

    def _cancel_on_unmount():
        def cleanup():
            # closing the dialog (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a check still running, or it
            # would save the layer the user just cancelled
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

    name_error = vis_layers.validate_name(name, p, current=editing) if name and p is not None else None
    title = t("steps.layers.edit_title", name=editing) if l0 is not None else t("steps.layers.add_title")

    def _band_select(label, value, on_value):
        rv.Select(label=label, items=band_items, v_model=value, on_v_model=on_value, dense=True, outlined=True,
                  loading=bands_task.pending, disabled=busy)

    def _range_fields():
        with rv.Row(dense=True):
            with rv.Col():
                rv.TextField(label=t("steps.layers.field_min"), v_model=vmin, on_v_model=set_vmin, type="number",
                             dense=True, outlined=True, disabled=busy)
            with rv.Col():
                rv.TextField(label=t("steps.layers.field_max"), v_model=vmax, on_v_model=set_vmax, type="number",
                             dense=True, outlined=True, disabled=busy)

    with rv.Card():
        with rv.CardTitle():
            solara.Text(title)
        with rv.CardText():
            rv.Select(label=t("steps.layers.field_kind"), v_model=kind, on_v_model=_set_kind,
                      items=[{"text": t(f"steps.layers.kind_{k}"), "value": k} for k in vis_layers.KINDS],
                      dense=True, outlined=True, disabled=busy)
            rv.TextField(label=t("steps.layers.field_name"), v_model=name, on_v_model=set_name, dense=True,
                         outlined=True, disabled=busy, counter=vis_layers.MAX_NAME,
                         error_messages=[t(name_error)] if name_error else [])
            if raster:
                RasterInput(gee_interface=gee_interface, origin=kind, on_origin=_set_kind, asset=asset, path=path,
                            asset_types=("IMAGE",), disabled=busy, show_origin=False)
                if bands_task.pending:
                    solara.Text(t("steps.raster_input.bands_loading"), classes=["text--secondary"])
                elif bands_task.error:
                    solara.Text(t("steps.raster_input.bands_failed", error=error_text(bands_task.exception)),
                                classes=["error--text"])
            if kind == "gee":
                rv.Switch(label=t("steps.layers.field_rgb"), v_model=rgb, on_v_model=set_rgb, dense=True,
                          hide_details=True, disabled=busy)
                if rgb:
                    _band_select(t("steps.layers.field_red"), red, set_red)
                    _band_select(t("steps.layers.field_green"), green, set_green)
                    _band_select(t("steps.layers.field_blue"), blue, set_blue)
                else:
                    _band_select(t("steps.layers.field_band"), band, set_band)
                _range_fields()
                if not rgb:
                    rv.TextField(label=t("steps.layers.field_palette"), v_model=palette, on_v_model=set_palette,
                                 dense=True, outlined=True, disabled=busy)
            elif kind == "file":
                _band_select(t("steps.layers.field_band"), band, set_band)
                rv.Select(label=t("steps.layers.field_colormap"), v_model=colormap, on_v_model=set_colormap,
                          items=list(vis_layers.COLORMAPS), dense=True, outlined=True, disabled=busy)
                _range_fields()
            else:
                rv.TextField(label=t("steps.layers.field_month"), v_model=month, on_v_model=set_month, dense=True,
                             outlined=True, disabled=busy, placeholder="YYYY-MM", hint=t("steps.layers.month_hint"),
                             persistent_hint=True)
                if region is not None:
                    solara.Text(t("steps.layers.region", region=vis_layers.region_label(region)),
                                classes=["text--secondary"])
            rv.Checkbox(label=t("steps.layers.field_show_in_analysis"), v_model=show, on_v_model=set_show, dense=True,
                        hide_details=True, disabled=busy)
            if error:  # validation errors stay inline in the modal (spec §11)
                solara.Text(error, classes=["error--text"])
        with rv.CardActions(style_="justify-content:flex-end;gap:8px;"):
            solara.Button(t("common.cancel"), text=True, small=True, on_click=lambda: open_.set(False))
            # spec §11: an async button is a TaskButtonComponent (while the check
            # runs it reads Cancel and stops it)
            TaskButtonComponent(label=t("common.save"), cancel_label=t("common.cancel"), small=True,
                                external_busy=bool(name_error), **use_task_button(validation, on_start=_submit))
