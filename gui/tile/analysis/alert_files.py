"""One alert's files (the Export section's build): its GeoPackage, the
before/after composite, the zip and the Word report, plus the rules that
decide when they, and the alert's GIF, can be made. Data and I/O only, no
widgets: ``export.build_export`` runs ``build_alert_files`` on a worker.
"""

import logging
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import shapely
from pysepal.i18n import current_locale
from shapely.geometry.base import BaseGeometry

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.growth import links_of
from deforestation_alerts.project.questions import column
from deforestation_alerts.project.review_groups import group_of, group_record, number
from deforestation_alerts.project.review_status import CONFIRMED, VERDICT_LABEL_KEYS, is_reviewed
from deforestation_alerts.project.store_schema import BELOW_MIN, DETECTED_AREA, GROWS_FROM, GROWS_FROM_AUTO
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.alert_export import detected_area
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import add_files_to_zip
from deforestation_alerts.scripts.report_builder import generate_deforestation_report_with_word_template
from gui.i18n import t
from gui.scripts import questions
from gui.scripts.notify_bridge import tile_progress_reporter
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis.paths import alert_gif_name, stage_alert_image
from gui.tile.analysis.report_images import stage_report_images

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Default Word report template (OLD used this literal when no custom template set).
# Resolved from the repo root (alert_files.py -> analysis -> tile -> gui -> root):
# the CWD is not guaranteed writable or even stable on the platform.
DEFAULT_REPORT_TEMPLATE = str(
    Path(__file__).resolve().parents[3] / "data" / "report_template.docx"
)


def default_report_template() -> Path:
    """The default template in the app's language (``report_template_<locale>.docx``,
    written by ``tools/translate_report_template.py``); English when there is none."""
    english = Path(DEFAULT_REPORT_TEMPLATE)
    localized = english.with_name(f"report_template_{current_locale()}.docx")
    return localized if localized.is_file() else english


class ReportTemplateMissing(KeyedError, RuntimeError):
    """The project's report template is gone from disk: the download writes nothing."""

    key = "analysis_tile.export_labels.report_template_missing"

    def __init__(self, path: str):
        super().__init__(f"report template not found: {path}", path=path)


def report_template_path(template: Optional[str]) -> Path:
    """The ``.docx`` a report is built from: the project's, or the app's default."""
    if not template:
        return default_report_template()
    path = Path(template)
    if not path.is_file():
        raise ReportTemplateMissing(template)
    return path


def alert_selected(alert) -> bool:
    """An alert set is loaded and the selected index points into it
    (``alert``: ``state.capture_alert``)."""
    gdf, alert_id = alert.alerts, alert.alert_id
    return gdf is not None and alert_id is not None and 0 <= alert_id < len(gdf)


def images_picked(alert) -> bool:
    """A before and an after image are selected for the alert under review
    AND each pane has rebuilt the one it was given.

    The infos alone are not enough: the timeline publishes them at the click
    and the ee.Image of that pick lands when its rebuild does
    (``imagery.wire_panes``, which clears the lane's image at the pick). In
    between, a build would name the composite after the new pick
    (``paths.alert_image_name`` reads the infos) and export the previous
    pick's pixels -- or fail inside geedim on a ``None`` image.
    """
    return all(v is not None for v in (alert.before_info, alert.after_info,
                                       alert.before_image, alert.after_image))


def radar_images_ready(alert) -> bool:
    """Each radar side on screen (its info set) has its ee.Image rebuilt: the
    files' rule for the radar pair, as ``images_picked`` is for the optical
    one. Not in ``images_picked``: the CNN runs on the optical pair alone.
    A saved radar pick still waiting for its index (``radar_pending``) is not
    ready either: the files would otherwise go out without it."""
    if getattr(alert, "radar_pending", False):
        return False
    return all(info is None or image is not None
               for info, image in ((alert.before_radar_info, alert.before_radar_image),
                                   (alert.after_radar_info, alert.after_radar_image)))


def review_placeholders(question_list, row, status) -> dict:
    """``{verdict}`` and one ``{key}`` per question, as the report shows them
    (in the current language; a question the verdict does not ask is blank)."""
    out = {"verdict": t(VERDICT_LABEL_KEYS[status]) if status in VERDICT_LABEL_KEYS else ""}
    for q in question_list:
        out[q.key] = questions.label(q, row.get(column(q))) if questions.visible(q, status) else ""
    return out


def saved_status(alerts, alert_id) -> Optional[str]:
    """The verdict the alert's row holds: what its last save wrote, or None.
    None too for a row never reviewed: the store writes ``"Not reviewed"``
    into every row, and that is no saved review (``is_reviewed``)."""
    if alerts is None or alert_id is None or not 0 <= alert_id < len(alerts) or "status" not in alerts.columns:
        return None
    value = alerts.iloc[alert_id]["status"]
    return value if isinstance(value, str) and is_reviewed(value) else None


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and value == "") or pd.isna(value)


