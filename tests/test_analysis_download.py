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

import pytest

from gui.store.state_manager import AppState
from gui.tile.analysis import export as export_mod
from gui.tile.analysis.state import AnalysisState
# ``_in`` / ``_build_button`` / ``_alerts_gdf`` live in conftest: the assembled
# panel's tests (tests/test_analysis_sections.py) drive the same export section
# through ``build_controls`` and need them too.
from tests.conftest import _alerts_gdf, _build_button, _in

# Smallest valid JPEG so python-docx's add_picture accepts the stubbed renders.
_JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)

_BEFORE_INFO = ["Sentinel 2", 1700000000, None, "S2_before", "_010124"]
_AFTER_INFO = ["Sentinel 2", 1700000001, None, "S2_after", "_020224"]
_TIF_NAME = "alert_0_s_010124_s_020224.tif"


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

    widgets = SimpleNamespace(actual_alert_grid=None)
    # The images picked for the alert (their info lists carry the composite's
    # name); the ee.Images themselves stay None, the download is stubbed.
    analysis = AnalysisState()
    analysis.before_info.set(_BEFORE_INFO)
    analysis.after_info.set(_AFTER_INFO)
    # ...and the panes have drawn them: the ee.Images the composite is
    # exported from. Stand-ins, since the geedim download is stubbed, but
    # present -- a build refuses while a lane is still rebuilding.
    analysis.before_image.set(object())
    analysis.after_image.set(object())
    return SimpleNamespace(
        widgets=widgets, analysis=analysis, app_state=app_state, aux_model=set_context,
        app_tile_model=set_context, folder=folder, scratch=scratch, downloads=downloads,
    )


def _run(ctx):
    return export_mod._download_data(
        ctx.widgets, ctx.analysis, ctx.app_state, ctx.aux_model, ctx.app_tile_model
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


def test_a_pick_from_another_collection_is_another_composite(download_ctx):
    """The cache above is keyed on the collections too, not only the dates.

    Sentinel 2, Landsat and Planet all have imagery for the same day, so a
    name built from the date suffixes alone collided: switching one side to
    another collection resolved to the file the first pick had already
    downloaded and ``stage_alert_image`` handed that back as a cache hit --
    the reviewer then analysed, exported and reported imagery from the wrong
    collection, with the right dates printed on it.
    """
    ctx = download_ctx

    _run(ctx)

    # Same day, another collection.
    ctx.analysis.before_info.set(
        ["Landsat", _BEFORE_INFO[1], None, "LS_before", _BEFORE_INFO[4]]
    )
    _run(ctx)

    assert len(ctx.downloads) == 2
    assert len({p.name for p in ctx.folder.glob("*.tif")}) == 2


def test_the_same_pick_is_always_the_same_composite():
    """...and the key is stable, or nothing would ever hit that cache."""
    from gui.tile.analysis import paths

    name = paths.alert_image_name("/f", 0, _BEFORE_INFO, _AFTER_INFO)
    assert name == paths.alert_image_name("/f", 0, list(_BEFORE_INFO), list(_AFTER_INFO))
    # The two sides are told apart, not merged: a before/after swap of the
    # same two collections is a different composite.
    landsat_before = ["Landsat", *_BEFORE_INFO[1:]]
    assert paths.alert_image_name("/f", 0, landsat_before, _AFTER_INFO) != name


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


def test_a_failed_composite_download_leaves_no_gpkg_in_the_set_folder(download_ctx, monkeypatch):
    """Deferred T2: the gpkg was copied into the set folder before the
    composite was downloaded, so a failed download left ``alert_0.gpkg`` behind."""
    from gui.tile.analysis import paths

    ctx = download_ctx

    def _failing(*args):
        raise RuntimeError("geedim failed")

    monkeypatch.setattr(paths, "download_both_images", _failing)
    with pytest.raises(RuntimeError):
        _run(ctx)
    assert list(ctx.folder.iterdir()) == []


def test_download_uses_the_users_custom_report_template(download_ctx, data_root):
    """A custom template is the user's own file, read where it is."""
    ctx = download_ctx
    template = data_root / "my_template.docx"
    template.write_bytes(Path(export_mod._DEFAULT_REPORT_TEMPLATE).read_bytes())
    ctx.aux_model.custom_report_template = str(template)

    result = _run(ctx)

    assert Path(result["report_path"]).read_bytes()[:2] == b"PK"
    assert template.exists()


def test_download_waits_for_both_panes_to_draw_their_picks(download_ctx):
    """Same mixture from the export side: the composite is named after the
    picked pair and built from the two ee.Images, so a lane that has not
    finished rebuilding is a build refused, not a file named for one pick out
    of another's pixels."""
    ctx = download_ctx
    ctx.analysis.before_image.set(None)  # the before pane is still rebuilding

    assert _run(ctx) is None
    assert ctx.downloads == [] and list(ctx.folder.iterdir()) == []


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
        ctx.widgets, ctx.analysis, ctx.app_state, ctx.aux_model, ctx.app_tile_model,
        task=first,
    )
    assert first.progress == [0.5, 1.0, None]

    cached = _Task()
    export_mod._download_data(
        ctx.widgets, ctx.analysis, ctx.app_state, ctx.aux_model, ctx.app_tile_model,
        task=cached,
    )
    assert cached.progress == [None]


