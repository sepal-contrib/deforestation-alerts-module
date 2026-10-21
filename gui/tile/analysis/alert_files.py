"""One alert's files (the Export section's build): its GeoPackage, the
before/after composite, the zip and the Word report, plus the rules that
decide when they, and the alert's GIF, can be made. Data and I/O only, no
widgets: ``export.build_export`` runs ``build_alert_files`` on a worker.
"""

import logging
from pathlib import Path
from typing import Optional

import geopandas as gpd

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.growth import links_of
from deforestation_alerts.project.questions import column
from deforestation_alerts.project.review_status import CONFIRMED, VERDICT_LABEL_KEYS
from deforestation_alerts.project.store_schema import BELOW_MIN, GROWS_FROM, GROWS_FROM_AUTO
from deforestation_alerts.scripts import local_files
from deforestation_alerts.scripts.gee_progress import geedim_tile_progress
from deforestation_alerts.scripts.mosaics_helper import add_files_to_zip
from deforestation_alerts.scripts.report_builder import generate_deforestation_report_with_word_template
from gui.i18n import t
from gui.scripts import questions
from gui.scripts.notify_bridge import tile_progress_reporter
from gui.tile.analysis import gif as gif_mod
from gui.tile.analysis.paths import alert_gif_name, stage_alert_image

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Default Word report template (OLD used this literal when no custom template set).
# Resolved from the repo root (alert_files.py -> analysis -> tile -> gui -> root):
# the CWD is not guaranteed writable or even stable on the platform.
DEFAULT_REPORT_TEMPLATE = str(
    Path(__file__).resolve().parents[3] / "data" / "report_template.docx"
)


class ReportTemplateMissing(KeyedError, RuntimeError):
    """The project's report template is gone from disk: the download writes nothing."""

    key = "analysis_tile.export_labels.report_template_missing"

    def __init__(self, path: str):
        super().__init__(f"report template not found: {path}", path=path)


def report_template_path(template: Optional[str]) -> Path:
    """The ``.docx`` a report is built from: the project's, or the app's default."""
    if not template:
        return Path(DEFAULT_REPORT_TEMPLATE)
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


def review_placeholders(question_list, row, status) -> dict:
    """``{verdict}`` and one ``{key}`` per question, as the report shows them
    (in the current language; a question the verdict does not ask is blank)."""
    out = {"verdict": t(VERDICT_LABEL_KEYS[status]) if status in VERDICT_LABEL_KEYS else ""}
    for q in question_list:
        out[q.key] = questions.label(q, row.get(column(q))) if questions.visible(q, status) else ""
    return out


def saved_status(alerts, alert_id) -> Optional[str]:
    """The verdict the alert's row holds: what its last save wrote, or None."""
    if alerts is None or alert_id is None or not 0 <= alert_id < len(alerts) or "status" not in alerts.columns:
        return None
    value = alerts.iloc[alert_id]["status"]
    return value if isinstance(value, str) else None


def picks_are_saved(alerts, alert_id, before_info, after_info) -> bool:
    """Whether the pair on screen is the pair the alert's row was saved with
    (``before_img`` / ``after_img`` hold the picks' ``info[3]``). A GIF
    animates the reviewed pair only (spec D9): a new, unsaved pick is not it.
    A missing column or value is no match."""
    if before_info is None or after_info is None:
        return False
    if alerts is None or alert_id is None or not 0 <= alert_id < len(alerts):
        return False
    row = alerts.iloc[alert_id]
    saved = [row.get("before_img"), row.get("after_img")]
    if not all(isinstance(value, str) for value in saved):
        return False
    return [before_info[3], after_info[3]] == saved


