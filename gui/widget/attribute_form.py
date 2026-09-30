"""Attribute raster controls (spec §7.4 form), rebuilt on pysepal's Solara inputs.

Pick an Earth Engine image or a GeoTIFF, load its bands, then choose per band
whether to include it, which statistic, and the output column. Emits the same
config dict ``raster_attribute.build_attribute_config`` always produced, or
``None`` once removed.

The rows describe the raster their bands were loaded from, not whatever the
pickers show now: picking another source changes nothing until its bands are
loaded, so the config never pairs one raster with another's bands.
"""

import asyncio
from pathlib import Path

import ee
import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.inputs import AssetSelectComponent, FileInputComponent
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button

from deforestation_alerts.scripts.raster_attribute import (
    band_names,
    build_attribute_config,
)
from gui.i18n import t

_STATS = ["majority", "mean", "min", "max", "median"]
_ROW = "display:flex;gap:8px;align-items:center;"


def _absolute(path) -> str | None:
    """A picked file as an absolute path (spec §11: nothing relative to the CWD)."""
    return str(Path(path).expanduser().absolute()) if path else None


@solara.component
def AttributeForm(gee_interface, value, on_value):
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
        {"include": True, "band": b["band"], "stat": b["stat"], "column": b["column"]}
        for b in cfg.get("bands", [])
    ])
    error, set_error = solara.use_state(None)

    def _source():
        if kind == "gee":
            return (asset.value or {}).get("asset_id") or None
        return _absolute(path.value)

    async def _load():
        src, src_kind = _source(), kind
        if not src:
            set_error(t("steps.integrate.attr_no_source"))
            return
        try:
            if src_kind == "gee":
                names = await gee_interface.get_info_async(ee.Image(src).bandNames())
            else:
                names = await asyncio.to_thread(band_names, src)
        except Exception as exc:  # noqa: BLE001 - shown next to the field
            set_error(str(exc) or exc.__class__.__name__)
            return
        new_rows = [{"include": True, "band": n, "stat": "majority", "column": n} for n in names]
        set_error(None)
        set_rows(new_rows)
        set_loaded((src, src_kind))
        on_value(build_attribute_config(src, src_kind, new_rows))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _clear():
        set_rows([])
        set_loaded(None)
        on_value(None)

    def _edit(i, **changes):
        new_rows = [dict(r, **changes) if j == i else r for j, r in enumerate(rows)]
        set_rows(new_rows)
        on_value(build_attribute_config(loaded[0], loaded[1], new_rows))

    with rv.Html(tag="div", style_="display:flex;flex-direction:column;gap:8px;"):
        with rv.RadioGroup(v_model=kind, on_v_model=set_kind, row=True, dense=True, hide_details=True):
            rv.Radio(label=t("steps.integrate.attr_gee"), value="gee")
            rv.Radio(label=t("steps.integrate.attr_file"), value="local")
        if kind == "gee":
            AssetSelectComponent(types=["IMAGE"], value=asset, gee_interface=gee_interface)
        else:
            FileInputComponent(extensions=[".tif", ".tiff"], value=path, label=t("steps.integrate.attr_file"))
        with rv.Html(tag="div", style_=_ROW):
            TaskButtonComponent(label=t("steps.integrate.attr_load"), cancel_label=t("common.cancel"), small=True,
                                **use_task_button(load_task, on_start=lambda: load_task()))
            if value:
                solara.Button(t("steps.integrate.attr_clear"), small=True, text=True, on_click=_clear)
        if error:  # field validation, next to the field (the CreationDialog exception)
            solara.Text(error, classes=["error--text"])
        if loaded and rows:
            solara.Text(t("steps.integrate.attr_loaded", source=loaded[0]), classes=["text--secondary"])
        for i, r in enumerate(rows):
            with rv.Html(tag="div", style_=_ROW):
                rv.Checkbox(v_model=r["include"], on_v_model=lambda v, i=i: _edit(i, include=v),
                            label=r["band"], dense=True, hide_details=True)
                rv.Select(items=[{"text": t(f"steps.integrate.attr_stat_{s}"), "value": s} for s in _STATS],
                          v_model=r["stat"], on_v_model=lambda v, i=i: _edit(i, stat=v), dense=True,
                          hide_details=True, style_="max-width:130px")
                rv.TextField(v_model=r["column"], on_v_model=lambda v, i=i: _edit(i, column=v), dense=True,
                             hide_details=True, style_="max-width:160px")
