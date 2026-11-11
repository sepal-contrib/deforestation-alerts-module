"""The vector-file part of the Add Source form (spec §5): a CSV, GeoPackage
or shapefile of dated points or polygons. Its check before the source is
added, the read that gives the form its columns and summary, the fields
themselves and ``use_vector_fields``, the block ``source_modal`` draws below
the picker in place of its raster block."""

import asyncio
from types import SimpleNamespace

import reacton.ipyvuetify as rv
import solara

from deforestation_alerts.engine.vector_input import (
    ERR_DATES, ERR_NO_FEATURES, VectorInputError, driver_of, list_layers, read_table, suggest_columns, summarize,
)
from deforestation_alerts.project.models import BandMap, VectorSpec
from deforestation_alerts.project.names import validate_source_name
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.widget.text_style import MUTED

ERR_PICK = "steps.alerts.err_vector_pick"  # a column not picked yet (a picked one missing is err_vector_columns)
DATE_ORDER_CHOICES = ("dmy", "mdy")  # what the user picks for an ambiguous day/month column
SIDE_DEFAULT = "30"  # metres (Global Constraints)


def vector_summary(gdf, date_col, date_order):
    """``(summary, order)``: the file under ``date_col``, read with the detected
    order or, for an ambiguous day/month column, with the user's ``date_order``
    (None until they pick one). A stale pick never overrides a detected order."""
    summary = summarize(gdf, date_col)
    reading = summary.reading
    if reading.ambiguous and date_order:
        return summarize(gdf, date_col, date_order), date_order
    return summary, reading.order


def _side(value):
    try:
        side = float(value)
    except (TypeError, ValueError):
        return None
    return side if side > 0 and side != float("inf") else None


def _disjoint(a, b) -> bool:
    return a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1]


async def validate_vector(project, *, name, ref, layer, lon, lat, date_col, date_order, side_m,
                          editing=None) -> dict:
    """Check a points or polygons file before it is added or saved. Local
    only: no Earth Engine request, and no coverage record (the AOI-raster clip
    at harmonize drops what falls outside), so ``coverage`` is None."""
    name = (name or "").strip()
    error = validate_source_name(name, project, current=editing)
    if error:
        return {"error": error}
    if not ref:
        return {"error": "steps.alerts.err_no_ref"}
    try:  # the file first: an unreadable one says so, whatever was picked
        driver = driver_of(ref)
        csv = driver == "csv"
        if driver == "gpkg" and not layer:  # stored by name: a layer added later cannot take its place
            layer = next(iter(await asyncio.to_thread(list_layers, ref)), None)
        gdf = await asyncio.to_thread(read_table, ref, layer=layer, lon=lon if csv else None,
                                      lat=lat if csv else None)
        if not date_col or (csv and not (lon and lat)):
            return {"error": ERR_PICK}
        summary, order = await asyncio.to_thread(vector_summary, gdf, date_col, date_order)
    except VectorInputError as exc:
        return {"error": exc.key}
    reading = summary.reading
    if reading.conflict or not reading.valid:
        return {"error": ERR_DATES}
    if order is None:  # ambiguous, and no order picked yet
        return {"error": "steps.alerts.err_vector_date_order"}
    if not (summary.points or summary.polygons):
        return {"error": ERR_NO_FEATURES}
    side = _side(side_m) if summary.points else None
    if summary.points and side is None:
        return {"error": "steps.alerts.err_vector_side"}
    if summary.bounds is None or _disjoint(summary.bounds, project.aoi.bounds):
        return {"error": "steps.alerts.err_not_covered"}
    spec = VectorSpec(driver=driver, layer=layer, lon=lon if csv else None, lat=lat if csv else None,
                      date=date_col, date_order=order, side_m=side)
    fields = dict(name=name, origin="vector", ref=ref, asset_type="FILE", format="date_only",
                  date_encoding="yyyyddd", band_map=BandMap(alert=None, date=date_col), native=None, vector=spec)
    return {"fields": fields, "coverage": None}


def use_vector_table(ref, layer, lon, lat, date_col, date_order):
    """The picked vector file as the form shows it: ``task.value`` is a dict of
    its ``driver``, ``layers``, the ``layer`` read, ``columns``, ``summary``
    and ``order`` (both None without a date column in the file). Read in a thread; the dependencies are the form's own
    picks, set in its handlers on the loop alone (``use_band_list``'s rule)."""

    def _read():
        driver = driver_of(ref)
        layers = list_layers(ref)
        # a layer left from another GeoPackage is not this one's: its first instead
        use = layer if layer in layers else (layers[0] if len(layers) > 1 else None)
        bare = read_table(ref, layer=use)
        columns = tuple(c for c in bare.columns if isinstance(c, str) and c != bare.geometry.name)
        gdf = bare
        if driver == "csv" and lon in columns and lat in columns:
            gdf = read_table(ref, lon=lon, lat=lat)
        summary, order = vector_summary(gdf, date_col, date_order) if date_col in columns else (None, None)
        return {"driver": driver, "layers": layers, "layer": use, "columns": columns, "summary": summary, "order": order}

    async def _load():
        if not ref:
            return None
        return await asyncio.to_thread(_read)

    return solara.lab.use_task(_load, dependencies=[ref, layer, lon, lat, date_col, date_order],
                               prefer_threaded=False, raise_error=False)


