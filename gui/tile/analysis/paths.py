"""Shared output-path builders for the analysis tile sub-components.

The before/after composite GeoTIFF name is built by BOTH the CNN run
(``edit_tools``) and the export download (``export``), and they must resolve to
the SAME cached file — the export step reuses the tif the CNN run already
downloaded. Keeping the construction here once removes the drift risk of two
byte-identical string builds living in separate modules.
"""


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
