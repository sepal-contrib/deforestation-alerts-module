"""One attribute layer's controls (spec §7.4 form, D3), on the shared raster input.

Pick an Earth Engine image or a GeoTIFF (``RasterInput``), load its bands,
then name the layer, select its bands and choose a statistic per band. Emits the config dict ``raster_attribute.build_attribute_config``
builds. ``gui/widget/attribute_modal`` hosts the form, and Integrate lists the
layers. An Earth Engine band keeps its ``crs`` and ``scale`` (metres,
``nominalScale``): Integrate's free-space gate estimates the band's download
from them.

The rows describe the raster their bands were loaded from, not whatever the
pickers show now: picking another source changes nothing until its bands are
loaded, so the config never pairs one raster with another's bands.

A layer has a name; its alert columns are ``<name>_<band>``, shown read-only
beside each selected band. They are de-duplicated against ``reserved`` (the
alert columns and the other layers' columns, ``raster_attribute.reserved_for``)
and the earlier bands.
A layer saved before layers had names keeps its columns until it is given one.
A freshly loaded layer starts with no band selected and a name filled from its
raster; editing keeps the saved selection and name.
"""

import asyncio
from pathlib import Path

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button

from deforestation_alerts.engine import user_raster
from deforestation_alerts.project.names import MAX_NAME, name_from_ref, suggest_name
from deforestation_alerts.scripts.raster_attribute import (
    ALERT_RESERVED_COLUMNS,
    band_names,
    build_attribute_config,
)
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.widget.default_name import use_default_name
from gui.widget.raster_input import RasterInput, absolute
from gui.widget.text_style import MUTED

_STATS = ["majority", "mean", "min", "max", "median"]
_ROW = "display:flex;gap:8px;align-items:center;"
#: the config's ``kind`` -> RasterInput's origin
_ORIGIN = {"gee": "gee", "local": "file"}
_KIND = {origin: kind for kind, origin in _ORIGIN.items()}


def layer_label(cfg) -> str:
    """A layer by its name; one saved without a name by its raster: a GeoTIFF
    by its file name, an Earth Engine asset id (dots included) as is."""
    cfg = cfg or {}
    if cfg.get("name"):
        return cfg["name"]
    src = str(cfg.get("source") or "")
    return Path(src).name if cfg.get("kind") == "local" else src