def picks_are_saved(alerts, alert_id, before_info, after_info, radar_saved=None) -> bool:
    """Whether the pair on screen is the pair the alert's row was saved with
    (``before_img`` / ``after_img`` hold the picks' ``info[3]``). A GIF
    animates the reviewed pair only (spec D9): a new, unsaved pick is not it.
    A missing column or value is no match.

    ``radar_saved`` (``review_save.radar_updates`` of the radar pair on
    screen) is what a save would write into the radar columns: each of its
    names must be the row's (None, NaN and "" alike are empty). A side it
    leaves out (no radar info) is not compared."""
    if before_info is None or after_info is None:
        return False
    if alerts is None or alert_id is None or not 0 <= alert_id < len(alerts):
        return False
    row = alerts.iloc[alert_id]
    saved = [row.get("before_img"), row.get("after_img")]
    if not all(isinstance(value, str) for value in saved):
        return False
    if [before_info[3], after_info[3]] != saved:
        return False
    # the names only: a millis column may come back from the store as a float
    for key, value in (radar_saved or {}).items():
        stored = row.get(key)
        if key.endswith("_img") and not (_empty(value) and _empty(stored) or value == stored):
            return False
    return True


def gif_allowed(alerts, alert_id, before_info, after_info, radar_saved=None) -> bool:
    """A GIF is made for a true alert on its reviewed pair (spec D9): the
    row's stored status is Confirmed and both picks are the pair it was saved
    with. The Timelapse tile and the zip build share this rule."""
    return (saved_status(alerts, alert_id) == CONFIRMED
            and picks_are_saved(alerts, alert_id, before_info, after_info, radar_saved))


