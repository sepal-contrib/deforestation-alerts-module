"""The radar pair of an alert's files (optical + radar picks spec, 2026-11-09):
the radar composite of the pair on screen at the click (``state.capture``),
pinned or following, and the report's radar captions. The optical files come
from the optical pair, as they always did. Runs on the build's worker.
"""

from pathlib import Path
from typing import Optional

from gui.i18n import t
from gui.tile.analysis.paths import stage_alert_image

#: the caption marks of a following radar side
_NEAREST_RADAR = "analysis_tile.export_labels.report_nearest_radar"
_NEAREST_RADAR_OTHER = "analysis_tile.export_labels.report_nearest_radar_other"
#: the radar figure markers of each side
_RADAR_MARKERS = (("[Placeholder for Radar 1]",), ("[Placeholder for Radar 2]", "[Placeholder for Radar 3]"))


def _caption(info, other, pinned) -> str:
    if info is None:
        return ""
    name = info[3].strip()
    if pinned:
        return name
    mark = _NEAREST_RADAR if other is None or other[0] == info[0] else _NEAREST_RADAR_OTHER
    return f"{name} ({t(mark)})"


def stage_report_images(alert, folder, area="normal") -> tuple[Optional[Path], dict, dict]:
    """Download the radar composite of ``alert``'s radar pair (worker thread).

    Returns ``(radar_tif, missing, extra)``. ``radar_tif`` is ``None`` when
    both radar infos are ``None`` (the lane was not shown); a side whose image
    is still loading never gets here (``alert_files.radar_images_ready``
    holds the build). A side exists
    only with its info: the timeline unpublishes a side by its info and leaves
    its last ee.Image behind. A side with no image takes the other side's
    place in the file, and ``missing`` gives its figures a note instead;
    ``extra`` holds the radar captions and ``radar_summary``."""
    infos = (alert.before_radar_info, alert.after_radar_info)
    images = tuple(None if info is None else image
                   for info, image in zip(infos, (alert.before_radar_image, alert.after_radar_image)))
    pinned = alert.radar_pinned or {}
    if all(info is None for info in infos):
        return None, {}, {}
    present = [(info, image) for info, image in zip(infos, images) if image is not None]
    radar = None
    if present:
        (before, image_b), (after, image_a) = present[0], present[-1]
        radar = stage_alert_image(folder, alert.alert_id, before, after, image_b, image_a, alert.grid, area)
    missing = {}
    for image, markers in zip(images, _RADAR_MARKERS):
        if image is None:
            missing.update(dict.fromkeys(markers, t("analysis_tile.export_labels.report_no_radar")))
    extra = {"before_radar": _caption(infos[0], infos[1], pinned.get("before")),
             "after_radar": _caption(infos[1], infos[0], pinned.get("after"))}
    # the summary names the radar pair too ("–" for a side with none)
    extra["radar_summary"] = t("analysis_tile.export_labels.report_radar_summary",
                               before=extra["before_radar"] or "–", after=extra["after_radar"] or "–")
    return radar, missing, extra
