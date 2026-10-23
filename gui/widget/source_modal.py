"""Add Source modal (spec §6.2): a predefined source, offered with its
coverage of the AOI, or a custom source from an Earth Engine image or
collection or a GeoTIFF, read through one of the format presets.

The custom form reads Name, Source, the picker, then the bands (spec
2026-10-23-source-form): the bands are read once a raster is picked and
nothing that depends on them shows until they are. One band is a line (read
as the alert date); two or more an Alert format that starts on the suggested
preset and band fields that start on its bands. A date-only source's date
values are read over the AOI's bounds and suggest its encoding. Everything
below the picker is the raster block (``use_raster_fields``), so another
origin can draw its own fields in its place.
"""

import asyncio
from datetime import date
from types import SimpleNamespace

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import use_task_button

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import (
    NOT_COVERED_KEYS, chip_note, coverage_request, parse_coverage, record_for,
)
from deforestation_alerts.engine.user_raster import (
    UserRasterError, check_discovery, check_file, discovery_request, file_native, native_from_discovery,
    read_header, suggest_bands, suggest_format,
)
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import PRESETS, BandMap, CustomSource, mapped_bands
from deforestation_alerts.project.names import name_from_ref, suggest_name, validate_source_name
from gui.i18n import t
from gui.scripts import aoi_io, date_probes
from gui.scripts.coverage_ui import note_text
from gui.scripts.error_text import error_text
from gui.scripts.remote_facts import aoi_origin
from gui.scripts.solara_threads import use_loop_dependencies
from gui.widget.default_name import use_default_name
from gui.widget.form_dialog import SubmitTaskButton, form_card, use_form_dialog
from gui.widget.raster_input import (
    RasterInput, SourceSelect, absolute, band_choices, band_index, band_status, bands_block_save, bands_read,
    use_band_list,
)
from gui.widget.text_style import MUTED

FORMATS = ("radd", "ccdc", "pyeo", "date_only")
ENCODINGS = ("yymm", "yyddd", "yyyyddd", "decimal_year")
MAX_CUSTOM = 6
#: the Source select's choices, in order (a points/polygons source adds its own)
SOURCE_ORIGINS = ("gee", "file")


def available_predefined(project) -> list:
    return [k for k in catalogue.KEYS if project is None or k not in project.sources]


def predefined_items(project, pending: bool) -> list:
    """The predefined options with their coverage (spec §6.2 "Predefined")."""
    items = []
    for key in available_predefined(project):
        label = t(catalogue.LABEL_KEYS[key])
        cov = record_for(project, key)
        if cov is None:
            status = t("coverage.chip_checking") if pending else t("coverage.chip_unchecked")
            items.append({"text": f"{label} — {status}", "value": key, "disabled": False})
        elif cov.fraction <= 0:
            items.append({"text": t(NOT_COVERED_KEYS[key]), "value": key, "disabled": True})
        else:
            items.append({"text": f"{label} — {note_text(chip_note(cov))}", "value": key, "disabled": False})
    return items


def _bundle(d: dict):
    import ee

    return ee.Dictionary(d)