def test_a_report_template_gone_from_disk_refuses_before_any_file_is_written(download_ctx):
    ctx = download_ctx
    folder = Path(ctx.app_tile_model.recipe_folder_path)
    ctx.aux_model.custom_report_template = str(folder.parent / "gone.docx")
    before = sorted(p.name for p in folder.iterdir())
    with pytest.raises(export_mod.ReportTemplateMissing):
        _run(ctx)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_the_report_is_built_from_the_projects_template(download_ctx):
    """A marker paragraph in the user's template reaches the report."""
    import docx

    ctx = download_ctx
    folder = Path(ctx.app_tile_model.recipe_folder_path)
    template = folder.parent / "mine.docx"
    doc = docx.Document(export_mod._DEFAULT_REPORT_TEMPLATE)
    doc.add_paragraph("MARKER-6f1c")
    doc.save(template)
    ctx.aux_model.custom_report_template = str(template)
    result = _run(ctx)
    text = "\n".join(p.text for p in docx.Document(result["report_path"]).paragraphs)
    assert "MARKER-6f1c" in text


# ---------------------------------------------------------------------------
# The export section's summary badge (Task 9)
# ---------------------------------------------------------------------------
def _downloads_toolbar(body):
    from pysepal import sepalwidgets as sw

    for widget in _in(body):
        if isinstance(widget, sw.Toolbar) and any(
            isinstance(child, sw.DownloadBtn)
            for child in (getattr(widget, "children", None) or [])
        ):
            return widget
    raise AssertionError("no downloads toolbar in the export section body")


def _download_buttons(body):
    """The two ``sw.DownloadBtn`` of the downloads toolbar, in tree order."""
    from pysepal import sepalwidgets as sw

    return [
        child
        for child in (getattr(_downloads_toolbar(body), "children", None) or [])
        if isinstance(child, sw.DownloadBtn)
    ]


def _project_poke(app_state):
    """A project object to republish ``app_state.project`` with, as
    ``update_project`` does when the report template is picked."""
    from datetime import datetime

    from deforestation_alerts.project.models import Project

    now = datetime.now()
    return Project(name="t", created_at=now, updated_at=now)