@solara.component
def AttributeForm(gee_interface, value, on_value, reserved=ALERT_RESERVED_COLUMNS, on_busy=None):
    cfg = value or {}
    gee_src = cfg.get("source") if cfg.get("kind") == "gee" else None
    local_src = cfg.get("source") if cfg.get("kind") == "local" else None
    kind, set_kind = solara.use_state(cfg.get("kind", "gee"))
    asset = solara.use_reactive({"asset_id": gee_src, "type": None, "column": "ALL", "value": None}
                                if gee_src else None)
    path = solara.use_reactive(local_src or "")
    # (source, kind) the rows' bands were read from; None until a load
    loaded, set_loaded = solara.use_state((cfg["source"], cfg["kind"]) if cfg.get("source") else None)
    rows, set_rows = solara.use_state([
        {"include": True, "band": b["band"], "stat": b["stat"], "column": b["column"],
         **{k: b[k] for k in ("crs", "scale") if k in b}}
        for b in cfg.get("bands", [])
    ])
    error, set_error = solara.use_state(None)
    name, set_name = solara.use_state(cfg.get("name", ""))
    fresh, set_fresh = solara.use_state(False)  # bands were loaded in this form: the name follows the raster

    def _emit(new_rows, new_name):
        on_value(build_attribute_config(loaded[0], loaded[1], new_rows, reserved=reserved, name=new_name))

    # the latest name, for the load: its default can land (use_default_name) before the load emits
    latest_name = solara.use_ref(cfg.get("name", ""))

    def _set_name(new):
        latest_name.current = new
        set_name(new)
        if loaded:  # the columns are named after it
            on_value(build_attribute_config(loaded[0], loaded[1], rows, reserved=reserved, name=new))

    use_default_name(loaded[0] if fresh and loaded else None, name, _set_name,
                     lambda r: suggest_name(name_from_ref(r), lambda n: True))

    def _source():
        if kind == "gee":
            return (asset.value or {}).get("asset_id") or None
        return absolute(path.value)

    async def _load():
        src, src_kind = _source(), kind
        if not src:
            set_error(t("steps.integrate.attr_no_source"))
            return
        try:
            if src_kind == "gee":
                names = await gee_interface.get_info_async(user_raster.band_list_request(src, "IMAGE"))
                projections = await gee_interface.get_info_async(user_raster.band_projections_request(src, names))
            else:
                names = await asyncio.to_thread(band_names, src)
                projections = {}
        except Exception as exc:  # noqa: BLE001 - shown next to the field
            set_error(error_text(exc))
            return
        new_rows = [{"include": False, "band": n, "stat": "majority", "column": n,
                     **(projections.get(n) or {})} for n in names]
        set_error(None)
        set_rows(new_rows)
        set_fresh(True)
        set_loaded((src, src_kind))
        on_value(build_attribute_config(src, src_kind, new_rows, reserved=reserved,
                                              name=latest_name.current.strip() or None))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _cancel_on_unmount():
        def cleanup():
            # closing the modal (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a load still running, or it
            # would hand the bands it read to a form the user closed
            if load_task.pending:
                try:
                    load_task.cancel()
                except RuntimeError:
                    pass

        return cleanup

    solara.use_effect(_cancel_on_unmount, [])
    # the host's Save must not fire on the config held before this load
    solara.use_effect(lambda: on_busy(load_task.pending) if on_busy else None, [load_task.pending])

    def _edit(i, **changes):
        new_rows = [dict(r, **changes) if j == i else r for j, r in enumerate(rows)]
        set_rows(new_rows)
        _emit(new_rows, name.strip() or None)

    def _select(bands):
        new_rows = [dict(r, include=r["band"] in bands) for r in rows]
        set_rows(new_rows)
        _emit(new_rows, name.strip() or None)

    # what each included band is written as, after the de-duplication
    resolved = {b["band"]: b["column"] for b in (value or {}).get("bands", [])}

    with rv.Html(tag="div", style_="display:flex;flex-direction:column;gap:8px;"):
        RasterInput(gee_interface=gee_interface, origin=_ORIGIN[kind], on_origin=lambda o: set_kind(_KIND[o]),
                    asset=asset, path=path, asset_types=("IMAGE",))
        with rv.Html(tag="div", style_=_ROW):
            TaskButtonComponent(label=t("steps.integrate.attr_load"), cancel_label=t("common.cancel"), small=True,
                                **use_task_button(load_task, on_start=lambda: load_task()))
        if error:  # a load failure, next to the field it is about (the modal's inline validation)
            solara.Text(error, classes=["error--text"])
        if loaded and rows:
            solara.Text(t("steps.integrate.attr_loaded", source=loaded[0]), style=MUTED)
        if loaded and rows:
            rv.TextField(label=t("steps.integrate.attr_name"), v_model=name,
                         on_v_model=lambda v: _set_name(v[:MAX_NAME]), dense=True, outlined=True,
                         counter=MAX_NAME, hint=t("steps.integrate.attr_name_hint"), persistent_hint=True)
            rv.Select(label=t("steps.integrate.attr_bands"), items=[r["band"] for r in rows],
                      v_model=[r["band"] for r in rows if r["include"]], on_v_model=_select, multiple=True,
                      chips=True, small_chips=True, dense=True, outlined=True, hide_details=True)
        for i, r in enumerate(rows):
            if not r["include"]:
                continue
            with rv.Html(tag="div", style_=_ROW):
                solara.Text(r["band"], style="min-width:90px;")
                rv.Select(items=[{"text": t(f"steps.integrate.attr_stat_{s}"), "value": s} for s in _STATS],
                          v_model=r["stat"], on_v_model=lambda v, i=i: _edit(i, stat=v), dense=True,
                          hide_details=True, style_="max-width:130px")
                solara.Text(resolved.get(r["band"], ""), style=MUTED)