async def validate_custom(gee_interface, project, aoi_fc, *, name, origin, ref, asset_type, fmt, encoding,
                          alert_band, date_band, editing=None) -> dict:
    """Check a custom source before it is added or saved (spec §6.2)."""
    name = (name or "").strip()
    error = validate_source_name(name, project, current=editing)
    if error:
        return {"error": error}
    if not ref:
        return {"error": "steps.alerts.err_no_ref"}
    if origin == "gee" and asset_type not in ("IMAGE", "IMAGE_COLLECTION"):
        return {"error": "steps.alerts.err_wrong_type"}  # before the probe: CustomSource refuses any other
    if fmt not in FORMATS or date_band is None or (fmt != "date_only" and alert_band is None):
        return {"error": "steps.alerts.err_band_map"}
    band_map = BandMap(alert=None if fmt == "date_only" else alert_band, date=date_band)
    date_encoding = PRESETS[fmt]["encoding"] or encoding
    if origin == "file":  # a GeoTIFF is mapped by its band indexes
        alert, when = band_index(band_map.alert), band_index(band_map.date)
        if when is None or (band_map.alert is not None and alert is None):
            return {"error": "steps.alerts.err_band_map"}
        band_map = BandMap(alert=alert, date=when)
    probe = CustomSource(name=name, origin=origin, ref=ref, asset_type=asset_type, format=fmt,
                         date_encoding=date_encoding, band_map=band_map, native=None, digit=3)
    # alert first, then date (date only for date-only): discovery_request reads
    # the native grid from the first band
    bands = mapped_bands(probe)
    if origin == "gee":
        info = await gee_interface.get_info_async(_bundle({
            "discovery": discovery_request(ref, asset_type, [str(b) for b in bands]),
            "coverage": coverage_request(aoi_fc, (), [probe]),
        }))
        error = check_discovery(info["discovery"], fmt, [str(b) for b in bands])
        if error:
            return {"error": error}
        native = native_from_discovery(info["discovery"])
        raw = info["coverage"]
    else:
        try:
            header = await asyncio.to_thread(read_header, ref)
        except UserRasterError as exc:
            return {"error": exc.key}
        error = check_file(header, bands)
        if error:
            return {"error": error}
        # check_file refused a file without a CRS, so the WGS84 bounds the
        # coverage request and the runner read are always there
        native = file_native(header)
        probe = probe.model_copy(update={"native": native})
        raw = await gee_interface.get_info_async(coverage_request(aoi_fc, (), [probe]))
    cov = parse_coverage(raw, aoi_digest=project.aoi.digest, today=date.today())[name]
    if cov.fraction <= 0:
        return {"error": "steps.alerts.err_not_covered"}
    fields = dict(name=name, origin=origin, ref=ref, asset_type=asset_type, format=fmt,
                  date_encoding=date_encoding, band_map=band_map, native=native)
    return {"fields": fields, "coverage": cov}


def number_text(value) -> str:
    """A date value in a hint: a whole value as an integer, any other with at
    most 2 decimals (the date cap keeps them short)."""
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _encoding_label(encoding) -> str:
    """An encoding as the hints name it: its dropdown text before " (" ("yymm", "decimal year")."""
    return t(f"steps.alerts.encoding_{encoding}").split(" (")[0]


def _scope(p):
    """What a date-value read must still match when it ends: the project and its AOI."""
    if p is None:
        return None
    return p.name, p.aoi.digest if p.aoi is not None else None


