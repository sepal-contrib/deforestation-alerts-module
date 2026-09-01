"""The filtered-alert GeoTIFF is staged locally (geedim + the vectorizer only
speak local paths) and published into the user's recipe folder.

The old code wrote it to ``<recipe or module_dir>/filtered_alerts_*.tif`` on the
container's disk, where the user could never see it.
"""

from pathlib import Path

from gui.tile.alerts_filter_tile import (
    AnalyzePlan,
    _filtered_alerts_path,
    _publish_alert_raster,
)

_ARGS = (["GLAD-L", "RADD"], "2026-01-01", "2026-06-12")
_NAME = "filtered_alerts_glad-l-radd_2026-01-01_2026-06-12.tif"


def _plan(local, remote):
    return AnalyzePlan(
        selections=None, filtered_alert_raster=None, region=None,
        pixel_size=10, min_size_pixels=1, out_tif=local, remote_tif=remote,
    )


def test_path_stages_locally_and_targets_the_recipe_folder(ws, tmp_path):
    folder = ws.results_dir("recipe_a")

    local, remote = _filtered_alerts_path(ws, folder, *_ARGS)

    assert remote == f"{folder}/{_NAME}"
    assert Path(local).name == _NAME
    assert Path(local).is_relative_to(tmp_path)


def test_path_without_a_recipe_has_no_remote_target(ws, tmp_path):
    """No recipe -> scratch only. There is no container fallback any more: the
    user would never find the file, and the container's disk is not theirs."""
    local, remote = _filtered_alerts_path(ws, "", *_ARGS)

    assert remote is None
    assert Path(local).is_relative_to(tmp_path)


def test_publish_uploads_the_staged_raster(ws):
    folder = ws.results_dir("recipe_a")
    local, remote = _filtered_alerts_path(ws, folder, *_ARGS)
    Path(local).write_bytes(b"geotiff-bytes")

    assert _publish_alert_raster(_plan(local, remote), ws) == remote
    assert ws.read_bytes(remote) == b"geotiff-bytes"


def test_publish_is_a_noop_without_a_recipe(ws):
    local, remote = _filtered_alerts_path(ws, "", *_ARGS)
    Path(local).write_bytes(b"geotiff-bytes")

    assert _publish_alert_raster(_plan(local, remote), ws) is None
    assert ws.client.files.files == {}
