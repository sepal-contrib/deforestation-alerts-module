"""Visualization-layer modal (spec §6.3): an Earth Engine image or a GeoTIFF in
the user's SEPAL folder (one band, Continuous with a colour ramp or Categorical
with a colour per class; or, for three bands or more, an RGB composite set by
hand or by band roles and a standard combination).

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
from gui.scripts import vis_classes, vis_layers, vis_palettes, vis_probes
from gui.scripts.error_text import error_text
from gui.scripts.legend_data import format_number
from gui.scripts.legend_specs import vis_legend
from gui.widget.default_name import use_default_name
from gui.widget.form_dialog import SubmitTaskButton, form_card, use_form_dialog
from gui.widget.raster_input import RasterInput, absolute, band_index, use_band_list
from gui.widget.text_style import MUTED
from gui.widget.vis_style_fields import (
    CHANNEL_CSS, BandsField, ClassList, CompositeSelect, LegendPreview, PaletteMenu, RampSelect, RasterTypeSelect,
    channel_class,
)


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


async def validate_vis_layer(gee_interface, project, *, kind, name, ref=None, asset_type=None,
                             style="continuous", bands=(), band=None, ramp=None, invert=False, palette=(),
                             colormap=None, roles=None, combo=None, classes=(), vmin=None, vmax=None,
                             show_in_analysis=False, editing=None) -> dict:
    """Check a visualization layer before it is saved: ``{"error": key,
    "args": {...}}`` or ``{"layer": GeeVisLayer | FileVisLayer}``.

    ``style`` is "continuous", "categorical" or "rgb". ``bands`` are the three
    RGB bands (style "rgb"); ``band`` is the one band of a single-band image or
    of a GeoTIFF. A continuous band is coloured by ``ramp`` (``invert``
    reverses it); a categorical one by its ``classes``, which have no range.
    With no ramp, the ``palette`` of a layer saved before the ramps is kept,
    or the ``colormap`` of a GeoTIFF saved before them; with none of these,
    the default ramp. ``roles`` and ``combo`` are kept only to prefill the
    form when the layer is edited."""
    name = (name or "").strip()
    error = vis_layers.validate_name(name, project, current=editing)
    if error:
        return {"error": error}
    rgb = style == "rgb"
    categorical = style == "categorical"
    span = (0.0, 1.0) if categorical else parse_range(vmin, vmax)
    if span is None:
        return {"error": "steps.layers.err_range"}
    if not ref:
        return {"error": "steps.layers.err_incomplete"}
    if kind == "gee" and asset_type != "IMAGE":
        return {"error": "steps.layers.err_wrong_type"}
    if categorical:
        values = [c.value for c in classes]
        if not values:
            return {"error": "steps.layers.err_no_classes"}
        if len(set(values)) != len(values):
            return {"error": "steps.layers.err_duplicate_class"}
        if len(values) > vis_classes.MAX_CLASSES:
            return {"error": "steps.layers.err_class_limit"}
        if not any(c.visible for c in classes):
            return {"error": "steps.layers.err_no_visible_class"}
        classes = [c.model_copy(update={"color": vis_palettes._bare(c.color)}) for c in classes]
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
    extra = {"roles": roles, "combo": combo} if rgb else {"classes": classes} if categorical else {}
    if rgb or categorical:
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
                                     style=style, show_in_analysis=bool(show_in_analysis), **extra)}
    kwargs = {"bands": chosen} if rgb else {"band": chosen[0], "palette": colours}
    if old_colormap:
        kwargs["colormap"] = old_colormap
    return {"layer": FileVisLayer(name=name, path=ref, min=span[0], max=span[1], style=style,
                                  show_in_analysis=bool(show_in_analysis), **kwargs, **extra)}


def preview_layer(kind, style, *, band, bands, ramp, invert, palette, colormap, classes, vmin, vmax):
    """The draft layer the form's legend preview is drawn from, coloured as
    ``validate_vis_layer`` would save it; None while the range is invalid
    (continuous, rgb), no class is visible (categorical) or the three bands
    are not all chosen (rgb)."""
    if style == "rgb" and None in ([band_index(b) for b in bands] if kind == "file" else list(bands)):
        return None
    if style == "categorical":
        if not vis_classes.visible_classes(classes):
            return None
        span = (0.0, 1.0)
    else:
        span = parse_range(vmin, vmax)
        if span is None:
            return None
    kw = {"name": "preview", "min": span[0], "max": span[1], "style": style}
    if style == "categorical":
        kw["classes"] = list(classes)
    elif style == "continuous":
        if ramp:
            kw["palette"] = vis_palettes.ramp_palette(ramp, invert)
        elif palette:
            kw["palette"] = list(palette)
        elif not (kind == "file" and colormap):
            kw["palette"] = vis_palettes.ramp_palette(vis_palettes.DEFAULT_RAMP, invert)
    if kind == "file":
        if style == "rgb":
            kw["bands"] = [i for i in (band_index(b) for b in bands) if i is not None]
        if colormap and "palette" not in kw:
            kw["colormap"] = colormap
        return FileVisLayer(path="", band=band_index(band) or 1, **kw)
    chosen = bands if style == "rgb" else (band,)
    return GeeVisLayer(asset="", bands=[str(b) for b in chosen if b is not None], **kw)


def _probe_error(err) -> str:
    """A probe's refusal (a message key) or failure (an exception), in the current language."""
    return t(err["key"]) if "key" in err else error_text(err["exception"])


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
    style0 = vis_layers.effective_style(raster0) if raster0 is not None else "continuous"
    rgb0 = style0 == "rgb"
    kind, set_kind = solara.use_state(l0.kind if l0 is not None else "gee")
    name, set_name = solara.use_state(l0.name if l0 is not None else "")
    asset = solara.use_reactive({"asset_id": gee0.asset, "type": "IMAGE", "column": "ALL", "value": None}
                                if gee0 is not None else None)
    path = solara.use_reactive(file0.path if file0 is not None else "")
    # Two steps: keep every band (RGB) or draw one; one band is Continuous or Categorical.
    bands_mode, set_bands_mode = solara.use_state("all" if rgb0 else "one")
    one_style, set_one_style = solara.use_state("categorical" if style0 == "categorical" else "continuous")
    classes, set_classes = solara.use_state(list(raster0.classes) if style0 == "categorical" else [])
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
    # its palette or colormap until a ramp is picked, and preselects no ramp.
    ramp, set_ramp = solara.use_state(ramp0 if raster0 is not None else vis_palettes.DEFAULT_RAMP)
    invert, set_invert = solara.use_state(invert0)
    show, set_show = solara.use_state(bool(l0.show_in_analysis) if l0 is not None else False)
    old_colormap = file0.colormap if file0 is not None and not file0.palette and not file0.bands else None
    saved_roles = bool(raster0 is not None and raster0.roles)
    first_load = solara.use_ref(l0 is not None)  # the first band list of an edit must not touch its bands
    error, set_error = solara.use_state(None)
    # what the probes refused, shown under their buttons until the band changes:
    # {"key": ...} or {"exception": ...}, translated as it is drawn (_probe_error)
    stretch_error, set_stretch_error = solara.use_state(None)
    classes_error, set_classes_error = solara.use_state(None)
    # a first find of only 0 and 1 says how they were set up, until the band changes
    zero_one, set_zero_one = solara.use_state(False)
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
    n_bands = len(band_items) if bands_task.finished else None
    # nothing below the picker shows until its band list is read (spec §3)
    bands_read = bool(ref) and bands_task.finished and not bands_task.error and bool(band_items)
    # RGB needs three bands: an RGB edit pointed at a smaller raster is drawn from one band
    style = "rgb" if bands_mode == "all" and (n_bands is None or n_bands >= 3) else one_style

    def _apply(chosen_combo, chosen_roles):
        picked = vis_palettes.combo_bands(chosen_combo, chosen_roles)
        if picked:
            set_red(picked[0])
            set_green(picked[1])
            set_blue(picked[2])
        return bool(picked)

    def _set_bands_mode(value):
        if not value:
            return
        set_bands_mode(value)
        if value == "all" and not (red or green or blue):  # an RGB composite starts on the first combination
            first = vis_palettes.available_combos(roles)
            if first and _apply(first[0], roles):
                set_combo(first[0])
            else:  # no combination found: the first three bands, picked by hand
                for setter, item in zip((set_red, set_green, set_blue), band_items[:3]):
                    setter(item["value"])
                set_combo("custom")

    # the roles the band names give, {role: band value}; red, green and blue all found suggest RGB
    by_name = {i["text"]: i["value"] for i in band_items}
    guessed = {r: by_name[n] for r, n in vis_palettes.guess_roles(list(by_name)).items()}
    rgb_found = all(r in guessed for r in ("red", "green", "blue"))

    def _guess_roles():
        if not band_items:
            return
        if first_load.current:  # an edit's own bands, roles and combination stay for its own image
            first_load.current = False
            if not saved_roles:  # a layer saved before the roles: they are guessed from the band names
                set_roles(guessed)
            # one band is always drawn from that band; an RGB edit on fewer than
            # three bands is drawn from one band, which starts on the first
            if len(band_items) == 1 or (bands_mode == "all" and len(band_items) < 3):
                set_band(band_items[0]["value"])
            return
        set_roles(guessed)
        set_combo("custom")
        set_band(band_items[0]["value"])  # the Band select starts on the image's first band
        for setter in (set_red, set_green, set_blue):
            setter(None)
        # spectral bands are kept as RGB; any other image draws one band (never guessed Categorical)
        set_bands_mode("all" if rgb_found else "one")
        first = vis_palettes.available_combos(guessed)
        if rgb_found and first and _apply(first[0], guessed):
            set_combo(first[0])

    solara.use_effect(_guess_roles, [tuple(i["value"] for i in band_items)])

    probed = solara.use_ref((kind, ref, band))  # what the classes and the probe errors were found for

    def _clear_probes():
        # classes found on one band are never kept on another band, raster or
        # kind; an edit opens on its own (kind, ref, band), so keeps its classes
        if probed.current == (kind, ref, band):
            return
        probed.current = (kind, ref, band)
        set_classes([])
        set_classes_error(None)
        set_stretch_error(None)
        set_zero_one(False)

    solara.use_effect(_clear_probes, [kind, ref, band])

    # a stretch refused for one RGB composite says nothing of another
    solara.use_effect(lambda: set_stretch_error(None), [combo, red, green, blue])

    def _pick_combo(value):
        if not value:
            return
        set_combo(value)
        if value != "custom":
            _apply(value, roles)

    def _rgb_setter(channel, setter):
        def on_value(value):
            setter(value)
            # picked by hand: Custom, unless the three bands now make a standard combination
            picked = [value if i == channel else b for i, b in enumerate((red, green, blue))]
            set_combo(vis_palettes.match_combo(picked, roles) or "custom")

        return on_value

    def _suggest(_ref):
        base = name_from_ref(ref)
        if style == "rgb":
            # the combination's short key, so a long image name keeps a readable base
            tail = f"_{combo}" if combo != "custom" else "_rgb"
        else:
            tail = f"_{band_text.get(band, band)}" if band is not None else ""
        # the base is shortened, never the combination or band suffix
        return suggest_name_with_tail(
            base, tail, lambda n: vis_layers.validate_name(n, app_state.project.value, current=editing) is None,
            vis_layers.MAX_NAME)

    rgb = style == "rgb"
    auto_name = use_default_name(
        f"{ref}|{rgb}|{combo if rgb else ''}|{'' if rgb else band}" if ref and l0 is None else None,
        name, set_name, _suggest) and l0 is None  # an edit's name is never filled from its raster

    async def _validate():
        live = app_state.project.value
        scope = _scope(live)
        if live is None or live.aoi is None:
            return {"error": "steps.layers.err_changed"}  # closed while the form was open
        out = await validate_vis_layer(
            gee_interface, live, kind=kind, name=name, ref=ref, asset_type=asset_type,
            style=style, bands=(red, green, blue), band=band, ramp=ramp, invert=invert,
            palette=palette0 if ramp is None else (),
            colormap=old_colormap if ramp is None else None, roles=roles, combo=None if combo == "custom" else combo,
            classes=classes, vmin=vmin, vmax=vmax, show_in_analysis=show, editing=editing)
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

    stretch_bands = [red, green, blue] if style == "rgb" else [band]

    async def _stretch():
        # p2/p98 of the band over the AOI's bounds (gui/scripts/vis_probes); for
        # RGB the lowest p2 and the highest p98 of the three bands, one shared range
        live = app_state.project.value
        scope = _scope(live)
        if live is None or live.aoi is None or not ref or None in stretch_bands:
            return None
        bounds = live.aoi.bounds
        if kind == "gee":
            info = await gee_interface.get_info_async(vis_probes.stretch_request(ref, stretch_bands, bounds))
            span = vis_probes.stretch_from_info(info, stretch_bands)
        else:
            span = await asyncio.to_thread(vis_probes.file_stretch, ref, [band_index(b) for b in stretch_bands],
                                           bounds)
        if not open_.value or _scope(app_state.project.value) != scope:
            return None  # closed, or the project or its AOI changed: the range is dropped
        return {"span": span} if span is not None else {"error": "steps.layers.err_no_stretch"}

    stretch_task = solara.lab.use_task(_stretch, dependencies=None, prefer_threaded=False, raise_error=False)

    def _on_stretched():
        if stretch_task.error:
            set_stretch_error({"exception": stretch_task.exception})
            return
        out = stretch_task.value if stretch_task.finished else None
        if out and "span" in out:
            lo, hi = out["span"]
            set_vmin(_number_text(round(lo, 6)))
            set_vmax(_number_text(round(hi, 6)))
        elif out and "error" in out:
            set_stretch_error({"key": out["error"]})

    solara.use_effect(_on_stretched, [stretch_task.pending, stretch_task.finished, stretch_task.error])

    async def _find_classes():
        # the band's values over the AOI's bounds, with its *_class_* names (gui/scripts/vis_probes)
        live = app_state.project.value
        scope = _scope(live)
        if live is None or live.aoi is None or not ref or band is None:
            return None
        bounds = live.aoi.bounds
        if kind == "gee":
            info = await gee_interface.get_info_async(vis_probes.classes_request(ref, band, bounds))
            out = vis_probes.classes_from_info(info, band)
        else:
            out = await asyncio.to_thread(vis_probes.file_classes, ref, band_index(band), bounds)
        if not open_.value or _scope(app_state.project.value) != scope:
            return None  # closed, or the project or its AOI changed: the classes are dropped
        return out

    classes_task = solara.lab.use_task(_find_classes, dependencies=None, prefer_threaded=False, raise_error=False)

    def _on_classes():
        if classes_task.error:
            set_classes_error({"exception": classes_task.exception})
            return
        out = classes_task.value if classes_task.finished else None
        if out and "values" in out:
            # a new find adds the new values; the listed classes keep their
            # colours, labels and visibility (only the palette menu repaints them)
            palette = vis_classes.palette_of(classes) or vis_palettes.DEFAULT_QUALITATIVE
            # a 0/1 band's 1 is named after the band's text (a GeoTIFF band is an index)
            found = vis_classes.classes_from_values(out["values"], palette=palette, known=out["known"],
                                                    band_label=band_text.get(band))
            if not classes:  # a first find, nothing listed yet: only 0 and 1, which the image does not name
                set_zero_one(vis_classes.is_zero_one(out["values"]) and not out["known"])
            set_classes(vis_classes.merge_found(classes, found))
        elif out and "error" in out:
            set_classes_error({"key": out["error"]})  # the list is left as it was

    solara.use_effect(_on_classes, [classes_task.pending, classes_task.finished, classes_task.error])

    def _start_probe(task, set_probe_error):
        set_probe_error(None)
        task()

    def _pick_palette(key):
        set_classes(vis_classes.recolor(classes, key))

    def _cancel_on_unmount():
        def cleanup():
            # closing the dialog (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a check still running, or it
            # would save the layer the user just cancelled; stop a probe too
            for task in (validation, stretch_task, classes_task):
                if task.pending:
                    try:
                        task.cancel()
                    except RuntimeError:
                        pass

        return cleanup

    solara.use_effect(_cancel_on_unmount, [])
    # fields are frozen while their values are being checked or probed
    busy = validation.pending or stretch_task.pending or classes_task.pending

    def _submit():
        if submitted.current:
            return
        submitted.current = True
        set_error(None)
        validation()

    name_error = vis_layers.validate_name(name, p, current=editing) if name and p is not None else None
    title = t("steps.layers.edit_title", name=editing) if l0 is not None else t("steps.layers.add_title")
    button = use_task_button(validation, on_start=_submit)  # a plain function, not a hook

    def _channel_select(label, value, on_value, channel):
        # an RGB band's label leads with its colour dot (CHANNEL_CSS)
        rv.Select(label=label, items=band_items, v_model=value, on_v_model=on_value, dense=True, outlined=True,
                  disabled=busy, class_=channel_class(channel))

    def _range_fields():
        with rv.Row(dense=True):
            with rv.Col():
                rv.TextField(label=t("steps.layers.field_min"), v_model=vmin, on_v_model=set_vmin, type="number",
                             dense=True, outlined=True, disabled=busy)
            with rv.Col():
                rv.TextField(label=t("steps.layers.field_max"), v_model=vmax, on_v_model=set_vmax, type="number",
                             dense=True, outlined=True, disabled=busy)

    def _stretch_fields():
        no_aoi = p is None or p.aoi is None
        solara.Button(label=t("steps.layers.stretch"), icon_name="mdi-arrow-expand-horizontal",
                      on_click=lambda: _start_probe(stretch_task, set_stretch_error), outlined=True,
                      color="primary", small=True,
                      disabled=busy or no_aoi or not ref or None in stretch_bands)
        solara.Text(t("steps.layers.stretch_hint"), style=MUTED)
        if stretch_error:
            solara.Text(_probe_error(stretch_error), classes=["error--text"])

    def _class_fields():
        no_aoi = p is None or p.aoi is None
        solara.Button(label=t("steps.layers.find_classes"), icon_name="mdi-magnify",
                      on_click=lambda: _start_probe(classes_task, set_classes_error), outlined=True,
                      color="primary", small=True,
                      disabled=busy or no_aoi or not ref or band is None)
        solara.Text(t("steps.layers.find_classes_hint"), style=MUTED)
        if zero_one:
            solara.Text(t("steps.layers.zero_one_note"), style=MUTED)
        if classes_error:
            solara.Text(_probe_error(classes_error), classes=["error--text"])
        # keyed on what the classes were found for: a clear also empties "Add a value" and its error
        ClassList(classes=classes, on_classes=set_classes, disabled=busy).key(f"classes|{kind}|{ref}|{band}")
        PaletteMenu(value=vis_classes.palette_of(classes), on_value=_pick_palette, disabled=busy or not classes)

    def _legend_preview():
        draft = preview_layer(kind, style, band=band, bands=(red, green, blue), ramp=ramp, invert=invert,
                              palette=palette0 if ramp is None else (),
                              colormap=old_colormap if ramp is None else None, classes=classes, vmin=vmin,
                              vmax=vmax)
        if draft is None:
            return
        mid = format_number((draft.min + draft.max) / 2) if style != "categorical" else None
        LegendPreview(spec=vis_legend("preview", draft).spec, mid=mid)

    def _actions():
        # spec §11: an async button is a task button. While the check runs it
        # reads Cancel and stops the check, and it carries no Enter class.
        # a band list still being read, or that could not be read, leaves nothing to save
        SubmitTaskButton(label=t("common.save") if l0 is not None else t("steps.layers.add"),
                         icon="mdi-content-save-outline" if l0 is not None else "mdi-plus",
                         external_busy=bool(name_error) or bool(bands_task.error) or (bool(ref) and bands_task.pending),
                         **button)

    # a validation error stays inline in the form (spec §11; form_card shows it)
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        # with the form, not the band selects: the outlined label's notch is
        # measured when a select mounts, so the dot's rule must be loaded by then
        solara.Style(CHANNEL_CSS)
        rv.TextField(label=t("steps.layers.field_name"), v_model=name, on_v_model=set_name, dense=True,
                     outlined=True, disabled=busy, counter=vis_layers.MAX_NAME, hint=t("steps.layers.name_hint"),
                     persistent_hint=auto_name, error_messages=[t(name_error)] if name_error else [])
        rv.Select(label=t("steps.layers.field_source"), v_model=kind, on_v_model=_set_kind,
                  items=[{"text": t(f"steps.layers.kind_{k}"), "value": k} for k in vis_layers.KINDS],
                  hint=t("steps.layers.source_hint"), persistent_hint=True, dense=True, outlined=True,
                  disabled=busy)
        RasterInput(gee_interface=gee_interface, origin=kind, on_origin=_set_kind, asset=asset, path=path,
                    asset_types=("IMAGE",), disabled=busy, show_origin=False)
        if bands_task.pending:
            solara.Text(t("steps.raster_input.bands_loading"), style=MUTED)
        elif bands_task.error:
            solara.Text(t("steps.raster_input.bands_failed", error=error_text(bands_task.exception)),
                        classes=["error--text"])
        if bands_read:
            BandsField(band_items=band_items, mode=bands_mode, on_mode=_set_bands_mode, band=band, on_band=set_band,
                       rgb_found=rgb_found, disabled=busy)
        if bands_read and rgb:
            CompositeSelect(combo=combo, on_combo=_pick_combo, roles=roles, band_text=band_text,
                            fallback=[i["value"] for i in band_items[:3]], disabled=busy)
            with rv.Row(dense=True):  # the three bands, filled by the combination or picked by hand
                for i, (key, value, setter) in enumerate((("steps.layers.field_red", red, set_red),
                                                          ("steps.layers.field_green", green, set_green),
                                                          ("steps.layers.field_blue", blue, set_blue))):
                    with rv.Col(cols=4):
                        _channel_select(t(key), value, _rgb_setter(i, setter), channel=i)
            _range_fields()
            _stretch_fields()
        elif bands_read:
            RasterTypeSelect(value=one_style, on_value=set_one_style,
                             suggested=vis_classes.looks_categorical(band_text.get(band, "")), disabled=busy)
            if style == "continuous":
                RampSelect(value=ramp, on_value=set_ramp, invert=invert, on_invert=set_invert, disabled=busy)
                _range_fields()
                _stretch_fields()
            else:
                _class_fields()
            _legend_preview()
        rv.Checkbox(label=t("steps.layers.field_show_in_analysis"), v_model=show, on_v_model=set_show, dense=True,
                    hide_details=True, disabled=busy)