def use_raster_fields(gee_interface, app_state, open_, src0, origin, ref, asset_type):
    """Hook: the custom form's raster block, everything below the picker.

    It reads the picked raster's bands (``use_band_list``) and, for a
    date-only source, its date band's lowest and highest values over the
    AOI's bounds (``gui/scripts/date_probes``), and holds the format, the
    band fields and the date encoding. The result carries those values,
    ``bands_task`` and ``date_task``, ``clear()`` (the caller's Source
    change), ``save_blocked`` (the bands or the dates are read, the bands
    failed, or a date-only source has no encoding) and ``draw(busy)``, a plain
    function that draws the block into the caller's container.

    With no raster (``ref`` None, as for an origin that is not a raster) both
    reads stay idle and ``draw`` draws nothing.

    Defaults: a new band list starts on its suggested format and bands
    (one band: date only, on that band), a format change on that format's
    bands, a new date band on the encoding its values read as. An edit
    (``src0``) keeps its saved values, with no date-value read, until its
    raster, format or date band changes (spec Goal 8)."""
    fmt, set_fmt = solara.use_state(src0.format if src0 else None)
    alert_band, set_alert_band = solara.use_state(src0.band_map.alert if src0 else None)
    date_band, set_date_band = solara.use_state(src0.band_map.date if src0 else None)
    encoding, set_encoding = solara.use_state(
        src0.date_encoding if src0 is not None and src0.format == "date_only" else None)
    ref0 = (src0.ref if src0.origin == "gee" else absolute(src0.ref)) if src0 is not None else None
    own = solara.use_ref(src0 is not None)  # still the edit's own raster, format and date band
    if own.current and ref != ref0:
        own.current = False  # another raster: the saved bands are not its own

    bands_task = use_band_list(gee_interface, origin, ref, asset_type)
    # nothing that depends on the bands shows until they are read; in the render
    # where the raster changes the task still holds the previous one's list
    has_bands = bool(ref) and bands_read(bands_task)
    # a GeoTIFF band reads as its text (description, else "Band N"); its value is its 1-based index
    choices = band_choices(*bands_task.value) if has_bands else []
    texts = [text for text, _ in choices]
    values = [value for _, value in choices]

    def _value_of(name):
        return values[texts.index(name)] if name is not None and name in texts else None

    def _apply(f):
        # ``f``'s defaults: its suggested bands, and no encoding until the date values are read
        alert, when = suggest_bands(f, texts)
        set_fmt(f)
        set_alert_band(None if f == "date_only" else _value_of(alert))
        set_date_band(values[0] if len(values) == 1 else _value_of(when))
        set_encoding(None)

    def _on_bands():
        if not values or own.current:
            return  # no list yet, or the edit's own raster keeps its saved bands
        _apply("date_only" if len(values) == 1 else suggest_format(texts))

    solara.use_effect(_on_bands, [tuple(choices)])

    def _set_format(value):
        if value and value != fmt:
            own.current = False
            _apply(value)

    def _set_date_band(value):
        if value is not None and value != date_band:
            own.current = False
            set_date_band(value)
            set_encoding(None)  # the new band's values say which

    def clear():
        own.current = False
        set_fmt(None)
        set_alert_band(None)
        set_date_band(None)
        set_encoding(None)

    p = app_state.project.value
    aoi = p.aoi if p is not None else None
    reads_dates = (has_bands and fmt == "date_only" and date_band is not None and date_band in values
                   and not own.current and aoi is not None)
    key = (origin, ref, asset_type, date_band, aoi.digest) if reads_dates else None

    async def _read_dates():
        if key is None:
            return None
        live = app_state.project.value
        scope = _scope(live)
        where, raster, kind, band, _digest = key
        bounds = live.aoi.bounds
        try:
            if where == "gee":
                info = await gee_interface.get_info_async(
                    date_probes.date_range_request(raster, kind, str(band), bounds))
                out = {"span": date_probes.date_range_from_info(info)}
            else:
                out = {"span": await asyncio.to_thread(date_probes.file_date_range, raster, band_index(band),
                                                       bounds)}
        except Exception as exc:  # noqa: BLE001 - shown as a line; the user picks the encoding
            out = {"error": exc}
        if not open_.value or _scope(app_state.project.value) != scope:
            return None  # closed, or the project or its AOI changed: the values are dropped
        return {"key": key, **out}

    # the key holds the AOI's digest, which a worker's publish can move: start
    # the read only from a render on the loop (gui/scripts/solara_threads)
    date_task = solara.lab.use_task(_read_dates, dependencies=use_loop_dependencies([key]),
                                    prefer_threaded=False, raise_error=False)
    done = date_task.value if date_task.finished and not date_task.pending else None
    result = done if done is not None and done["key"] == key else None  # never a read of another band
    reading = key is not None and result is None
    span = result.get("span") if result is not None else None
    guess = date_probes.guess_encoding(*span) if span is not None else None

    def _on_dates():
        if result is not None and "error" not in result:
            set_encoding(guess)  # None: values that are not dates, or none at all

    solara.use_effect(_on_dates, [date_task.pending, date_task.finished])

    def _cancel_on_unmount():
        def cleanup():
            if date_task.pending:
                try:
                    date_task.cancel()
                except RuntimeError:
                    pass

        return cleanup

    solara.use_effect(_cancel_on_unmount, [])

    save_blocked = (bands_block_save(bands_task, ref) or reading
                    or (bool(ref) and fmt == "date_only" and not encoding))

    def draw(busy):
        band_status(bands_task, ref)
        if not choices:
            return
        if len(choices) == 1:
            solara.Text(t("steps.alerts.band_one", name=texts[0]), style=MUTED)
        else:
            _format_field(busy)
            _band_fields(busy)
        if fmt == "date_only" and date_band is not None and date_band in values:
            _encoding_field(busy)

    def _format_field(busy):
        suggested = suggest_format(texts)
        items = [{"text": t("steps.layers.suggested", text=t(f"steps.alerts.format_{f}")) if f == suggested
                  else t(f"steps.alerts.format_{f}"), "value": f} for f in FORMATS]
        rv.Select(label=t("steps.alerts.format_label"), v_model=fmt, on_v_model=_set_format, items=items,
                  dense=True, outlined=True, disabled=busy,
                  hint=t(f"steps.alerts.format_help_{fmt}") if fmt else "", persistent_hint=bool(fmt))

    def _band_items(suggested):
        return [{"text": t("steps.layers.suggested", text=text) if value == suggested else text, "value": value}
                for text, value in choices]

    def _band_fields(busy):
        if fmt is None:
            return
        alert_name, date_name = suggest_bands(fmt, texts)
        if fmt != "date_only":
            missing = alert_band not in values
            # never a value that is not an item: a new list renders once before its defaults land
            rv.Select(label=t("steps.alerts.band_alert"), items=_band_items(_value_of(alert_name)),
                      v_model=None if missing else alert_band, on_v_model=set_alert_band, dense=True,
                      outlined=True, disabled=busy, hint=t("steps.alerts.alert_band_pick") if missing else "",
                      persistent_hint=missing)
        suggested = _value_of(date_name)
        if date_band not in values:
            hint = t("steps.alerts.date_band_pick")
        elif date_band == suggested:
            hint = t("steps.alerts.date_band_suggested", count=len(choices), name=texts[values.index(date_band)])
        else:
            hint = t("steps.alerts.date_band_help")
        rv.Select(label=t("steps.alerts.band_date"), items=_band_items(suggested),
                  v_model=date_band if date_band in values else None, on_v_model=_set_date_band, dense=True,
                  outlined=True, disabled=busy, hint=hint, persistent_hint=True)

    def _encoding_field(busy):
        if reading:
            solara.Text(t("steps.alerts.dates_loading"), style=MUTED)
            return
        hint = t("steps.alerts.encoding_help")
        if result is not None and "error" in result:
            solara.Text(t("steps.alerts.dates_failed", error=error_text(result["error"])), classes=["error--text"])
        elif result is not None and span is None:
            hint = t("steps.alerts.dates_none")
        elif span is not None:
            lo, hi = (number_text(v) for v in span)
            hint = (t("steps.alerts.dates_read", lo=lo, hi=hi, encoding=_encoding_label(guess)) if guess
                    else t("steps.alerts.dates_odd", lo=lo, hi=hi))
        items = [{"text": t("steps.layers.suggested", text=t(f"steps.alerts.encoding_{e}")) if e == guess
                  else t(f"steps.alerts.encoding_{e}"), "value": e} for e in ENCODINGS]
        rv.Select(label=t("steps.alerts.encoding_label"), v_model=encoding, on_v_model=set_encoding, items=items,
                  dense=True, outlined=True, disabled=busy, hint=hint, persistent_hint=True)

    return SimpleNamespace(fmt=fmt, alert_band=alert_band, date_band=date_band, encoding=encoding,
                           bands_task=bands_task, date_task=date_task, clear=clear, save_blocked=save_blocked,
                           draw=draw)


