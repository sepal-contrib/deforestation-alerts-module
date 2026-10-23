"""One attribute layer's controls (spec §7.4 form, D3), on the shared raster input.

The form reads Layer name, Source, the picker (``RasterInput``: an Earth
Engine image or a GeoTIFF), then the bands: they are read as soon as a raster
is picked, and what depends on them waits for them ("Reading the bands…", or
the read's error). One band is a line, two or more a Bands select; each
selected band has a row with its statistic and its alert column. Emits the
config dict ``raster_attribute.build_attribute_config`` builds.
``gui/widget/attribute_modal`` hosts the form, and Integrate lists the layers.
An Earth Engine band keeps its ``crs`` and ``scale`` (metres,
``nominalScale``): Integrate's free-space gate estimates the band's download
from them.

The rows describe the raster their bands were read from, not whatever the
pickers show now: picking another raster, clearing the picker or changing
Source drops them at once and emits ``None`` until the new bands are read (a
failed read leaves it ``None``), so the config never pairs one raster with
another's bands.

A layer has a name; its alert columns are ``<name>_<band>``, shown read-only
beside each selected band. They are de-duplicated against ``reserved`` (the
alert columns and the other layers' columns, ``raster_attribute.reserved_for``)
and the earlier bands. A layer saved before layers had names keeps its
columns until it is given one. A new raster starts on its first band, each
band's statistic by its name (Majority for a band that looks categorical,
else Mean), and a name filled from the raster as soon as it is picked; an
edit keeps the saved selection and name, shows nothing below the picker
until its raster's bands are read, and reading the raster its rows came from
keeps their selection and statistics by band (new bands arrive unselected). A GeoTIFF
band shows its description, else "Band N", but keeps its
``raster_attribute.band_names`` key (``band_N``), so saved layers and their
columns do not change.
"""

import asyncio
from pathlib import Path

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.engine import user_raster
from deforestation_alerts.project.names import MAX_NAME, name_from_ref, suggest_name
from deforestation_alerts.scripts.raster_attribute import (
    ALERT_RESERVED_COLUMNS,
    band_keys_and_texts,
    build_attribute_config,
)
from gui.i18n import t
from gui.scripts.vis_classes import looks_categorical
from gui.widget.default_name import use_default_name
from gui.widget.raster_input import RasterInput, SourceSelect, absolute, band_status
from gui.widget.text_style import MUTED

_STATS = ["majority", "mean", "min", "max", "median"]
_ROW = "display:flex;gap:8px;align-items:center;"
_COLUMN = "display:flex;flex-direction:column;gap:8px;"
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


def other_layer_names(layers, skip=None) -> set:
    """The names (case-folded) of the layers other than ``skip``: a default name
    must not repeat one (a clash gets ``_2``, spec §4)."""
    return {str(layer["name"]).strip().casefold() for i, layer in enumerate(layers or [])
            if i != skip and (layer or {}).get("name")}


def default_stat(text) -> str:
    """A new band's statistic: Majority for a band whose name looks like
    classes (``vis_classes.looks_categorical``), else Mean."""
    return "majority" if looks_categorical(text) else "mean"


def _band_text(row) -> str:
    return row.get("text") or row["band"]


