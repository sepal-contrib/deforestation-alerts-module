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


def absolute(path) -> Optional[str]:
    """A picked file as an absolute path (spec §11)."""
    return str(Path(path).expanduser().absolute()) if path else None


@solara.component
def RasterInput(gee_interface, origin, on_origin, asset, path, asset_types=("IMAGE", "IMAGE_COLLECTION"),
                disabled=False):
    with rv.RadioGroup(v_model=origin, on_v_model=on_origin, row=True, dense=True, hide_details=True,
                       disabled=disabled):
        rv.Radio(label=t("steps.raster_input.origin_gee"), value="gee")
        rv.Radio(label=t("steps.raster_input.origin_file"), value="file")
    if origin == "gee":
        AssetSelectComponent(types=list(asset_types), value=asset, gee_interface=gee_interface)
    else:
        FileInputComponent(extensions=EXTENSIONS, value=path, label=t("steps.raster_input.file"))


def use_band_list(gee_interface, origin, ref, asset_type):
    """The bands of the picked raster: ``task.value == (names, header)``.

    Its dependencies are the input's own values, which no worker publish
    changes, so the task never restarts off the event loop."""

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