@solara.component
def SourceModal(open_, app_state, gee_interface, on_add_predefined, on_add_custom, on_save_custom, editing=None):
    # The form frame (gui/widget/form_dialog): persistent, an outside click
    # nudges it, ESC closes it. Its hooks run on every render, open or not.
    with use_form_dialog(open_, "560px"):
        if open_.value:  # mounted per opening: a clean form, and no asset listing while closed
            _SourceForm(open_=open_, app_state=app_state, gee_interface=gee_interface, editing=editing,
                        on_add_predefined=on_add_predefined, on_add_custom=on_add_custom,
                        on_save_custom=on_save_custom)


@solara.component
def _SourceForm(open_, app_state, gee_interface, editing, on_add_predefined, on_add_custom, on_save_custom):
    p = app_state.project.value
    src0 = p.sources.get(editing) if p is not None and editing else None
    n_custom = sum(1 for s in p.sources.values() if s.kind == "custom") if p is not None else 0
    cap_reached = src0 is None and n_custom >= MAX_CUSTOM
    pending_cov = app_state.coverage_pending.value
    mode, set_mode = solara.use_state("custom" if src0 is not None else "predefined")
    items = predefined_items(p, pending_cov)
    selectable = [i["value"] for i in items if not i["disabled"]]
    key, set_key = solara.use_state(selectable[0] if selectable else None)
    if key not in selectable:
        key = selectable[0] if selectable else None
    name, set_name = solara.use_state(src0.name if src0 else "")
    origin, set_origin = solara.use_state(src0.origin if src0 else "gee")
    asset = solara.use_reactive({"asset_id": src0.ref, "type": src0.asset_type, "column": "ALL", "value": None}
                                if src0 is not None and src0.origin == "gee" else None)
    path = solara.use_reactive(src0.ref if src0 is not None and src0.origin == "file" else "")
    error, set_error = solara.use_state(None)
    submitted = solara.use_ref(False)  # one Add per opening, held through the validation

    ref = (asset.value or {}).get("asset_id") if origin == "gee" else absolute(path.value)
    asset_type = (asset.value or {}).get("type") if origin == "gee" else "FILE"
    raster = use_raster_fields(gee_interface, app_state, open_, src0, origin, ref, asset_type)
    fmt, alert_band, date_band, encoding = raster.fmt, raster.alert_band, raster.date_band, raster.encoding
    use_default_name(
        ref if mode == "custom" and src0 is None else None, name, set_name,
        lambda r: suggest_name(name_from_ref(r), lambda n: validate_source_name(n, app_state.project.value) is None))

    def _set_origin(value):
        # Another Source clears the picked raster and its bands: a band is a name
        # in Earth Engine and an index in a GeoTIFF, never a choice of the other's
        if value and value != origin:
            set_origin(value)
            asset.set(None)
            path.set("")
            raster.clear()

    async def _validate():
        live = app_state.project.value
        digest = live.aoi.digest
        result = app_state.aoi_result.value
        # the geometry of THIS AOI: right after an AOI change ``aoi_result`` may
        # still hold the previous one's selection (remote_facts.aoi_origin)
        where = aoi_origin(live, result)
        if where is None:
            return {"error": "steps.alerts.err_aoi_not_ready"}
        if where == "result":
            aoi = aoi_io.ee_aoi(result, None)
        else:  # parsing the sidecar can take a while for a national AOI: off the loop
            aoi = await asyncio.to_thread(aoi_io.ee_aoi, None, paths.project_dir(live.name))
        out = await validate_custom(gee_interface, live, aoi, name=name, origin=origin, ref=ref,
                                    asset_type=asset_type, fmt=fmt, encoding=encoding, alert_band=alert_band,
                                    date_band=date_band, editing=editing)
        if not open_.value:
            return None  # closed during the check (ESC or Cancel): the user cancelled
        now = app_state.project.value
        if now is None or now.name != live.name or now.aoi is None or now.aoi.digest != digest:
            return {"error": "steps.alerts.err_aoi_changed"}  # the share is the previous AOI's
        if "error" not in out:
            # The project changes here, on the loop, never in the completion effect
            # below: an effect runs inside the render lock, and update_project
            # takes AppState._lock (state_manager's lock order; Global Constraints).
            if src0 is not None:
                on_save_custom(editing, out["fields"], out["coverage"])
            else:
                on_add_custom(out["fields"], out["coverage"])
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
            open_.set(False)  # the task added or saved the source

    solara.use_effect(_on_validated, [validation.pending, validation.finished, validation.error,
                                      validation.cancelled])

    def _cancel_on_unmount():
        def cleanup():
            # closing the dialog (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a check still running, or it
            # would add the source the user just cancelled
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
        if mode == "predefined":
            if key is not None:
                on_add_predefined(key)
            open_.set(False)
            return
        validation()

    name_error = validate_source_name(name, p, current=editing) if name else None
    title = t("steps.alerts.edit_title", name=editing) if src0 is not None else t("steps.alerts.add_title")
    waiting = mode == "predefined" and key is not None and pending_cov and record_for(p, key) is None
    button = use_task_button(validation, on_start=_submit)  # a plain function, not a hook

    def _actions():
        # spec §11: an async button is a task button. While the check runs it
        # reads Cancel and stops the check, and it carries no Enter class, so
        # Enter cannot stop it (Review Focus 1). The predefined branch of
        # _submit adds at once and never starts the task.
        SubmitTaskButton(
            label=t("coverage.chip_checking") if waiting else
            (t("steps.alerts.save") if src0 is not None else t("steps.alerts.add")),
            icon="mdi-content-save-outline" if src0 is not None else "mdi-plus",
            # a band list or date values still being read, a band list that could
            # not be read, or a date-only source with no encoding leaves nothing to save
            external_busy=waiting or (mode == "predefined" and key is None)
            or (mode == "custom" and (bool(name_error) or raster.save_blocked)),
            **button,
        )

    # a validation error stays inline in the form (spec §11; form_card shows it)
    with form_card(title, on_cancel=lambda: open_.set(False), actions=_actions, error=error):
        if src0 is None:  # an edit is always custom
            rv.Select(label=t("steps.alerts.mode_label"), v_model=mode,
                      on_v_model=lambda value: value and set_mode(value),
                      items=[{"text": t("steps.alerts.mode_predefined"), "value": "predefined"},
                             {"text": t("steps.alerts.mode_custom", n=n_custom), "value": "custom",
                              "disabled": cap_reached}],
                      hint=t("steps.alerts.cap_reached") if cap_reached else t("steps.alerts.mode_hint"),
                      persistent_hint=True, dense=True, outlined=True, disabled=busy)
        if mode == "predefined":
            if items:
                rv.Select(label=t("steps.alerts.predefined_label"), items=items, v_model=key,
                          on_v_model=set_key, dense=True, outlined=True, hint=t("steps.alerts.predefined_hint"),
                          persistent_hint=True)
                if key == "RADD":
                    solara.Text(t("coverage.radd_note"), style=MUTED)
            else:
                solara.Text(t("steps.alerts.all_added"), style=MUTED)
        else:
            rv.TextField(label=t("steps.alerts.name_label"), v_model=name, on_v_model=set_name, dense=True,
                         outlined=True, disabled=src0 is not None or busy, hint=t("steps.alerts.name_hint"),
                         persistent_hint=True, error_messages=[t(name_error)] if name_error else [])
            SourceSelect(origin=origin, on_origin=_set_origin, disabled=busy, origins=SOURCE_ORIGINS)
            RasterInput(gee_interface=gee_interface, origin=origin, asset=asset, path=path, disabled=busy)
            if asset_type == "IMAGE_COLLECTION":  # it does not depend on the bands
                solara.Text(t("steps.alerts.collection_help"), style=MUTED)
            raster.draw(busy)
