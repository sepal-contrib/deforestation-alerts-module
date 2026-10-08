"""Add Source modal (spec §6.2): a predefined source, offered with its
coverage of the AOI, or a custom source from an Earth Engine image or
collection or a GeoTIFF, read through one of three format presets."""

import asyncio
from datetime import date

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import use_task_button

from deforestation_alerts.engine import catalogue
from deforestation_alerts.engine.coverage import (
    NOT_COVERED_KEYS, chip_note, coverage_request, parse_coverage, record_for,
)
from deforestation_alerts.engine.user_raster import (
    UserRasterError, check_discovery, check_file, discovery_request, file_native, native_from_discovery,
    read_header, suggest_band_map, suggest_format,
)
from deforestation_alerts.project import paths
from deforestation_alerts.project.models import PRESETS, BandMap, CustomSource, mapped_bands
from deforestation_alerts.project.names import name_from_ref, suggest_name, validate_source_name
from gui.i18n import t
from gui.scripts import aoi_io
from gui.scripts.coverage_ui import note_text
from gui.scripts.error_text import error_text
from gui.scripts.remote_facts import aoi_origin
from gui.widget.default_name import use_default_name
from gui.widget.form_dialog import SubmitTaskButton, form_card, use_form_dialog
from gui.widget.raster_input import RasterInput, absolute, band_index, use_band_list
from gui.widget.text_style import MUTED

FORMATS = ("radd", "ccdc", "date_only")
ENCODINGS = ("yymm", "yyddd", "yyyyddd", "decimal_year")
MAX_CUSTOM = 6


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
    fmt, set_fmt = solara.use_state(src0.format if src0 else None)
    encoding, set_encoding = solara.use_state(src0.date_encoding if src0 and src0.format == "date_only" else "yymm")
    alert_band, set_alert_band = solara.use_state(src0.band_map.alert if src0 else None)
    date_band, set_date_band = solara.use_state(src0.band_map.date if src0 else None)
    error, set_error = solara.use_state(None)
    submitted = solara.use_ref(False)  # one Add per opening, held through the validation

    ref = (asset.value or {}).get("asset_id") if origin == "gee" else absolute(path.value)
    asset_type = (asset.value or {}).get("type") if origin == "gee" else "FILE"
    bands_task = use_band_list(gee_interface, origin, ref, asset_type)
    use_default_name(
        ref if mode == "custom" and src0 is None else None, name, set_name,
        lambda r: suggest_name(name_from_ref(r), lambda n: validate_source_name(n, app_state.project.value) is None))
    names, header = bands_task.value if bands_task.finished and bands_task.value else ((), None)
    band_items = ([{"text": b.name, "value": b.index} for b in header.bands] if header is not None
                  else [{"text": n, "value": n} for n in names])

    def _suggest():
        if not names or (src0 is not None and ref == src0.ref):
            return  # an edit keeps its own mapping until the raster changes
        suggested = suggest_format(names)
        set_fmt(suggested)
        bm = suggest_band_map(suggested, names) if suggested else None
        if header is not None and bm is not None:  # GeoTIFF: band indexes, not names
            index = {b.name: b.index for b in header.bands}
            bm = BandMap(alert=index.get(bm.alert), date=index[bm.date]) if bm.date in index else None
        set_alert_band(bm.alert if bm else None)
        set_date_band(bm.date if bm else None)

    # ``header`` too: an asset and a file with the same band names map by name
    # and by index respectively
    solara.use_effect(_suggest, [names, header])

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
            external_busy=waiting or (mode == "predefined" and key is None)
            or (mode == "custom" and bool(name_error)),
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
            RasterInput(gee_interface=gee_interface, origin=origin, on_origin=set_origin, asset=asset, path=path,
                        disabled=busy)
            if asset_type == "IMAGE_COLLECTION":
                solara.Text(t("steps.alerts.collection_help"), style=MUTED)
            if bands_task.pending:
                solara.Text(t("steps.raster_input.bands_loading"), style=MUTED)
            elif bands_task.error:
                solara.Text(t("steps.raster_input.bands_failed", error=error_text(bands_task.exception)),
                            classes=["error--text"])
            rv.Select(label=t("steps.alerts.format_label"), v_model=fmt, on_v_model=set_fmt, dense=True,
                      outlined=True, disabled=busy,
                      items=[{"text": t(f"steps.alerts.format_{f}"), "value": f} for f in FORMATS],
                      hint=t(f"steps.alerts.format_help_{fmt}") if fmt else "", persistent_hint=bool(fmt))
            if fmt in ("radd", "ccdc"):
                rv.Select(label=t("steps.alerts.band_alert"), items=band_items, v_model=alert_band,
                          on_v_model=set_alert_band, dense=True, outlined=True, disabled=busy)
            if fmt is not None:
                rv.Select(label=t("steps.alerts.band_date"), items=band_items, v_model=date_band,
                          on_v_model=set_date_band, dense=True, outlined=True, disabled=busy)
            if fmt == "date_only":
                rv.Select(label=t("steps.alerts.encoding_label"), v_model=encoding, on_v_model=set_encoding,
                          dense=True, outlined=True, disabled=busy,
                          items=[{"text": t(f"steps.alerts.encoding_{e}"), "value": e} for e in ENCODINGS])
