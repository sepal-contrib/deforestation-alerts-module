"""Visualization-layer modal (spec §6.3): an Earth Engine image or a GeoTIFF in
the user's SEPAL folder (one band and a colour ramp, or three bands as RGB, set
by hand or by band roles and a standard combination).

It follows ``source_modal``'s rules (Task 17), on the same form frame
(``gui/widget/form_dialog``), as ``mask_modal`` does: the Add/Save is a
``SubmitTaskButton`` over a ``use_task``; ``on_save`` (which calls
``update_project``) runs inside that task, on the loop, never in its
completion effect; closing the dialog cancels a check still running; the
fields are frozen while their values are checked; a project or AOI change
during the check refuses inline. What ``on_save`` raises is shown inline, so
it raises translated text.
"""

import asyncio
from pathlib import Path

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import use_task_button

from deforestation_alerts.engine.user_raster import UserRasterError, band_list_request, check_file, read_header
from deforestation_alerts.project.models import FileVisLayer, GeeVisLayer
from deforestation_alerts.project.names import name_from_ref, suggest_name_with_tail
from gui.i18n import t
from gui.scripts import vis_layers, vis_palettes
from gui.scripts.error_text import error_text
from gui.widget.default_name import use_default_name
from gui.widget.form_dialog import SubmitTaskButton, form_card, use_form_dialog
from gui.widget.raster_input import RasterInput, absolute, band_index, use_band_list
from gui.widget.text_style import MUTED


def parse_range(vmin, vmax):
    """``(min, max)`` as floats, or None unless both are numbers and min < max."""
    try:
        lo, hi = float(vmin), float(vmax)
    except (TypeError, ValueError):
        return None
    return (lo, hi) if lo < hi else None


def _number_text(value) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else str(value)


async def validate_vis_layer(gee_interface, project, *, kind, name, ref=None, asset_type=None, rgb=False,
                             bands=(), band=None, ramp=None, invert=False, palette=(), colormap=None, roles=None,
                             combo=None, vmin=None, vmax=None, show_in_analysis=False,
                             editing=None) -> dict:
    """Check a visualization layer before it is saved: ``{"error": key,
    "args": {...}}`` or ``{"layer": GeeVisLayer | FileVisLayer}``.

    ``bands`` are the three RGB bands (``rgb``); ``band`` is the one band of a
    single-band image or of a GeoTIFF. A single band is coloured by ``ramp``
    (``invert`` reverses it). With no ramp, the ``palette`` of a layer saved
    before the ramps is kept, or the ``colormap`` of a GeoTIFF saved before
    them; with none of these, the default ramp. ``roles`` and ``combo`` are
    kept only to prefill the form when the layer is edited."""
    name = (name or "").strip()
    error = vis_layers.validate_name(name, project, current=editing)
    if error:
        return {"error": error}
    span = parse_range(vmin, vmax)
    if span is None:
        return {"error": "steps.layers.err_range"}
    if not ref:
        return {"error": "steps.layers.err_incomplete"}
    if kind == "gee" and asset_type != "IMAGE":
        return {"error": "steps.layers.err_wrong_type"}
    is_file = kind == "file"
    if is_file:  # a GeoTIFF band is its 1-based index (a name is left from an asset)
        chosen = [band_index(b) for b in bands] if rgb else [band_index(band)]
    else:
        chosen = [str(b) if b is not None else None for b in (bands if rgb else (band,))]
    if len(chosen) != (3 if rgb else 1) or None in chosen:
        return {"error": "steps.layers.err_incomplete"}
    if len(set(chosen)) != len(chosen):
        return {"error": "steps.layers.err_duplicate_bands"}
    if rgb:
        roles = {r: b for r, b in (roles or {}).items() if b is not None}
        combo = combo or None
    if not is_file:
        names = await gee_interface.get_info_async(band_list_request(ref, "IMAGE"))
        if any(b not in names for b in chosen):
            return {"error": "steps.layers.err_bands"}
    else:
        try:
            header = await asyncio.to_thread(read_header, ref)
        except UserRasterError as exc:
            return {"error": exc.key}
        if rgb and any(b not in {x.index for x in header.bands} for b in chosen):
            return {"error": "steps.layers.err_bands"}
        error = check_file(header, chosen)
        if error:
            return {"error": error}
        if not vis_layers.folder_writable(ref):  # spec §9.1: its .ovr sidecar goes next to it
            return {"error": "steps.layers.err_folder", "args": {"folder": str(Path(ref).parent)}}
    extra = {"roles": roles, "combo": combo} if rgb else {}
    if rgb:
        colours, old_colormap = [], None
    elif ramp:
        colours, old_colormap = vis_palettes.ramp_palette(ramp, invert), None
    elif palette:
        colours, old_colormap = list(palette), None
    elif is_file and colormap:
        colours, old_colormap = [], colormap
    else:
        colours, old_colormap = vis_palettes.ramp_palette(vis_palettes.DEFAULT_RAMP, invert), None
    if not is_file:
        return {"layer": GeeVisLayer(name=name, asset=ref, bands=chosen, min=span[0], max=span[1], palette=colours,
                                     show_in_analysis=bool(show_in_analysis), **extra)}
    kwargs = {"bands": chosen} if rgb else {"band": chosen[0], "palette": colours}
    if old_colormap:
        kwargs["colormap"] = old_colormap
    return {"layer": FileVisLayer(name=name, path=ref, min=span[0], max=span[1],
                                  show_in_analysis=bool(show_in_analysis), **kwargs, **extra)}


