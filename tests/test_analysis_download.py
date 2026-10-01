"""``build_export``'s download worker writes four artifacts — gpkg, before/after
GeoTIFF, zip and Word report — and all four are USER data.

Since Phase 6 the recipe folder is a remote SEPAL path, so the whole flow is
build-in-scratch + upload. These tests drive the real ``_download_data`` with
only the two genuinely external steps stubbed (the geedim image download and the
cartopy figure render) and then read every artifact back out of the fake client's
in-memory workspace.
"""

import base64
import zipfile
from pathlib import Path
from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import box

from gui.store.state_manager import AppState
from gui.tile.analysis import export as export_mod

# Smallest valid JPEG so python-docx's add_picture accepts the stubbed renders.
_JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)

_BEFORE_INFO = ["Sentinel 2", 1700000000, None, "S2_before", "_010124"]
_AFTER_INFO = ["Sentinel 2", 1700000001, None, "S2_after", "_020224"]
_TIF_NAME = "alert_0_010124_020224.tif"


def _alerts_gdf():
    gdf = gpd.GeoDataFrame(
        {
            "admin1": ["Paraguay"], "admin2": ["Canindeyú"], "admin3": ["Curuguaty"],
            "alert_date_min": [2026.001], "alert_date_max": [2026.010],
            "before_img": ["S2_before"], "after_img": ["S2_after"],
            "alert_sources": ["GLAD-L"], "area_ha": [1.25],
            "alert_polygon": [None],
        },
        geometry=[box(0, 0, 1, 1)], crs="EPSG:4326",
    )
    gdf["bounding_box"] = gdf.geometry.envelope
    return gdf


@pytest.fixture()
def download_ctx(monkeypatch, ws):
    """Everything ``_download_data`` reads, plus the two external steps stubbed."""
    from deforestation_alerts.scripts import report_builder
    from gui.tile.analysis import paths

    downloads = []

    def _fake_download(image1, image2, image_name, source1, source2, region):
        downloads.append(image_name)
        Path(image_name).write_bytes(b"geotiff-bytes")
        return image_name

    monkeypatch.setattr(paths, "download_both_images", _fake_download)
    monkeypatch.setattr(
        report_builder,
        "plot_tiff_with_overlay",
        lambda tiff_path, output_path, **kw: Path(output_path).write_bytes(_JPEG_1PX),
    )

    app_state = AppState()
    app_state.alerts_gdf.value = _alerts_gdf()
    app_state.actual_alert_id.value = 0

    # What _download_data reads from the shell's SetContext (passed as both
    # app_tile_model and aux_model): the set's folder and the report template.
    set_context = SimpleNamespace(recipe_folder_path=ws.ensure_dir("set"), custom_report_template=None)

    widgets = SimpleNamespace(
        selected_img_before_info_list=_BEFORE_INFO,
        selected_img_after_info_list=_AFTER_INFO,
        selected_img_before=None,
        selected_img_after=None,
        actual_alert_grid=None,
    )
    return SimpleNamespace(
        widgets=widgets, app_state=app_state, aux_model=set_context,
        app_tile_model=set_context, ws=ws, downloads=downloads,
    )


def _run(ctx):
    return export_mod._download_data(
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model, ctx.ws
    )


def test_download_publishes_every_artifact_to_the_workspace(download_ctx, tmp_path):
    ctx = download_ctx
    folder = ctx.app_tile_model.recipe_folder_path

    result = _run(ctx)

    assert result == {
        "zip_path": f"{folder}/alert_0.zip",
        "report_path": f"{folder}/alert_0.docx",
    }
    for name in ("alert_0.gpkg", _TIF_NAME, "alert_0.zip", "alert_0.docx"):
        assert ctx.ws.exists(f"{folder}/{name}"), name
    # The zip carries the two members under their plain names, not the container
    # staging path they were built in.
    local_zip = tmp_path / "alert_0.zip"
    local_zip.write_bytes(ctx.ws.read_bytes(f"{folder}/alert_0.zip"))
    assert sorted(zipfile.ZipFile(local_zip).namelist()) == sorted(
        [_TIF_NAME, "alert_0.gpkg"]
    )
    # Nothing staged survives the call.
    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []


def test_download_reuses_the_cached_geotiff(download_ctx):
    """A second download must not re-export the composite from GEE: the cache
    probe is a remote ``exists``, not a container ``Path.exists``."""
    ctx = download_ctx

    _run(ctx)
    assert len(ctx.downloads) == 1

    _run(ctx)
    assert len(ctx.downloads) == 1


def test_download_uses_the_users_custom_report_template(download_ctx):
    """A custom template is a file in the USER's workspace (the Aux tile's remote
    FileInput), so it must be staged down — not opened as a container path."""
    ctx = download_ctx
    template = ctx.ws.results_dir("my_template.docx")
    ctx.ws.write_bytes(
        template, Path(export_mod._DEFAULT_REPORT_TEMPLATE).read_bytes()
    )
    ctx.aux_model.custom_report_template = template

    result = _run(ctx)

    assert ctx.ws.read_bytes(result["report_path"])[:2] == b"PK"


def test_download_is_a_noop_without_a_recipe(download_ctx):
    ctx = download_ctx
    ctx.app_tile_model.recipe_folder_path = ""

    assert _run(ctx) is None


def test_composite_download_drives_the_task_progress_ring(download_ctx, monkeypatch):
    """The before/after composite download reports geedim tile ticks as task
    progress and then hands the ring back to indeterminate for the zip/report;
    a cache hit downloads nothing, so it only resets the ring."""
    from deforestation_alerts.scripts import gee_progress
    from gui.tile.analysis import paths

    ctx = download_ctx
    fake_download = paths.download_both_images

    def _ticking_download(*args, **kwargs):
        callback = gee_progress._local.callback
        callback(1, 2)
        callback(2, 2)
        return fake_download(*args, **kwargs)

    monkeypatch.setattr(paths, "download_both_images", _ticking_download)

    class _Task:
        def __init__(self):
            self.progress = []

        def set_progress(self, value):
            self.progress.append(value)

    first = _Task()
    export_mod._download_data(
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model, ctx.ws,
        task=first,
    )
    assert first.progress == [0.5, 1.0, None]

    cached = _Task()
    export_mod._download_data(
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model, ctx.ws,
        task=cached,
    )
    assert cached.progress == [None]
