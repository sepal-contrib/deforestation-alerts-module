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
from deforestation_alerts.scripts.scene_index import SOURCE_CODE


def _source_slug(info) -> str:
    """A short, filename-safe tag for the collection one side was picked from.

    ``SOURCE_CODE`` is the same one-letter code ``scene_query.row_id`` uses
    (``s`` / ``l`` / ``p``). A source outside it falls back to its own letters
    and digits instead of raising: this only has to tell the sources apart,
    and a KeyError here would lose a CNN run or a download.
    """
    source = str(info[0])
    code = SOURCE_CODE.get(source)
    if code:
        return code
    return "".join(ch for ch in source.lower() if ch.isalnum()) or "x"


def alert_image_name(recipe_folder, alert_id, before_info, after_info):
    """Build the before/after composite GeoTIFF path for one alert.

    ``<recipe>/alert_<id>_<src><before[4]>_<src><after[4]>.tif``, where
    ``*_info[4]`` is each selected image's date suffix (e.g. ``_010124``) and
    ``<src>`` its collection's code.

    The OLD god-class name carried the dates alone
    (``alert_<id><before[4]><after[4]>.tif``), and for one alert the dates are
    not a key: Sentinel 2, Landsat and Planet all have imagery for the same
    day. Switching one side to another collection then resolved to the file
    the first pick had already downloaded, which ``stage_alert_image`` hands
    back as a cache hit -- the reviewer analysed, exported and reported the
    wrong collection's imagery under the right dates. Source + date IS a key:
    for a given alert they determine the granules the mosaic is built from.
    """
    return (
        recipe_folder
        + "/alert_"
        + str(alert_id)
        + "_"
        + _source_slug(before_info)
        + before_info[4]
        + "_"
        + _source_slug(after_info)
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


def alert_gif_name(recipe_folder, alert_id, before_info, after_info):
    """The alert's GIF, beside its composite and named like it (spec D10):
    the same stem, so the two are found together in the set folder."""
    return alert_image_name(recipe_folder, alert_id, before_info, after_info)[: -len(".tif")] + ".gif"