def _export_harness(tmp_path, monkeypatch, set_badge=None, defer=False):
    """``build_export``'s arguments, wired so a save and a build run inline.

    What the badge says is imperative, per-alert widget state, so it is read
    through the very paths that change it — the shared ``run_save`` and the
    build button's own click handler — with only the blocking bodies stubbed
    and ``spawn_in_context`` running on the calling thread. No notification
    provider is mounted, so the workers publish to a ``NoopNotifier``.

    ``defer=True`` holds each click's worker in ``harness.held`` instead
    (``harness.run()`` runs the oldest), so a test can land a worker *after*
    an alert change — the ordering an inline worker can never produce.
    """
    app_state = AppState()
    app_state.alerts_gdf.value = _alerts_gdf()
    app_state.actual_alert_id.value = 0
    analysis = AnalysisState()
    widgets = SimpleNamespace(actual_alert_grid=None)
    model = SimpleNamespace(recipe_folder_path=str(tmp_path), custom_report_template=None)

    held = []
    monkeypatch.setattr(
        export_mod,
        "spawn_in_context",
        (lambda target, *a, **k: held.append(target)) if defer
        else (lambda target, *a, **k: target()),
    )
    monkeypatch.setattr(export_mod, "_save_attributes", lambda *a, **k: True)
    monkeypatch.setattr(
        export_mod,
        "_download_data",
        lambda *a, **k: {
            "zip_path": str(tmp_path / "alert_0.zip"),
            "report_path": str(tmp_path / "alert_0.docx"),
        },
    )
    monkeypatch.setattr(export_mod.local_files, "home_relative", str)

    parts = export_mod.build_export(
        widgets=widgets,
        app_state=app_state,
        aux_model=model,
        analysis=analysis,
        app_tile_model=model,
        set_badge=set_badge,
    )
    build_btn = _build_button(parts.body)
    return SimpleNamespace(
        parts=parts,
        analysis=analysis,
        app_state=app_state,
        model=model,
        build_btn=build_btn,
        downloads=_downloads_toolbar(parts.body),
        download_btns=_download_buttons(parts.body),
        save=parts.run_save,
        build=lambda: build_btn.fire_event("click", None),
        held=held,
        run=lambda: held.pop(0)(),
    )


def test_the_export_badge_follows_what_is_available(tmp_path, monkeypatch):
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text)
    )
    # The literal English, not ``t()``/``tn()`` of the same key: a missing or
    # misshapen key renders the truthy placeholder ``⟦key⟧`` on both sides of
    # such a comparison, and the assertion passes on nothing.
    assert seen[-1] == "available after save"

    harness.save()  # a save landed: the build is possible
    assert seen[-1] == "ready to build"

    harness.build()  # the zip and the report are there
    assert seen[-1] == "2 files ready"


def test_the_export_badge_does_not_claim_the_previous_alerts_files(tmp_path, monkeypatch):
    """What this section can offer belongs to ONE alert.

    The save stamp leaked across alerts earlier on this branch; the build
    button and the two download links are the same kind of per-alert state.
    An alert change (``reset_alert``) must take the badge back to "available
    after save", and with it the button that would rebuild the previous
    alert's files and the links that still point at them.
    """
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text)
    )
    harness.save()
    harness.build()
    assert seen[-1] == "2 files ready"

    harness.analysis.reset_alert()  # the reviewer moved on

    assert seen[-1] == "available after save"
    assert harness.build_btn.disabled is True
    assert harness.downloads.viz is False
    # The links themselves, not only the toolbar that hides them: a later
    # reveal must not resurrect the previous alert's zip and report.
    for btn in harness.download_btns:
        assert "alert_0" not in btn.href and btn.disabled is True


def test_a_build_that_lands_after_the_alert_change_offers_nothing(tmp_path, monkeypatch):
    """A worker that finishes after the reviewer moved on applies nothing.

    The build is slow (an imagery download plus the Word report), so the
    reviewer can navigate while it runs. Its apply step is what reveals the
    downloads toolbar, so "the toolbar is hidden" is no protection: left
    unguarded, the stale worker shows the previous alert's zip and report
    under a badge counting them, beside the alert now on screen.
    """
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text),
        defer=True,
    )
    harness.save()
    harness.run()  # the save of this alert landed while it was on screen
    assert harness.build_btn.disabled is False

    harness.build()  # the build starts here...
    harness.analysis.reset_alert()  # ...the reviewer moves on...
    harness.run()  # ...and only now does the build land

    assert harness.downloads.viz is False
    assert harness.build_btn.disabled is True  # not re-enabled by the worker
    assert seen[-1] == "available after save"
    for btn in harness.download_btns:
        assert "alert_0" not in btn.href and btn.disabled is True


