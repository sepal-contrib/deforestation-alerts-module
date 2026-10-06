"""Visualization layers (spec §6.3): Earth Engine images, GeoTIFFs and Planet
NICFI, on the shared map and the Analysis maps. Builders are blocking (map
ids, tile servers): call them from a worker thread (MapLayers does).

What a builder raises ``MapLayers`` shows as the toast (``error_text``), so
it raises keyed errors and never translates on its worker (spec §9.1, §11: a
GeoTIFF in a folder that is not writable is refused with a toast naming the
folder).
"""

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from deforestation_alerts.engine.raster_io import ensure_overviews
from deforestation_alerts.errors import FolderNotWritable, UserFileMissing
from gui.i18n import t
from gui.scripts import legend_specs, raster_map
from gui.scripts.legend_data import format_number

NICFI_COLLECTIONS = {
    "americas": "projects/planet-nicfi/assets/basemaps/americas",
    "africa": "projects/planet-nicfi/assets/basemaps/africa",
    "asia": "projects/planet-nicfi/assets/basemaps/asia",
}
NICFI_VIS = {"bands": ["R", "G", "B"], "min": 0, "max": 1600}  # the Analysis maps' Planet true colour
KINDS = ("gee", "file", "nicfi")
#: the colormaps a GeoTIFF layer offers (matplotlib names)
COLORMAPS = ("viridis", "magma", "Greens", "YlOrBr", "terrain", "gray")
MAX_NAME = 24


def nicfi_region(bounds) -> str:
    """Americas west of 30°W, Asia east of 60°E, else Africa: from the AOI centroid."""
    lon = (bounds[0] + bounds[2]) / 2
    return "americas" if lon < -30 else "asia" if lon > 60 else "africa"


def region_label(region: str) -> str:
    return t(f"steps.layers.region_{region}")


def period_month(project, today: date) -> str:
    """The active period's end month, else last month: the month of a NICFI
    layer without one of its own."""
    period = project.periods.get(project.active_period) if project.active_period else None
    if period is not None:
        return period.end.strftime("%Y-%m")
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def nicfi_month(layer, project, today: date) -> str:
    """The layer's month, else the active period's end month, else last month."""
    return layer.month or period_month(project, today)


def nicfi_image(region: str, month: str):
    import ee

    start = ee.Date(f"{month}-01")
    return ee.ImageCollection(NICFI_COLLECTIONS[region]).filterDate(start, start.advance(1, "month")).first()


def nicfi_access_request(region: str):
    """The number of images the user can read in the region's collection (0 or
    an Earth Engine error: no access)."""
    import ee

    return ee.ImageCollection(NICFI_COLLECTIONS[region]).limit(1).size()


def ee_vis_params(layer) -> dict:
    vis = {"bands": list(layer.bands), "min": float(layer.min), "max": float(layer.max)}
    if layer.palette and len(layer.bands) == 1:
        vis["palette"] = list(layer.palette)
    return vis


def folder_writable(path) -> bool:
    """Whether the ``.ovr`` sidecar of ``path`` can be written next to it."""
    return os.access(Path(path).parent, os.W_OK)


def validate_name(name, project, current: Optional[str] = None) -> Optional[str]:
    """None, or the message key of the first problem with the layer's name."""
    text = str(name or "").strip()
    if not text:
        return "steps.layers.name_empty"
    if len(text) > MAX_NAME:
        return "steps.layers.name_too_long"
    if text.casefold() in {n.casefold() for n in project.vis_layers if n != current}:
        return "steps.layers.name_duplicate"
    return None


def vis_layers_summary(layer) -> str:
    """The layer's details note: its bands or band and colormap and its range,
    or the NICFI month."""
    if layer.kind == "nicfi":
        return layer.month or t("steps.layers.nicfi_period_month")
    span = f"{format_number(layer.min)}–{format_number(layer.max)}"
    if layer.kind == "gee":
        return f"{', '.join(layer.bands)} · {span}"
    return f"{t('steps.layers.summary_band', band=layer.band)} · {layer.colormap} · {span}"


def refresh_nicfi_layers(layers, map_, project, *, today: date, notifier=None) -> None:
    """Give every NICFI layer on the map that draws the active period's month
    (no month of its own) the current one, image and legend. A layer is shown
    with its month as content id, so ``refresh`` rebuilds only when the month
    changed."""
    if project.aoi is None:
        return
    month = period_month(project, today)
    for key in sorted(layers.on.value):
        layer = project.vis_layers.get(key[4:]) if key.startswith("vis:") else None
        if layer is None or layer.kind != "nicfi" or layer.month:
            continue
        layers.refresh(key, builder(map_, layer, key, project=project, aoi_bounds=project.aoi.bounds), month,
                       legend=legend_specs.vis_legend(key, layer, month=month), notifier=notifier)


def builder(map_, layer, key: str, *, project, aoi_bounds, shown: bool = True):
    """The blocking build of ``layer`` under ``key`` (MapLayers runs it on a
    worker). ``shown=False`` adds it unticked (the Analysis maps)."""

    def build():
        import ee
        from matplotlib import colormaps

        if layer.kind == "gee":
            img = ee.Image(layer.asset).select(list(layer.bands))
            map_.add_ee_layer(img, ee_vis_params(layer), name=layer.name, key=key, shown=shown)
        elif layer.kind == "nicfi":
            month = nicfi_month(layer, project, date.today())
            img = nicfi_image(nicfi_region(aoi_bounds), month)
            map_.add_ee_layer(img, dict(NICFI_VIS), name=layer.name, key=key, shown=shown)
        else:
            if not Path(layer.path).exists():
                raise UserFileMissing(f"file not found: {layer.path}", key="steps.layers.err_missing", path=layer.path)
            if not folder_writable(layer.path):
                folder = str(Path(layer.path).parent)
                raise FolderNotWritable(f"the folder {folder} is not writable", key="steps.layers.err_folder",
                                        folder=folder)
            ensure_overviews(layer.path, resampling="nearest")
            raster_map.add_raster_layer(map_, layer.path, key=key, name=layer.name, band=int(layer.band),
                                        colormap=colormaps[layer.colormap], vmin=layer.min, vmax=layer.max,
                                        nodata=None, visible=shown)

    return build