@solara.component
def AttributeForm(gee_interface, value, on_value, reserved=ALERT_RESERVED_COLUMNS, on_busy=None, taken_names=()):
    # the config the form opened with: ``use_reactive`` follows a changed
    # argument, and the emitted ``None`` (a pick waiting for its bands) must
    # not clear the picker
    cfg = solara.use_memo(lambda: value or {}, dependencies=[])
    gee_src = cfg.get("source") if cfg.get("kind") == "gee" else None
    local_src = cfg.get("source") if cfg.get("kind") == "local" else None
    kind, set_kind = solara.use_state(cfg.get("kind", "gee"))
    asset = solara.use_reactive({"asset_id": gee_src, "type": None, "column": "ALL", "value": None}
                                if gee_src else None)
    path = solara.use_reactive(local_src or "")
    # the raster the form opened on, as the pickers name it: a GeoTIFF path
    # expanded and absolute (a legacy config may hold ``~/…``)
    src0 = (absolute(cfg["source"]) if cfg.get("kind") == "local" else cfg["source"]) if cfg.get("source") else None
    # (source, kind) the rows' bands came from: the saved raster on an edit, else
    # None until a read lands
    loaded, set_loaded = solara.use_state((src0, cfg["kind"]) if src0 else None)
    rows, set_rows = solara.use_state([
        {"include": True, "band": b["band"], "stat": b["stat"], "column": b["column"],
         **{k: b[k] for k in ("crs", "scale") if k in b}}
        for b in cfg.get("bands", [])
    ])
    listed, set_listed = solara.use_state(False)  # the rows hold the raster's whole band list
    name, set_name = solara.use_state(cfg.get("name", ""))
    # another raster than the one the form opened on was picked: the name follows the raster
    fresh = solara.use_ref(False)

    def _emit(new_rows, new_name):
        on_value(build_attribute_config(loaded[0], loaded[1], new_rows, reserved=reserved, name=new_name))

    # the latest name, for the read: its default can land (use_default_name) before the read emits
    latest_name = solara.use_ref(cfg.get("name", ""))

    # what the form holds now, for the read (its closure can predate later edits)
    held = solara.use_ref(None)
    held.current = (loaded, rows)

    src = ((asset.value or {}).get("asset_id") or None) if kind == "gee" else absolute(path.value)
    held_here = loaded is not None and loaded == (src, kind)

    def _set_name(new):
        latest_name.current = new
        set_name(new)
        if held_here:  # the columns are named after it (rows of another raster are being dropped)
            _emit(rows, new.strip() or None)

    if src and src != src0:
        fresh.current = True
    # filled on pick, as the mask and visualization forms do; an edit's own
    # raster never fills it (a layer saved without a name keeps its columns)
    use_default_name(src if fresh.current else None, name, _set_name,
                     lambda r: suggest_name(name_from_ref(r), lambda n: n.strip().casefold() not in taken_names))

    def _drop():
        # the rows describe a raster the pickers no longer show: Save waits for the next read
        if held.current[0] is not None:
            set_rows([])
            set_loaded(None)
            set_listed(False)
            on_value(None)

    def _on_pick():
        if loaded is not None and loaded != (src, kind):
            _drop()

    solara.use_effect(_on_pick, [src, kind])

    def _set_origin(origin):
        # another Source clears the picked raster (and so its rows, _on_pick)
        if origin and _KIND[origin] != kind:
            set_kind(_KIND[origin])
            asset.set(None)
            path.set("")

    async def _read():
        # keyed on (kind, src): a new pick cancels a read still running, so a
        # late result never lands on a newer pick
        if not src:
            return None
        try:
            if kind == "gee":
                names = await gee_interface.get_info_async(user_raster.band_list_request(src, "IMAGE"))
                projections = await gee_interface.get_info_async(user_raster.band_projections_request(src, names))
                bands = [(n, n) for n in names]
            else:
                bands = await asyncio.to_thread(band_keys_and_texts, src)
                projections = {}
        except Exception:
            _drop()  # a failed read leaves no config; band_status shows why
            raise
        # reading the raster the rows came from keeps their selection and statistics
        held_loaded, held_rows = held.current
        old = {r["band"]: r for r in held_rows} if held_loaded == (src, kind) else {}
        new_rows = []
        for i, (key, text) in enumerate(bands):
            row = old.get(key)
            include = row["include"] if row else (not old and i == 0)  # a new raster: its first band
            new_rows.append({"include": include or len(bands) == 1, "band": key, "text": text,
                             "stat": row["stat"] if row else default_stat(text),
                             "column": row["column"] if row else key, **(projections.get(key) or {})})
        set_rows(new_rows)
        set_listed(True)
        set_loaded((src, kind))
        on_value(build_attribute_config(src, kind, new_rows, reserved=reserved,
                                        name=latest_name.current.strip() or None))
        return None

    read_task = solara.lab.use_task(_read, dependencies=[kind, src], prefer_threaded=False, raise_error=False)
    reading = bool(src) and read_task.pending

    def _cancel_on_unmount():
        def cleanup():
            # closing the modal (ESC, Cancel) unmounts this form, and use_task
            # does not cancel on unmount: stop a read still running, or it
            # would hand the bands it read to a form the user closed
            if read_task.pending:
                try:
                    read_task.cancel()
                except RuntimeError:
                    pass

        return cleanup

    solara.use_effect(_cancel_on_unmount, [])
    # the host's Save must not fire on the config held before this read
    solara.use_effect(lambda: on_busy(reading) if on_busy else None, [reading])

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
    # nothing below the picker until its bands are read, an edit's saved rows too
    shown = held_here and listed
    selected = [r["band"] for r in rows if r["include"]]

    with rv.Html(tag="div", style_=_COLUMN):
        rv.TextField(label=t("steps.integrate.attr_name"), v_model=name,
                     on_v_model=lambda v: _set_name(v[:MAX_NAME]), dense=True, outlined=True,
                     counter=MAX_NAME, hint=t("steps.integrate.attr_name_hint"), persistent_hint=True)
        SourceSelect(origin=_ORIGIN[kind], on_origin=_set_origin)
        RasterInput(gee_interface=gee_interface, origin=_ORIGIN[kind], on_origin=_set_origin,
                    asset=asset, path=path, asset_types=("IMAGE",), show_origin=False)
        band_status(read_task, src)
        if shown and len(rows) == 1:
            solara.Text(t("steps.integrate.attr_band_one", name=_band_text(rows[0])), style=MUTED)
        elif shown and rows:
            # plain text selection, no chips (the forms' dropdown rule)
            rv.Select(label=t("steps.integrate.attr_bands"),
                      items=[{"text": _band_text(r), "value": r["band"]} for r in rows],
                      v_model=selected, on_v_model=_select, multiple=True, dense=True, outlined=True,
                      hint=t("steps.integrate.attr_bands_pick", count=len(rows)), persistent_hint=True,
                      error_messages=[] if selected else [t("steps.integrate.attr_err_no_bands")])
        for i, r in enumerate(rows if shown else []):
            if not r["include"]:
                continue
            suggested = default_stat(_band_text(r))
            with rv.Html(tag="div", style_=_ROW):
                solara.Text(_band_text(r), style="min-width:90px;")
                rv.Select(items=[{"text": t("steps.layers.suggested", text=t(f"steps.integrate.attr_stat_{s}"))
                                  if s == suggested else t(f"steps.integrate.attr_stat_{s}"), "value": s}
                                 for s in _STATS],
                          v_model=r["stat"], on_v_model=lambda v, i=i: _edit(i, stat=v), dense=True,
                          outlined=True, hide_details=True, style_="max-width:200px")
                solara.Text(resolved.get(r["band"], ""), style=MUTED)