def _dead_links(harness):
    """Whether the two download links offer nothing (no path, both disabled)."""
    return all("alert_0" not in btn.href and btn.disabled is True
               for btn in harness.download_btns)


def test_a_later_save_stops_offering_the_files_the_previous_one_built(tmp_path, monkeypatch):
    """The zip and the report are a snapshot of the review that was saved.

    Save -> build -> change the answer -> Save again: the two links and the
    "2 files ready" badge otherwise still hand the reviewer the report built
    from the previous save, for a review that no longer says that. Only an
    alert change used to clear them. The files are not deleted (they stay in
    the set folder) -- they are simply not this review's answer any more, so
    the section goes back to offering the rebuild.
    """
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text)
    )
    harness.save()
    harness.build()
    assert seen[-1] == "2 files ready"

    harness.save()  # the reviewer changed their answer and saved again

    assert seen[-1] == "ready to build"
    assert harness.downloads.viz is False
    assert _dead_links(harness)
    assert harness.build_btn.disabled is False


def test_other_imagery_stops_offering_the_files_built_from_the_old_pair(tmp_path, monkeypatch):
    """The report and the composite in the zip are of the pair they were built
    from, so another before/after pick invalidates them the same way."""
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text)
    )
    harness.save()
    harness.build()
    assert seen[-1] == "2 files ready"

    harness.analysis.before_info.set(["Landsat", 1, None, "Landsat 2026-04-10 ", "_100426"])

    assert seen[-1] == "ready to build"
    assert harness.downloads.viz is False
    assert _dead_links(harness)


def test_another_report_template_stops_offering_the_report_of_the_old_one(tmp_path, monkeypatch):
    """The reviewer's own template is an input of the build too: the ``.docx``
    on the link was written from the previous one."""
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text)
    )
    harness.save()
    harness.build()
    assert seen[-1] == "2 files ready"

    # What ``ReportTemplateField`` does: write the project, which republishes
    # it; the section reads the template off the live ``SetContext``.
    harness.model.custom_report_template = str(tmp_path / "mine.docx")
    harness.app_state.project.value = _project_poke(harness.app_state)

    assert seen[-1] == "ready to build"
    assert harness.downloads.viz is False
    assert _dead_links(harness)


def test_a_build_that_lands_after_a_later_save_offers_nothing(tmp_path, monkeypatch):
    """The same hole from the other side: the build is in flight when the
    inputs change, and its apply step is what reveals the links. Without a
    revision captured at the click it restores a readiness that is already
    stale -- the links would point at files built from the review the save has
    just replaced."""
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text),
        defer=True,
    )
    harness.save()
    harness.run()

    harness.build()        # the build starts here...
    harness.save()
    harness.held.pop()()   # ...a later save of the same alert lands...
    harness.run()          # ...and only now does the build

    assert harness.downloads.viz is False
    assert seen[-1] == "ready to build"
    assert _dead_links(harness)


def test_a_save_that_lands_after_the_alert_change_offers_no_build(tmp_path, monkeypatch):
    """The save path has the same hole, and the set-wide half still runs.

    A late ``_apply_save`` would enable the build button and badge "ready to
    build" for an alert that was never saved. The ``alerts_gdf`` rebind is NOT
    alert-scoped — the store write did land, and Overview's counts read that
    reactive — so it has to happen even when the alert-scoped half is dropped.
    """
    seen = []
    harness = _export_harness(
        tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text),
        defer=True,
    )
    before = harness.app_state.alerts_gdf.value

    harness.save()  # the save starts here...
    harness.analysis.reset_alert()  # ...the reviewer moves on...
    harness.analysis.dirty.set(True)  # ...and edits the alert now on screen
    harness.run()  # ...and only now does the save land

    assert harness.build_btn.disabled is True
    assert seen[-1] == "available after save"
    # The new alert's unsaved edit is still unsaved: the save wrote the
    # previous alert's review, not this one's.
    assert harness.analysis.dirty.value is True
    # ...but the write did happen, so the set's gdf was still re-published.
    assert harness.app_state.alerts_gdf.value is not before
