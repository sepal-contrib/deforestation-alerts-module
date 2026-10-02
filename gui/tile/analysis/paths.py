"""Shared output-path builders for the analysis tile sub-components.

The before/after composite GeoTIFF name is built by BOTH the CNN run
(``edit_tools``) and the export download (``export``), and they must resolve to
the SAME file — the export step reuses the tif the CNN run already downloaded.
Keeping the construction here once removes the drift risk of two byte-identical
string builds living in separate modules.

The composite is a file of the set folder: :func:`stage_alert_image` downloads
it there once (through a unique ``.part`` renamed into place) and every later
CNN run or export reads it where it is.
"""

import os
from pathlib import Path

from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.mosaics_helper import download_both_images


def alert_image_name(recipe_folder, alert_id, before_info, after_info):
    """Build the before/after composite GeoTIFF path for one alert.

    Mirrors the OLD god-class: ``<recipe>/alert_<id><before[4]><after[4]>.tif``
    where ``*_info[4]`` is each selected image's date suffix (e.g. ``_010124``).
    """
    return (
        recipe_folder
        + "/alert_"
        + str(alert_id)
        + before_info[4]
        + after_info[4]
        + ".tif"
    )


def stage_alert_image(set_folder, alert_id, before_info, after_info,
                      image_before, image_after, region):
    """The before/after composite of one alert, in the set folder.

    Downloaded once (geedim) into a unique ``.part`` beside it and renamed;
    every later CNN run or export reuses the file. The CNN's prediction raster
    is written next to it by ``apply_dl_model`` (same stem), so both always
    sit in the set folder (slice-1 M31: a first run used to leave them in
    scratch). Raises ``ValueError`` without a set folder.
    """
    if not set_folder:
        raise ValueError("no alert set is active: there is no folder for the alert's images")
    dest = Path(alert_image_name(str(set_folder), alert_id, before_info, after_info))
    if dest.exists():
        return dest
    with local_files.write_into(dest) as part:
        tif = part.with_name(part.stem + ".tif")  # geedim wants a .tif name
        try:
            download_both_images(image_before, image_after, str(tif), before_info[0],
                                 after_info[0], region)
            os.replace(tif, part)
        finally:
            tif.unlink(missing_ok=True)
    return dest
