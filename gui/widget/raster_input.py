"""The shared user-raster input (spec §6.2): an Earth Engine image or image
collection, or a GeoTIFF in the user's SEPAL folder, and the list of its
bands. Used by the source and mask modals, the visualization-layer modal and
the attribute raster."""

import asyncio
from pathlib import Path
from typing import Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.inputs import AssetSelectComponent, FileInputComponent

from deforestation_alerts.engine import user_raster
from gui.i18n import t

EXTENSIONS = [".tif", ".tiff"]


def band_index(band) -> Optional[int]:
    """A GeoTIFF band as its 1-based index; None for a band name (left over
    from an Earth Engine asset picked before the file)."""
    if isinstance(band, int) or (isinstance(band, str) and band.isdigit()):
        return int(band)
    return None


def absolute(path) -> Optional[str]:
    """A picked file as an absolute path (spec §11)."""
    return str(Path(path).expanduser().absolute()) if path else None


@solara.component
def RasterInput(gee_interface, origin, on_origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                disabled=False, show_origin=True):
    # ``show_origin=False``: the caller picks the origin itself (the
    # visualization-layer modal's Kind select)
    if show_origin:  # an outlined field like the others in the form (spec D2), not radios
        rv.Select(label=t("steps.raster_input.origin_label"), v_model=origin, on_v_model=on_origin,
                  items=[{"text": t("steps.raster_input.origin_gee"), "value": "gee"},
                         {"text": t("steps.raster_input.origin_file"), "value": "file"}],
                  dense=True, outlined=True, hint=t("steps.raster_input.origin_hint"), persistent_hint=True,
                  disabled=disabled)
    # pysepal's pickers take no ``disabled``: a wrapper blocks their pointer
    # events instead. Always the same wrapper, so toggling it never remounts
    # a picker (which would list the user's assets again).
    with rv.Html(tag="div", style_="pointer-events:none;opacity:0.6;" if disabled else ""):
        if origin == "gee":
            AssetSelectComponent(types=list(asset_types), value=asset, gee_interface=gee_interface)
        else:
            FileInputComponent(extensions=EXTENSIONS, value=path, label=t("steps.raster_input.file"))


def use_band_list(gee_interface, origin, ref, asset_type):
    """The bands of the picked raster: ``task.value == (names, header)``.

    Its dependencies are the input's own values, which no worker publish
    changes, so the task never restarts off the event loop. They are plain,
    not ``use_loop_dependencies`` (gui/scripts/solara_threads.py): safe only
    while the origin and the picked raster move in the pickers' handlers on
    the loop alone. Should a worker ever publish them, wrap them in that
    hook, or the task starts in the worker's render and raises "no running
    event loop"."""

    async def _load():
        if not ref:
            return (), None
        if origin == "gee":
            names = await gee_interface.get_info_async(user_raster.band_list_request(ref, asset_type))
            return tuple(names), None
        header = await asyncio.to_thread(user_raster.read_header, ref)
        return tuple(b.name for b in header.bands), header

    return solara.lab.use_task(_load, dependencies=[origin, ref, asset_type], prefer_threaded=False,
                               raise_error=False)