def use_vector_fields(src0, ref):
    """Hook: the custom form's block for a points or polygons file, everything
    below the picker, as ``source_modal.use_raster_fields`` is for a raster.

    It reads the picked file (``use_vector_table``) and holds the layer, the
    coordinate and date columns, the day/month order and the square side. The
    result carries ``validate(project, name, editing)``, ``save_blocked``
    (the file is being read) and ``draw(busy)``, a plain function that draws
    the block into the caller's container. With no file (``ref`` None, as for
    another origin) the read stays idle and ``draw`` draws nothing.

    A newly read file starts on its suggested columns; an edit (``src0``)
    keeps its saved picks until the file changes."""
    vec0 = src0.vector if src0 is not None else None
    layer, set_layer = solara.use_state(vec0.layer if vec0 else None)
    lon, set_lon = solara.use_state(vec0.lon if vec0 else None)
    lat, set_lat = solara.use_state(vec0.lat if vec0 else None)
    date_col, set_date_col = solara.use_state(vec0.date if vec0 else None)
    date_order, set_date_order = solara.use_state(vec0.date_order if vec0 else None)
    side, set_side = solara.use_state(f"{vec0.side_m:g}" if vec0 and vec0.side_m else SIDE_DEFAULT)
    suggested_for = solara.use_ref(None)  # the (file, columns) the picks were last suggested for
    task = use_vector_table(ref, layer, lon, lat, date_col, date_order)
    info = task.value if task.finished and task.value else None
    shown = solara.use_ref(None)  # the last file read: the selects keep their items while a pick re-reads
    if info is not None:
        shown.current = info
    elif not ref or task.error:
        shown.current = None  # no file, or one that cannot be read: nothing of the previous one stays

    def _suggest():
        if info is None:
            return
        columns = info["columns"]
        if layer != info["layer"]:
            # the layer read (the first of several, named; None for one): its columns are these
            set_layer(info["layer"])
        if suggested_for.current == (ref, columns):
            return  # a pick re-read the same file: the user's picks stay
        suggested_for.current = (ref, columns)
        if vec0 is not None and ref == src0.ref:
            return  # an edit keeps its own picks until the file changes
        picks = suggest_columns(columns)
        set_lon(picks["lon"])
        set_lat(picks["lat"])
        set_date_col(picks["date"])
        set_date_order(None)

    solara.use_effect(_suggest, [info])

    async def validate(project, name, editing):
        return await validate_vector(project, name=name, ref=ref, layer=layer, lon=lon, lat=lat, date_col=date_col,
                                     date_order=date_order, side_m=side, editing=editing)

    def draw(busy):
        if ref:
            VectorFields(task=task, info=shown.current, layer=layer, set_layer=set_layer, lon=lon, set_lon=set_lon,
                         lat=lat, set_lat=set_lat, date_col=date_col, set_date_col=set_date_col,
                         date_order=date_order, set_date_order=set_date_order, side=side, set_side=set_side,
                         disabled=busy)

    return SimpleNamespace(validate=validate, save_blocked=bool(ref) and task.pending, draw=draw)


def summary_text(info) -> str:
    """The muted line under the vector fields, or "" until dates are read."""
    summary = (info or {}).get("summary")
    if summary is None or summary.date_min is None:
        return ""
    return t("steps.alerts.vector_summary", order=t(f"steps.alerts.vector_order_{info['order']}"),
             points=summary.points, polygons=summary.polygons, first=summary.date_min.isoformat(),
             last=summary.date_max.isoformat(), skipped=summary.skipped)


@solara.component
def VectorFields(task, info, layer, set_layer, lon, set_lon, lat, set_lat, date_col, set_date_col,
                  date_order, set_date_order, side, set_side, disabled):
    """A vector file's picks (spec §5.2): its layer, coordinate and date
    columns, the day/month order of an ambiguous column, the square side of
    its points, and what the file holds under them."""
    if task.pending:
        solara.Text(t("steps.alerts.vector_reading"), style=MUTED)
    elif task.error:
        solara.Text(error_text(task.exception), classes=["error--text"])
    if info is None:
        return
    columns = [{"text": c, "value": c} for c in info["columns"]]
    common = dict(dense=True, outlined=True, disabled=disabled)
    if len(info["layers"]) > 1:
        rv.Select(label=t("steps.alerts.vector_layer"), items=list(info["layers"]), v_model=layer,
                  on_v_model=set_layer, **common)
    if info["driver"] == "csv":
        rv.Select(label=t("steps.alerts.vector_lon"), items=columns, v_model=lon, on_v_model=set_lon, **common)
        rv.Select(label=t("steps.alerts.vector_lat"), items=columns, v_model=lat, on_v_model=set_lat, **common)
    rv.Select(label=t("steps.alerts.vector_date"), items=columns, v_model=date_col, on_v_model=set_date_col,
              **common)
    summary = info["summary"]
    if summary is not None and summary.reading.ambiguous:
        rv.Select(label=t("steps.alerts.vector_date_order"), v_model=date_order, on_v_model=set_date_order,
                  items=[{"text": t(f"steps.alerts.vector_{o}"), "value": o} for o in DATE_ORDER_CHOICES],
                  **common)
    if summary is not None and summary.points > 0:
        rv.TextField(label=t("steps.alerts.vector_side"), v_model=side, on_v_model=set_side, type="number",
                     hint=t("steps.alerts.vector_side_hint"), persistent_hint=True, **common)
    if summary is not None and (summary.reading.conflict or not summary.reading.valid):
        solara.Text(t(ERR_DATES), classes=["error--text"])
    text = summary_text(info)
    if text:
        solara.Text(text, style=MUTED)
