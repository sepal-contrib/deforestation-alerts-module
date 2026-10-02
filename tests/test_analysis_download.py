"""``build_export``'s download worker writes four artifacts — gpkg, before/after
GeoTIFF, zip and Word report — and all four are USER data, written straight
into the set folder.

These tests drive the real ``_download_data`` with only the two genuinely
external steps stubbed (the geedim image download and the cartopy figure
render) and then read every artifact back out of the set folder.
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
def download_ctx(monkeypatch, data_root, tmp_path):
    """Everything ``_download_data`` reads, plus the two external steps stubbed."""
    from deforestation_alerts import gdal_env
    from deforestation_alerts.scripts import report_builder
    from gui.tile.analysis import paths

    scratch = tmp_path / "scratch"
    monkeypatch.setattr(gdal_env, "scratch_dir", lambda: scratch)

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
    folder = data_root / "set"
    folder.mkdir()
    set_context = SimpleNamespace(recipe_folder_path=str(folder), custom_report_template=None)

    widgets = SimpleNamespace(
        selected_img_before_info_list=_BEFORE_INFO,
        selected_img_after_info_list=_AFTER_INFO,
        selected_img_before=None,
        selected_img_after=None,
        actual_alert_grid=None,
    )
    return SimpleNamespace(
        widgets=widgets, app_state=app_state, aux_model=set_context,
        app_tile_model=set_context, folder=folder, scratch=scratch, downloads=downloads,
    )


def _run(ctx):
    return export_mod._download_data(
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model
    )


def test_download_writes_every_artifact_into_the_set_folder(download_ctx, data_files):
    ctx = download_ctx
    folder = ctx.folder

    result = _run(ctx)

    assert result == {
        "zip_path": f"{folder}/alert_0.zip",
        "report_path": f"{folder}/alert_0.docx",
    }
    # The four artifacts, and nothing else (no .part, no GDAL sidecar).
    assert data_files(folder) == sorted(
        folder / name for name in ("alert_0.gpkg", _TIF_NAME, "alert_0.zip", "alert_0.docx")
    )
    # The composite was downloaded once, through a .part of the set folder.
    [downloaded] = ctx.downloads
    assert Path(downloaded).parent == folder and Path(downloaded).name.startswith(_TIF_NAME + ".")
    # The zip carries the two members under their plain names, not the
    # scratch path they were built in.
    assert sorted(zipfile.ZipFile(folder / "alert_0.zip").namelist()) == sorted(
        [_TIF_NAME, "alert_0.gpkg"]
    )
    assert (folder / "alert_0.docx").read_bytes()[:2] == b"PK"
    # The scratch folder the gpkg, zip and report were built in is gone.
    assert list(ctx.scratch.iterdir()) == []


def test_download_reuses_the_composite_in_the_set_folder(download_ctx):
    """A second download must not re-export the composite from GEE."""
    ctx = download_ctx

    _run(ctx)
    assert len(ctx.downloads) == 1

    _run(ctx)
    assert len(ctx.downloads) == 1


def test_a_failed_composite_download_leaves_no_composite_or_part_file(download_ctx, monkeypatch):
    from gui.tile.analysis import paths

    ctx = download_ctx

    def _failing(image1, image2, image_name, *args):
        Path(image_name).write_bytes(b"half")
        raise RuntimeError("geedim failed")

    monkeypatch.setattr(paths, "download_both_images", _failing)
    with pytest.raises(RuntimeError):
        _run(ctx)
    assert not (ctx.folder / _TIF_NAME).exists()
    assert not list(ctx.folder.glob("*.part")) and not list(ctx.folder.glob("*.tif*"))
    assert list(ctx.scratch.iterdir()) == []


def test_download_uses_the_users_custom_report_template(download_ctx, data_root):
    """A custom template is the user's own file, read where it is."""
    ctx = download_ctx
    template = data_root / "my_template.docx"
    template.write_bytes(Path(export_mod._DEFAULT_REPORT_TEMPLATE).read_bytes())
    ctx.aux_model.custom_report_template = str(template)

    result = _run(ctx)

    assert Path(result["report_path"]).read_bytes()[:2] == b"PK"
    assert template.exists()


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
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model,
        task=first,
    )
    assert first.progress == [0.5, 1.0, None]

    cached = _Task()
    export_mod._download_data(
        ctx.widgets, ctx.app_state, ctx.aux_model, ctx.app_tile_model,
        task=cached,
    )
    assert cached.progress == [None]