# Ramp cards (as spatial_risk's variable modal): a gradient bar over its name in
# a bordered card; the selected card is ringed and bold.
_RAMP_GRID = "display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;width:100%;"
_RAMP_CARD = (
    "display:block;height:auto;min-width:0;width:100%;padding:6px;"
    "text-transform:none;letter-spacing:normal;font-weight:400;text-align:left;"
    "border:1px solid rgba(128,128,128,0.5);border-radius:4px;background:transparent;"
)
_RAMP_CARD_SELECTED = "box-shadow:0 0 0 2px currentColor;font-weight:600;"
_RAMP_BAR = (
    "display:block;height:14px;border-radius:3px;margin-bottom:6px;"
    "border:1px solid rgba(0,0,0,0.1);background:{gradient};"
)
_FIELDSET_STYLE = ("border:1px solid rgba(128,128,128,0.6);border-radius:4px;"
                   "padding:8px 12px 4px;margin:4px 0 0;min-width:0;")
_LEGEND_STYLE = "padding:0 4px;font-size:0.75rem;opacity:0.75;"


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


@solara.component
def VisLayerModal(open_, app_state, gee_interface, on_save, editing=None):
    # The form frame (gui/widget/form_dialog), 560px like every form that picks
    # a raster: persistent, an outside click nudges it, ESC closes it.
    with use_form_dialog(open_, "560px"):
        if open_.value:  # mounted per opening: a clean form, and no asset listing while closed
            _VisLayerForm(open_=open_, app_state=app_state, gee_interface=gee_interface, on_save=on_save,
                          editing=editing)


def _scope(p):
    """What the check must still match when it ends: the project and its AOI."""
    if p is None:
        return None
    return p.name, p.aoi.digest if p.aoi is not None else None


