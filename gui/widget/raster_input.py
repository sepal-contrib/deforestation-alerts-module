"""The shared user-raster input (spec §6.2): an Earth Engine image or image
collection, or a GeoTIFF in the user's SEPAL folder, and the list of its
bands. Used by the source and mask modals, the visualization-layer modal and
the attribute raster."""

import asyncio
from pathlib import Path
from typing import Callable, Optional

import reacton.ipyvuetify as rv
import solara
from pysepal.solara.components.inputs import AssetSelectComponent, FileInputComponent

from deforestation_alerts.engine import user_raster
from gui.i18n import t
from gui.scripts.error_text import error_text
from gui.widget.text_style import MUTED

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
    # ``show_origin=False``: the caller picks the origin itself, with a
    # ``SourceSelect`` above the picker
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


@solara.component
def SourceSelect(origin: str, on_origin: Callable[[str], None], disabled: bool = False):
    """The Source of a form that picks a raster: Earth Engine or a GeoTIFF in
    the user's SEPAL folder, above ``RasterInput(..., show_origin=False)``.
    ``on_origin`` gets the new origin; the caller clears the picked raster."""
    rv.Select(label=t("steps.raster_input.field_source"), v_model=origin, on_v_model=on_origin,
              items=[{"text": t("steps.raster_input.source_gee"), "value": "gee"},
                     {"text": t("steps.raster_input.source_file"), "value": "file"}],
              hint=t("steps.raster_input.source_hint"), persistent_hint=True, dense=True, outlined=True,
              disabled=disabled)


def BandStatus(task) -> None:
    """Where a form's band list stands, under the picker: "Reading the bands…"
    while ``task`` runs, its error once it fails, nothing otherwise.

    A plain function drawn into the caller's container (no wrapper, no
    component of its own). It reads only ``task.pending``, ``task.error`` and
    ``task.exception``, so it serves any ``solara.lab.use_task`` result, not
    only ``use_band_list``'s; ``bands_read`` says when that one's bands are in."""
    if task.pending:
        solara.Text(t("steps.raster_input.bands_loading"), style=MUTED)
    elif task.error:
        solara.Text(t("steps.raster_input.bands_failed", error=error_text(task.exception)),
                    classes=["error--text"])


def bands_read(task) -> bool:
    """Whether ``use_band_list``'s task has read a non-empty band list.

    The task restarts in an effect, so in the render where the picked raster
    changes it still holds the previous raster's list: a caller that must show
    nothing once the raster is cleared also checks its ref."""
    return bool(task.finished and not task.error and task.value and task.value[0])


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