def gif_allowed(alerts, alert_id, before_info, after_info) -> bool:
    """A GIF is made for a true alert on its reviewed pair (spec D9): the
    row's stored status is Confirmed and both picks are the pair it was saved
    with. The Timelapse tile and the zip build share this rule."""
    return (saved_status(alerts, alert_id) == CONFIRMED
            and picks_are_saved(alerts, alert_id, before_info, after_info))


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
    if not images_picked(alert):
        return None
    picked = (alert.before_info, alert.after_info, alert.before_image, alert.after_image)

    # Before any file is written: a template gone from disk is a refusal, not
    # a half-written set of downloads.
    template = report_template_path(template)

    # Single-row GeoDataFrame for the selected alert.
    selected_element = alertas_gdf.iloc[actual_alert_id]
    # The answers in the language of the download (stored as codes); the
    # columns of deleted questions are left out.
    selected_gdf = questions.with_labels(
        gpd.GeoDataFrame([selected_element], columns=alertas_gdf.columns), alert.questions or []
    )
    active = (
        "alert_polygon"
        if not selected_gdf["alert_polygon"].isnull().all()
        else "bounding_box"
    )
    selected_gdf.set_geometry(active, inplace=True)
    # PRE-EXISTING BUG, fixed here because the migrated path is otherwise
    # untestable: ``alerts_gdf`` carries three geometry columns (polygon +
    # ``bounding_box`` + ``point``), and GPKG accepts exactly one — so
    # ``to_file`` raised "GeoDataFrame contains multiple geometry columns" on
    # EVERY download. Drop the inactive ones, the same way ``alert_export`` does
    # for its single-geometry formats; the report only reads attribute columns.
    selected_gdf = selected_gdf.drop(
        columns=[
            c
            for c in selected_gdf.columns
            if c != active and selected_gdf[c].dtype == "geometry"
        ]
    )
    # The growth columns as the main export writes them (spec §6.7): the
    # links joined by ";", the working columns left out.
    if GROWS_FROM in selected_gdf.columns:
        selected_gdf[GROWS_FROM] = [";".join(links_of(cell)) for cell in selected_gdf[GROWS_FROM]]
    selected_gdf = selected_gdf.drop(columns=[c for c in (GROWS_FROM_AUTO, BELOW_MIN) if c in selected_gdf.columns])

    gpkg_dest = folder / f"alert_{actual_alert_id}.gpkg"
    zip_dest = folder / f"alert_{actual_alert_id}.zip"
    report_dest = folder / f"alert_{actual_alert_id}.docx"
    with local_files.scratch_dir() as stage:
        gpkg_local = stage / gpkg_dest.name
        selected_gdf.set_crs(epsg="4326", allow_override=True, inplace=True).to_file(
            str(gpkg_local), driver="GPKG"
        )

        # Before/after image GeoTIFF (downloaded only if not already in the set
        # folder). Built via the shared helper so it resolves to the same file
        # the CNN run (edit_tools) wrote.
        on_tiles = tile_progress_reporter(task) if task is not None else None
        with geedim_tile_progress(on_tiles):
            image_local = stage_alert_image(
                folder,
                actual_alert_id,
                *picked,
                alert.grid,
            )
        if task is not None:
            task.set_progress(None)  # zip + report: back to indeterminate

        # The GIF of a true alert, into the set folder and the zip (spec D9).
        # A failure costs the GIF, not the build: the reviewer still gets the
        # files and is told the zip has no GIF.
        gif_local, gif_warning = None, False
        if gif is not None and saved_status(alertas_gdf, actual_alert_id) == CONFIRMED:
            try:
                gif_local = gif_mod.build_gif(
                    gif, Path(alert_gif_name(str(folder), actual_alert_id, gif.before_info, gif.after_info)))
            except Exception:  # noqa: BLE001 - logged; the build goes on without it
                logger.exception("Analysis: the GIF for the zip could not be made")
                gif_warning = True

        # Only once the composite is there: a failed download must not leave
        # the alert's gpkg in the set folder.
        local_files.copy_into(gpkg_local, gpkg_dest)

        # Zip the image + gpkg.
        zip_local = stage / zip_dest.name
        add_files_to_zip(str(zip_local), str(image_local), str(gpkg_local),
                         *([str(gif_local)] if gif_local is not None else []))
        local_files.copy_into(zip_local, zip_dest)

        # Word report: the project's template (a file of the user's, read
        # where it is) or the packaged default, resolved above.
        generate_deforestation_report_with_word_template(
            str(image_local), str(gpkg_local), str(template), str(report_dest),
            # The stored row is what the report describes: a build follows a save.
            extra=review_placeholders(alert.questions or [], selected_element, selected_element.get("status")),
        )

    result = {"zip_path": str(zip_dest), "report_path": str(report_dest)}
    if gif_local is not None:
        result["gif_path"] = str(gif_local)
    if gif_warning:
        result["gif_warning"] = True
    return result
