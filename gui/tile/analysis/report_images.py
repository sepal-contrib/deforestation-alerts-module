"""The images an alert's files show when a pick is radar (report radar spec,
2026-11-09): the optical figures always show optical images (the nearest one
stands in for a radar pick), and a radar pair gets its own block.

``capture_report_images`` reads the scene index on the UI thread at the click,
like ``gif.capture_gif``; ``stage_report_images`` downloads the composites on
the build's worker.
"""

from typing import NamedTuple, Optional

from deforestation_alerts.scripts import scene_query
from deforestation_alerts.scripts.image_sources import is_radar
from gui.i18n import t
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis import imagery
from gui.tile.analysis.paths import stage_alert_image
from gui.tile.analysis.timeline import info_tuple

#: the caption marks of a stand-in, and the ``{image_note}`` per side(s) of the radar picks
_NEAREST_OPTICAL = "analysis_tile.export_labels.report_nearest_optical"
_NEAREST_RADAR = "analysis_tile.export_labels.report_nearest_radar"
_NEAREST_RADAR_OTHER = "analysis_tile.export_labels.report_nearest_radar_other"
_NOTES = {("before",): "analysis_tile.export_labels.report_note_before",
          ("after",): "analysis_tile.export_labels.report_note_after",
          ("before", "after"): "analysis_tile.export_labels.report_note_both"}


class Shown(NamedTuple):
    info: list  # ``timeline.info_tuple`` of the image
    keys: Optional[tuple]  # its granules, to rebuild a stand-in; None: the reviewer's own pick
    mark: str  # "" for a pick, else the label key of the stand-in's caption mark


class ReportImages(NamedTuple):
    optical: tuple  # (before, after): a Shown, or None when there is none
    radar: tuple
    radar_sides: tuple  # the sides whose pick is radar


def plan_report_images(tables, bbox, before_info, after_info, max_cloud, area_bbox=None) -> Optional[ReportImages]:
    """What the report shows for these picks, or ``None`` when neither is radar
    (the files are built from the picks, as they always were). Stand-ins come
    from the index within the GIF's reach of the picks, under ``max_cloud``,
    whatever sources the timeline shows; their granules fill ``area_bbox``
    (a larger image area) else ``bbox``."""
    infos = (before_info, after_info)
    if None in infos or not any(is_radar(i[0]) for i in infos):
        return None
    picks = tuple(scene_query.saved_pick_id(i[3]) for i in infos)
    if None in picks:
        return None
    days = [scene_query.parse_row_id(rid)[1] for rid in picks]
    rows = scene_query.scenes_for(tables, bbox, scene_query.shift_months(min(days), -gif_mod._REACH_MONTHS),
                                  scene_query.shift_months(max(days), gif_mod._REACH_MONTHS), max_cloud=max_cloud)
    ids = scene_query.report_images(rows, *picks)

    def shown(i, rid, kind):
        if rid is None:
            return None
        if rid == picks[i]:
            return Shown(list(infos[i]), None, "")
        row = rows[rows["id"] == rid].iloc[0]
        mark = _NEAREST_OPTICAL
        if kind == "radar":  # paired with the other side's radar pick: says when the pass changed
            mark = _NEAREST_RADAR if row["source"] == infos[1 - i][0] else _NEAREST_RADAR_OTHER
        keys = scene_query.granule_keys(tables, area_bbox or bbox, row["source"], row["date"])
        return Shown(info_tuple(row), tuple(keys), mark)

    return ReportImages(
        optical=tuple(shown(i, rid, "optical") for i, rid in enumerate(ids["optical"])),
        radar=tuple(shown(i, rid, "radar") for i, rid in enumerate(ids["radar"])),
        radar_sides=tuple(side for side, i in (("before", 0), ("after", 1)) if is_radar(infos[i][0])),
    )


def capture_report_images(app_state, widgets, alert) -> Optional[ReportImages]:
    """``plan_report_images`` of ``alert`` (``state.capture_alert``), read at
    the click on the UI thread. Without the alert's imagery context (another
    alert is on screen) no stand-in is found: the report notes the missing images."""
    ctx = widgets.imagery_ctx.value or {}
    if ctx.get("token") != alert.token:
        ctx = {}
    filt = getattr(getattr(widgets, "timeline_filter", None), "value", None)
    max_cloud = filt[1] if filt is not None else None
    return plan_report_images(app_state.scene_index.value, ctx.get("bbox"), alert.before_info, alert.after_info,
                              max_cloud, area_bbox=ctx.get("area_bbox"))


def _caption(shown: Optional[Shown]) -> str:
    if shown is None:
        return ""
    name = shown.info[3].strip()
    return f"{name} ({t(shown.mark)})" if shown.mark else name


def _stage(folder, alert, pair, area):
    """The composite of ``pair`` and which sides it holds. A side without an
    image takes the other's place in the file (its figures carry a note
    instead); ``None`` when neither side has one."""
    images = [None if s is None else pick if s.keys is None else imagery.rebuild_image(s.info[0], list(s.keys),
                                                                                       alert.grid)
              for s, pick in zip(pair, (alert.before_image, alert.after_image))]
    present = [(s, image) for s, image in zip(pair, images) if image is not None]
    held = tuple(image is not None for image in images)
    if not present:
        return None, held
    (before, image_b), (after, image_a) = present[0], present[-1]
    return stage_alert_image(folder, alert.alert_id, before.info, after.info, image_b, image_a, alert.grid,
                             area), held


#: the figure markers of each side, optical then radar
_OPTICAL_MARKERS = (("[Placeholder for Image 1]", "[Placeholder for Image 4]"),
                    ("[Placeholder for Image 2]", "[Placeholder for Image 3]",
                     "[Placeholder for Image 5]", "[Placeholder for Image 6]"))
_RADAR_MARKERS = (("[Placeholder for Radar 1]",), ("[Placeholder for Radar 2]", "[Placeholder for Radar 3]"))


def stage_report_images(plan: ReportImages, alert, folder, area="normal"):
    """Download the composites ``plan`` needs (worker thread).

    Returns ``(optical_tif, radar_tif, missing, extra)``: either tif may be
    ``None``; ``missing`` is the report's ``{marker: note}`` for the figures
    with no image, ``extra`` its image placeholders (the captions, marks and
    the ``{image_note}``).
    """
    optical, optical_held = _stage(folder, alert, plan.optical, area)
    radar, radar_held = _stage(folder, alert, plan.radar, area)
    missing = {}
    for held, markers, note in ((optical_held, _OPTICAL_MARKERS, "analysis_tile.export_labels.report_no_optical"),
                                (radar_held, _RADAR_MARKERS, "analysis_tile.export_labels.report_no_radar")):
        for side_held, side_markers in zip(held, markers):
            if not side_held:
                missing.update(dict.fromkeys(side_markers, t(note)))
    extra = {"before_radar": _caption(plan.radar[0]), "after_radar": _caption(plan.radar[1]),
             "image_note": t(_NOTES[plan.radar_sides])}
    # the summary names the radar pair too ("–" for a side with none)
    extra["radar_summary"] = t("analysis_tile.export_labels.report_radar_summary",
                               before=extra["before_radar"] or "–", after=extra["after_radar"] or "–")
    for key, shown in (("before_img", plan.optical[0]), ("after_img", plan.optical[1])):
        if shown is not None:  # with none, the caption stays the reviewer's pick
            extra[key] = _caption(shown)
    return optical, radar, missing, extra