@solara.component
def _VisLayerForm(open_, app_state, gee_interface, on_save, editing):
    p = app_state.project.value
    l0 = p.vis_layers.get(editing) if p is not None and editing else None
    gee0 = l0 if l0 is not None and l0.kind == "gee" else None
    file0 = l0 if l0 is not None and l0.kind == "file" else None
    raster0 = gee0 or file0
    rgb0 = raster0 is not None and len(raster0.bands) == 3
    kind, set_kind = solara.use_state(l0.kind if l0 is not None else "gee")
    name, set_name = solara.use_state(l0.name if l0 is not None else "")
    asset = solara.use_reactive({"asset_id": gee0.asset, "type": "IMAGE", "column": "ALL", "value": None}
                                if gee0 is not None else None)
    path = solara.use_reactive(file0.path if file0 is not None else "")
    rgb, set_rgb = solara.use_state(rgb0)
    band, set_band = solara.use_state(file0.band if file0 is not None and not rgb0
                                      else gee0.bands[0] if gee0 is not None and gee0.bands and not rgb0 else None)
    red, set_red = solara.use_state(raster0.bands[0] if rgb0 else None)
    green, set_green = solara.use_state(raster0.bands[1] if rgb0 else None)
    blue, set_blue = solara.use_state(raster0.bands[2] if rgb0 else None)
    roles, set_roles = solara.use_state(dict(raster0.roles) if raster0 is not None else {})
    combo, set_combo = solara.use_state((raster0.combo if raster0 is not None else None) or "custom")
    vmin, set_vmin = solara.use_state(_number_text(l0.min) if l0 is not None else "0")
    vmax, set_vmax = solara.use_state(_number_text(l0.max) if l0 is not None else "1")
    palette0 = raster0.palette if raster0 is not None else []
    ramp0, invert0 = vis_palettes.ramp_choice(palette0)
    # A new layer starts on the default ramp. A layer saved before the ramps keeps
    # its palette or colormap until a card is picked, and preselects no card.
    ramp, set_ramp = solara.use_state(ramp0 if raster0 is not None else vis_palettes.DEFAULT_RAMP)
    invert, set_invert = solara.use_state(invert0)
    show, set_show = solara.use_state(bool(l0.show_in_analysis) if l0 is not None else False)
    old_colormap = file0.colormap if file0 is not None and not file0.palette and not file0.bands else None
    saved_roles = bool(raster0 is not None and raster0.roles)
    first_load = solara.use_ref(l0 is not None)  # the first band list of an edit must not touch its bands
    error, set_error = solara.use_state(None)
    submitted = solara.use_ref(False)  # one Save per opening, held through the validation

    def _set_kind(value):
        # A band is a name in Earth Engine and an index in a GeoTIFF: one kind's
        # band is never a choice of the other's.
        if value and value != kind:
            set_kind(value)
            set_band(None)
            for setter in (set_red, set_green, set_blue):
                setter(None)
            set_roles({})
            set_combo("custom")

    ref = (asset.value or {}).get("asset_id") if kind == "gee" else absolute(path.value)
    asset_type = (asset.value or {}).get("type") if kind == "gee" else "FILE"
    bands_task = use_band_list(gee_interface, kind, ref, asset_type)
    names, header = bands_task.value if bands_task.finished and bands_task.value else ((), None)
    band_items = ([{"text": b.name, "value": b.index} for b in header.bands] if header is not None
                  else [{"text": n, "value": n} for n in names])
    band_text = {i["value"]: i["text"] for i in band_items}

    def _apply(chosen_combo, chosen_roles):
        picked = vis_palettes.combo_bands(chosen_combo, chosen_roles)
        if picked:
            set_red(picked[0])
            set_green(picked[1])
            set_blue(picked[2])
        return bool(picked)

    def _set_rgb(on):
        set_rgb(on)
        if on and l0 is None and not (red or green or blue):  # a new RGB layer starts on the first composite
            first = vis_palettes.available_combos(roles)
            if first and _apply(first[0], roles):
                set_combo(first[0])

    def _guess_roles():
        if not band_items:
            return
        by_name = {i["text"]: i["value"] for i in band_items}
        guessed = {r: by_name[n] for r, n in vis_palettes.guess_roles(list(by_name)).items()}
        if first_load.current:  # an edit's own bands, roles and combination stay for its own image
            first_load.current = False
            if not saved_roles:  # a layer saved before the roles: only fill the empty role selects
                set_roles(guessed)
            return
        set_roles(guessed)
        set_combo("custom")
        for setter in (set_red, set_green, set_blue):
            setter(None)
        first = vis_palettes.available_combos(guessed)
        if rgb and l0 is None and first and _apply(first[0], guessed):
            set_combo(first[0])

    solara.use_effect(_guess_roles, [tuple(i["value"] for i in band_items)])

    def _set_role(role, value):
        updated = {k: v for k, v in {**roles, role: value}.items() if v is not None}
        set_roles(updated)
        if combo != "custom" and not _apply(combo, updated):  # the combination lost a role
            set_combo("custom")

    def _pick_combo(value):
        if not value:
            return
        set_combo(value)
        if value != "custom":
            _apply(value, roles)

    def _rgb_setter(setter):
        def on_value(value):
            setter(value)
            set_combo("custom")  # picked by hand: no longer a standard combination

        return on_value

    def _suggest(_ref):
        base = name_from_ref(ref)
        if rgb:
            # the combination's short key, so a long image name keeps a readable base
            tail = f"_{combo}" if combo != "custom" else "_rgb"
        else:
            tail = f"_{band_text.get(band, band)}" if band is not None else ""
        # the base is shortened, never the combination or band suffix
        return suggest_name_with_tail(
            base, tail, lambda n: vis_layers.validate_name(n, app_state.project.value, current=editing) is None,
            vis_layers.MAX_NAME)

    use_default_name(f"{ref}|{rgb}|{combo if rgb else ''}|{'' if rgb else band}" if ref and l0 is None else None,
                     name, set_name, _suggest)

    async def _validate():
        live = app_state.project.value
        scope = _scope(live)
        if live is None or live.aoi is None:
            return {"error": "steps.layers.err_changed"}  # closed while the form was open
        out = await validate_vis_layer(
            gee_interface, live, kind=kind, name=name, ref=ref, asset_type=asset_type, rgb=rgb,
            bands=(red, green, blue), band=band, ramp=ramp, invert=invert, palette=palette0 if ramp is None else (),
            colormap=old_colormap if ramp is None else None, roles=roles, combo=None if combo == "custom" else combo,
            vmin=vmin, vmax=vmax, show_in_analysis=show, editing=editing)
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
    button = use_task_button(validation, on_start=_submit)  # a plain function, not a hook

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

    def _ramp_fields():
        with rv.Html(tag="fieldset", style_=_FIELDSET_STYLE):
            rv.Html(tag="legend", children=[t("steps.layers.field_ramp")], style_=_LEGEND_STYLE)
            with rv.Html(tag="div", style_=_RAMP_GRID):
                for key in vis_palettes.RAMPS:
                    selected = key == ramp
                    solara.Button(
                        children=[rv.Html(tag="span", style_="display:block;width:100%;", children=[
                            rv.Html(tag="span", style_=_RAMP_BAR.format(gradient=vis_palettes.ramp_css(key, invert))),
                            rv.Html(tag="span", children=[t(f"steps.layers.ramp_{key}")])])],
                        on_click=lambda *_, k=key: set_ramp(k), disabled=busy, text=True,
                        style=_RAMP_CARD + (_RAMP_CARD_SELECTED if selected else ""),
                        classes=["dv-ramp"] + (["dv-ramp--selected"] if selected else []))
            rv.Switch(label=t("steps.layers.field_invert"), v_model=invert, on_v_model=set_invert, dense=True,
                      hide_details=True, disabled=busy or ramp is None, class_="mt-0 mb-1")

    def _actions():
        # spec §11: an async button is a task button. While the check runs it
        # reads Cancel and stops the check, and it carries no Enter class.
        SubmitTaskButton(label=t("common.save") if l0 is not None else t("steps.layers.add"),
                         icon="mdi-content-save-outline" if l0 is not None else "mdi-plus",
                         external_busy=bool(name_error), **button)

    # a validation error stays inline in the form (spec §11; form_card shows it)
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        rv.Select(label=t("steps.layers.field_kind"), v_model=kind, on_v_model=_set_kind,
                  items=[{"text": t(f"steps.layers.kind_{k}"), "value": k} for k in vis_layers.KINDS],
                  dense=True, outlined=True, disabled=busy)
        rv.TextField(label=t("steps.layers.field_name"), v_model=name, on_v_model=set_name, dense=True,
                     outlined=True, disabled=busy, counter=vis_layers.MAX_NAME,
                     error_messages=[t(name_error)] if name_error else [])
        RasterInput(gee_interface=gee_interface, origin=kind, on_origin=_set_kind, asset=asset, path=path,
                    asset_types=("IMAGE",), disabled=busy, show_origin=False)
        if bands_task.pending:
            solara.Text(t("steps.raster_input.bands_loading"), style=MUTED)
        elif bands_task.error:
            solara.Text(t("steps.raster_input.bands_failed", error=error_text(bands_task.exception)),
                        classes=["error--text"])
        rv.Switch(label=t("steps.layers.field_rgb"), v_model=rgb, on_v_model=_set_rgb, dense=True,
                  hide_details=True, disabled=busy)
        if rgb:
            solara.Text(t("steps.layers.roles_hint"), style=MUTED)
            with rv.Row(dense=True):
                for role in vis_palettes.ROLES:
                    with rv.Col(cols=6):
                        rv.Select(label=t(f"steps.layers.role_{role}"), items=band_items,
                                  v_model=roles.get(role), on_v_model=lambda value, r=role: _set_role(r, value),
                                  clearable=True, dense=True, outlined=True, hide_details=True,
                                  loading=bands_task.pending, disabled=busy)
            rv.Select(label=t("steps.layers.field_combo"), v_model=combo, on_v_model=_pick_combo,
                      items=[{"text": _capital(t(f"steps.layers.combo_{c}")), "value": c}
                             for c in [*vis_palettes.available_combos(roles), "custom"]],
                      dense=True, outlined=True, disabled=busy)
            _band_select(t("steps.layers.field_red"), red, _rgb_setter(set_red))
            _band_select(t("steps.layers.field_green"), green, _rgb_setter(set_green))
            _band_select(t("steps.layers.field_blue"), blue, _rgb_setter(set_blue))
            _range_fields()
        else:
            _band_select(t("steps.layers.field_band"), band, set_band)
            _ramp_fields()
            _range_fields()
        rv.Checkbox(label=t("steps.layers.field_show_in_analysis"), v_model=show, on_v_model=set_show, dense=True,
                    hide_details=True, disabled=busy)
