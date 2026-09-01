"""Shared output-path builders for the analysis tile sub-components.

The before/after composite GeoTIFF name is built by BOTH the CNN run
(``edit_tools``) and the export download (``export``), and they must resolve to
the SAME cached file — the export step reuses the tif the CNN run already
downloaded. Keeping the construction here once removes the drift risk of two
byte-identical string builds living in separate modules.

Since Phase 6 the recipe folder is a REMOTE path in the user's SEPAL workspace,
so "the same cached file" is a remote one and the cache probe is
``workspace.exists``. geedim and rasterio only speak local paths, though, so
:func:`stage_alert_image` owns the whole exists / download / upload dance for
both callers (inventory rows a21 + c14).
"""

from pathlib import PurePosixPath

from deforestation_alerts.scripts.mosaics_helper import download_both_images


def alert_image_name(recipe_folder, alert_id, before_info_list, after_info_list):
    """Build the before/after composite GeoTIFF path for one alert.

    Mirrors the OLD god-class: ``<recipe>/alert_<id><before[4]><after[4]>.tif``
    where ``*_info_list[4]`` is each selected image's date suffix (e.g. ``_010124``).
    """
    return (
        recipe_folder
        + "/alert_"
        + str(alert_id)
        + before_info_list[4]
        + after_info_list[4]
        + ".tif"
    )


def stage_alert_image(
    workspace,
    recipe_folder,
    alert_id,
    before_info_list,
    after_info_list,
    image_before,
    image_after,
    region,
):
    """Return ``(local_tif, remote_tif)`` for one alert's before/after composite.

    Already in the recipe folder -> staged back down so rasterio/zipfile can read
    it. Missing -> downloaded to scratch by geedim and uploaded, so the next CNN
    run or export hits the cache. Without a recipe folder (smoke / pre-recipe)
    there is nowhere in the workspace to cache it: the composite is built in
    scratch and ``remote_tif`` is ``None``.

    The caller owns ``local_tif`` and must ``workspace.discard_scratch`` it.
    """
    remote_tif = (
        alert_image_name(recipe_folder, alert_id, before_info_list, after_info_list)
        if recipe_folder
        else None
    )
    if remote_tif and workspace.exists(remote_tif):
        return workspace.download_to_scratch(remote_tif), remote_tif

    name = (
        PurePosixPath(remote_tif).name
        if remote_tif
        else f"alert_{alert_id}{before_info_list[4]}{after_info_list[4]}.tif"
    )
    local_tif = workspace.scratch_file(name=name)
    download_both_images(
        image_before,
        image_after,
        str(local_tif),
        before_info_list[0],
        after_info_list[0],
        region,
    )
    if remote_tif:
        workspace.upload_from_scratch(local_tif, remote_tif)
    return local_tif, remote_tif
