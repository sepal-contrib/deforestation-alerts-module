"""Attribute raster controls (spec §7.4 form), on the shared raster input.

Pick an Earth Engine image or a GeoTIFF (``RasterInput``), load its bands,
then choose per band whether to include it, which statistic, and the output
column. Emits the config dict ``raster_attribute.build_attribute_config``
builds, or ``None`` once removed. An Earth Engine band keeps its ``crs`` and
``scale`` (metres, ``nominalScale``): Integrate's free-space gate estimates
the band's download from them.

The rows describe the raster their bands were loaded from, not whatever the
pickers show now: picking another source changes nothing until its bands are
loaded, so the config never pairs one raster with another's bands.

The column field shows the sanitised name being written; when the
de-duplication against the alert columns and earlier bands changes it, its
hint says what is written instead.
"""

import asyncio

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.task_button import TaskButtonComponent, use_task_button

from deforestation_alerts.engine import user_raster
from deforestation_alerts.scripts.raster_attribute import (
    band_names,
    build_attribute_config,
    display_column,
)
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.widget.raster_input import RasterInput, absolute

_STATS = ["majority", "mean", "min", "max", "median"]
_ROW = "display:flex;gap:8px;align-items:center;"
#: the config's ``kind`` -> RasterInput's origin
_ORIGIN = {"gee": "gee", "local": "file"}
_KIND = {origin: kind for kind, origin in _ORIGIN.items()}


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
        {"include": True, "band": b["band"], "stat": b["stat"], "column": b["column"],
         **{k: b[k] for k in ("crs", "scale") if k in b}}
        for b in cfg.get("bands", [])
    ])
    error, set_error = solara.use_state(None)

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
        new_rows = [{"include": True, "band": n, "stat": "majority", "column": n,
                     **(projections.get(n) or {})} for n in names]
        set_error(None)
        set_rows(new_rows)
        set_loaded((src, src_kind))
        on_value(build_attribute_config(src, src_kind, new_rows))

    load_task = solara.lab.use_task(_load, dependencies=None, prefer_threaded=False, raise_error=False)

    def _clear():
        set_error(None)
        set_rows([])
        set_loaded(None)
        on_value(None)

    def _edit(i, **changes):
        new_rows = [dict(r, **changes) if j == i else r for j, r in enumerate(rows)]
        set_rows(new_rows)
        on_value(build_attribute_config(loaded[0], loaded[1], new_rows))

    # what each included band is written as, after the de-duplication
    resolved = {b["band"]: b["column"] for b in (value or {}).get("bands", [])}

    with rv.Html(tag="div", style_="display:flex;flex-direction:column;gap:8px;"):
        RasterInput(gee_interface=gee_interface, origin=_ORIGIN[kind], on_origin=lambda o: set_kind(_KIND[o]),
                    asset=asset, path=path, asset_types=("IMAGE",))
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
                shown = display_column(r["column"])
                written = resolved.get(r["band"])
                renamed = written is not None and written != shown
                rv.TextField(v_model=shown, on_v_model=lambda v, i=i: _edit(i, column=v), dense=True,
                             hint=t("steps.integrate.attr_written_as", column=written) if renamed else "",
                             persistent_hint=renamed, hide_details=not renamed, style_="max-width:160px")