def build_alert_files(alert, folder, template=None, *, task=None, gif=None):
    """Build one alert's files (the OLD ``download_data``; IO only, no widgets).

    Builds the single-alert gpkg, the before/after GeoTIFF (if missing), the zip,
    and the Word report, all in the set folder. Returns ``{"zip_path",
    "report_path"}`` — the set-folder paths for the download effect to wire onto
    the two file tiles. Guards on the gdf/id.

    The gpkg and the zip are built in a private scratch folder (GDAL may leave
    sidecars, zipfile writes a named file) and copied into the set folder; the
    composite is downloaded into the set folder once (``stage_alert_image``)
    and read where it is; the report is rendered from the scratch gpkg onto
    its set-folder path. The scratch folder is deleted when the block ends.

    ``task`` (the worker's ``TaskTracker``, optional) gets the composite
    download's geedim tile ticks as progress, and is handed back to
    indeterminate once the composite is staged. A cache hit produces no ticks.

    ``alert`` (``state.capture_alert``, taken at the click) is the alert the
    files are built for: its row, images and grid, read together, so the
    composite is never one alert's images clipped to another's grid.
    ``folder`` is the set folder the files go into, and ``template`` the
    project's report template (None: the app's default).

    ``gif`` (``gif.capture_gif``'s job, taken at the click) adds the alert's
    GIF to the zip when the row is saved Confirmed (spec D9); a GIF that fails
    leaves the zip without it and says so in the result: ``"gif_path"`` when
    one was made, ``"gif_warning": True`` when one was asked for and failed.

    The composite is the optical pair's. With a radar pair on screen at the
    click (``alert``'s radar infos), its composite joins it in the zip and the
    report gets its radar block (``report_images.stage_report_images``).
    """
    alertas_gdf = alert.alerts
    actual_alert_id = alert.alert_id

    if alertas_gdf is None or actual_alert_id is None or actual_alert_id < 0:
        return None
    if actual_alert_id >= len(alertas_gdf):
        return None

    # Every artifact below is written into the set folder; without one there
    # is nowhere to put them (smoke / no active set).
    if not folder:
        return None
    folder = Path(folder)

    # The composite needs a before and an after image (their info lists carry
    # the date suffix and the image id, see paths.alert_image_name), and both
    # are None until an image is picked for this alert, which nothing does by
    # itself. Refused here, before any file is written: the TypeError this
    # raised further down used to leave a partial alert_<id>.gpkg behind.
    if not images_picked(alert) or not radar_images_ready(alert):
        return None
    picked = (alert.before_info, alert.after_info, alert.before_image, alert.after_image)

    # Before any file is written: a template gone from disk is a refusal, not
    # a half-written set of downloads.
    template = report_template_path(template)

    # Single-row GeoDataFrame for the selected alert.
    # A grouped alert is described as its group (spec §9): the main's row with
    # the summed area, plus ``group_size`` / ``group_alerts``, and the group's
    # outline as the one geometry. A single alert is its own row.
    # The geometry is what the reviewer approved: the drawing, else the
    # cluster (never its box); the cluster (the group's union) goes beside it.
    selected_element, outline = group_record(alertas_gdf, actual_alert_id)
    grouped = selected_element["group_size"] > 1
    main, members = group_of(alertas_gdf, actual_alert_id)
    rows = [main, *members]
    drawn = selected_element.get("alert_polygon")
    selected_element[DETECTED_AREA] = float(detected_area(alertas_gdf.iloc[rows]).sum())
    selected_element["geometry_source"] = "drawn" if isinstance(drawn, BaseGeometry) and not drawn.is_empty \
        else "detected"
    cluster = shapely.union_all(alertas_gdf.geometry.iloc[rows].to_numpy()) if grouped \
        else alertas_gdf.geometry.iloc[main]
    if grouped:
        selected_element["alert_polygon"] = outline
    # The answers in the language of the download (stored as codes); the
    # columns of deleted questions are left out.
    extra = [c for c in (DETECTED_AREA, "group_size", "group_alerts", "geometry_source") if c not in alertas_gdf.columns]
    selected_gdf = questions.with_labels(
        gpd.GeoDataFrame([selected_element], columns=[*alertas_gdf.columns, *extra]),
        alert.questions or [],
    )
    active = (
        "alert_polygon"
        if not selected_gdf["alert_polygon"].isnull().all()
        else "geometry"
    )
    selected_gdf.set_geometry(active, inplace=True)
    # PRE-EXISTING BUG, fixed here because the migrated path is otherwise
    # untestable: ``alerts_gdf`` carries three geometry columns (polygon +
    # ``bounding_box`` + ``point``), and GPKG accepts exactly one — so
    # ``to_file`` raised "GeoDataFrame contains multiple geometry columns" on
    # EVERY download. Drop the inactive ones (a one-row frame may hold them as
    # object columns), as ``alert_export`` does; the report only reads attributes.
    selected_gdf = selected_gdf.drop(
        columns=[
            c
            for c in selected_gdf.columns
            if c != active and (c in ("bounding_box", "point") or selected_gdf[c].dtype == "geometry")
        ]
    )
    # The growth columns as the main export writes them (spec §6.7): the
    # links joined by ";", the working columns left out.
    if GROWS_FROM in selected_gdf.columns:
        selected_gdf[GROWS_FROM] = [";".join(links_of(cell)) for cell in selected_gdf[GROWS_FROM]]
    selected_gdf = selected_gdf.drop(columns=[c for c in (GROWS_FROM_AUTO, BELOW_MIN) if c in selected_gdf.columns])

    # Files are named by the stored id, the number a person reads; the row is still found by position.
    n = number(alertas_gdf, actual_alert_id)
    gpkg_dest = folder / f"alert_{n}.gpkg"
    zip_dest = folder / f"alert_{n}.zip"
    report_dest = folder / f"alert_{n}.docx"
    with local_files.scratch_dir() as stage:
        gpkg_local = stage / gpkg_dest.name
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            str(gpkg_local), driver="GPKG", layer=gpkg_local.stem
        )
        keep = [c for c in ("alert_id", "set_id", DETECTED_AREA) if c in selected_gdf.columns]
        gpd.GeoDataFrame(selected_gdf[keep], geometry=[cluster], crs="EPSG:4326").to_file(
            str(gpkg_local), driver="GPKG", layer=f"{gpkg_local.stem}_detected", mode="a")

        # Before/after image GeoTIFF (downloaded only if not already in the set
        # folder). Built via the shared helper so it resolves to the same file
        # the CNN run (edit_tools) wrote.
        on_tiles = tile_progress_reporter(task) if task is not None else None
        area = getattr(alert, "image_area", "normal")
        with geedim_tile_progress(on_tiles):
            image_local = stage_alert_image(folder, n, *picked, alert.grid, area)
            radar_local, missing, image_text = stage_report_images(alert, folder, area)
        if task is not None:
            task.set_progress(None)  # zip + report: back to indeterminate

        # The GIF of a true alert, into the set folder and the zip (spec D9).
        # A failure costs the GIF, not the build: the reviewer still gets the
        # files and is told the zip has no GIF.
        gif_local, gif_warning = None, False
        if gif is not None and saved_status(alertas_gdf, actual_alert_id) == CONFIRMED:
            try:
                gif_local = gif_mod.build_gif(
                    gif, Path(alert_gif_name(str(folder), n, gif.before_info, gif.after_info)))
            except Exception:  # noqa: BLE001 - logged; the build goes on without it
                logger.exception("Analysis: the GIF for the zip could not be made")
                gif_warning = True

        # Only once the composite is there: a failed download must not leave
        # the alert's gpkg in the set folder.
        local_files.copy_into(gpkg_local, gpkg_dest)

        # Zip the image + gpkg.
        zip_local = stage / zip_dest.name
        add_files_to_zip(str(zip_local), str(gpkg_local),
                         *(str(f) for f in (image_local, radar_local, gif_local) if f is not None))
        local_files.copy_into(zip_local, zip_dest)

        # Word report: the project's template (a file of the user's, read
        # where it is) or the packaged default, resolved above.
        generate_deforestation_report_with_word_template(
            str(image_local), str(gpkg_local), str(template), str(report_dest),
            # The stored row is what the report describes: a build follows a save.
            extra={**review_placeholders(alert.questions or [], selected_element, selected_element.get("status")),
                   **image_text},
            radar_path=None if radar_local is None else str(radar_local), missing=missing,
        )

    result = {"zip_path": str(zip_dest), "report_path": str(report_dest)}
    if gif_local is not None:
        result["gif_path"] = str(gif_local)
    if gif_warning:
        result["gif_warning"] = True
    return result
