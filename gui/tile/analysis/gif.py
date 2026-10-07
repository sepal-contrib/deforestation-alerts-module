"""The alert GIF (spec D6-D10): which frames, captured at the click, and the
render that a worker runs.

``capture_gif`` reads everything the GIF needs on the UI thread, where the
alert, its picks, the scene index and the timeline's filter are one alert's:
the worker then never reads live state that an alert change may have moved
on. ``build_gif`` does the slow part (one Earth Engine thumbnail per frame).
"""

import logging
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional

import ee
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.scripts import alert_gif, local_files, scene_query
from deforestation_alerts.scripts.scene_index import SOURCE_CODE
from gui.i18n import t
from gui.tile.analysis import imagery
from gui.tile.analysis.timeline import SRC_LABEL

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

#: how far either side of the picks the neighbours are looked for. The index
#: holds what its background fill fetched; nothing is fetched for the GIF.
_REACH_MONTHS = 12


class GifRefused(KeyedError):
    """A GIF that must not be made: nothing failed, there is nothing to animate."""


class Frame(NamedTuple):
    source: str
    day: date
    keys: tuple
    role: str  # "before", "after" or ""


class GifJob(NamedTuple):
    frames: tuple
    grid: object
    outline: BaseGeometry
    detect_date: Optional[date]
    alert_id: int
    before_info: list
    after_info: list


def _pick_id(info) -> Optional[str]:
    return scene_query.saved_pick_id(info[3]) if info else None


def plan_frames(tables, bbox, before_info, after_info, sources, max_cloud) -> list:
    """The frames, oldest first: the picks and up to two neighbours either side
    under the timeline's filter (spec D6). Empty when a pick is not in the index."""
    before_id, after_id = _pick_id(before_info), _pick_id(after_info)
    if tables is None or bbox is None or not before_id or not after_id:
        return []
    days = [scene_query.parse_row_id(rid)[1] for rid in (before_id, after_id)]
    rows = scene_query.scenes_for(tables, bbox, scene_query.shift_months(min(days), -_REACH_MONTHS),
                                  scene_query.shift_months(max(days), _REACH_MONTHS), max_cloud=max_cloud)
    rows = rows[rows["source"].isin(sources) | rows["id"].isin([before_id, after_id])]
    chosen = scene_query.gif_frames(rows, before_id, after_id)
    return [Frame(row["source"], row["date"].date(),
                  tuple(scene_query.granule_keys(tables, bbox, row["source"], row["date"])), row["role"])
            for _, row in chosen.iterrows()]


def _outline_of(alerts, alert_id) -> BaseGeometry:
    """The reviewed polygon when the alert has one, else the alert's own."""
    row = alerts.iloc[alert_id]
    drawn = row.get("alert_polygon")
    if isinstance(drawn, BaseGeometry) and not drawn.is_empty:
        return drawn
    return row.geometry


def capture_gif(app_state, widgets, analysis, alert) -> GifJob:
    """Everything the GIF of ``alert`` needs, read at the click (UI thread).

    ``alert`` is ``state.capture_alert``'s namespace. Raises ``GifRefused``
    when there is nothing to animate.
    """
    ctx = widgets.imagery_ctx.value or {}
    no_images = GifRefused(key="analysis_tile.export_labels.gif_no_images")
    if alert.alerts is None or alert.alert_id is None or ctx.get("token") != alert.token:
        raise no_images
    before_id, after_id = _pick_id(alert.before_info), _pick_id(alert.after_info)
    if not before_id or not after_id:
        raise no_images
    if before_id == after_id:
        raise GifRefused(key="analysis_tile.export_labels.gif_same_image")
    filt = getattr(getattr(widgets, "timeline_filter", None), "value", None)
    sources, max_cloud = filt if filt is not None else (frozenset(SOURCE_CODE), None)
    frames = plan_frames(app_state.scene_index.value, ctx.get("bbox"), alert.before_info, alert.after_info,
                         sources, max_cloud)
    if len(frames) < 2:
        raise no_images
    detect = ctx.get("date_min") or scene_query.parse_row_id(after_id)[1]
    return GifJob(frames=tuple(frames), grid=alert.grid, outline=_outline_of(alert.alerts, alert.alert_id),
                  detect_date=detect, alert_id=alert.alert_id, before_info=list(alert.before_info),
                  after_info=list(alert.after_info))


def label_of(frame: Frame) -> str:
    text = f"{SRC_LABEL.get(frame.source, frame.source)} {frame.day.isoformat()}"
    if frame.role == "before":
        return f"{t('analysis.timeline.before')} · {text}"
    if frame.role == "after":
        return f"{t('analysis.timeline.after')} · {text}"
    return text


def _ee_geometry(geom: BaseGeometry):
    """The outline as an ``ee.Geometry`` (a seam the offline tests replace)."""
    return ee.Geometry(mapping(geom))


def build_gif(job: GifJob, dest) -> Path:
    """Render ``job`` into ``dest`` (worker thread). A frame that fails is
    skipped and logged; fewer than two left raises ``GifRefused`` and writes
    nothing. Written through a ``.part`` renamed into place."""
    outline = _ee_geometry(job.outline)
    pngs, labels = [], []
    for frame in job.frames:
        try:
            image = imagery.rebuild_image(frame.source, list(frame.keys), job.grid)
            if image is None:
                continue
            draw = job.detect_date is not None and frame.day >= job.detect_date
            shown = alert_gif.frame_image(image, imagery.true_colour_vis(frame.source), outline if draw else None)
            pngs.append(alert_gif.fetch_frame(shown, job.grid))
            labels.append(label_of(frame))
        except Exception:  # noqa: BLE001 - one frame lost is a shorter GIF, not a failed one
            logger.exception("GIF: the %s frame of %s could not be rendered", frame.source, frame.day)
    if len(pngs) < 2:
        raise GifRefused(key="analysis_tile.export_labels.gif_too_few")
    with local_files.write_into(dest) as part:
        alert_gif.assemble_gif(pngs, labels, part)
    return Path(dest)
