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

from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import export as export_mod
from gui.tile.analysis import alert_files
from deforestation_alerts.scripts import local_files
from gui.tile.analysis.state import AnalysisState
# ``_in`` / ``_build_button`` / ``_alerts_gdf`` live in conftest: the assembled
# panel's tests (tests/test_analysis_sections.py) drive the same export section
# through ``build_controls`` and need them too.
from tests.conftest import _alerts_gdf, _build_button, _in, _rebuild_button, _shown

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
    template.write_bytes(Path(alert_files.DEFAULT_REPORT_TEMPLATE).read_bytes())
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
    with pytest.raises(alert_files.ReportTemplateMissing):
        _run(ctx)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_the_report_is_built_from_the_projects_template(download_ctx):
    """A marker paragraph in the user's template reaches the report."""
    import docx

    ctx = download_ctx
    folder = Path(ctx.app_tile_model.recipe_folder_path)
    template = folder.parent / "mine.docx"
    doc = docx.Document(alert_files.DEFAULT_REPORT_TEMPLATE)
    doc.add_paragraph("MARKER-6f1c")
    doc.save(template)
    ctx.aux_model.custom_report_template = str(template)
    result = _run(ctx)
    text = "\n".join(p.text for p in docx.Document(result["report_path"]).paragraphs)
    assert "MARKER-6f1c" in text


def test_the_download_labels_the_questions_and_fills_their_placeholders(download_ctx):
    """The gpkg row carries current questions' columns as labels and not a
    deleted question's; the report fills {verdict} and one {key} per question."""
    import docx
    import geopandas as gpd

    from deforestation_alerts.project.models import default_questions
    from gui.scripts import questions as questions_mod

    ctx = download_ctx
    template = ctx.folder.parent / "mine.docx"
    doc = docx.Document(alert_files.DEFAULT_REPORT_TEMPLATE)
    doc.add_paragraph("PH {verdict} / {loss_driver}")
    doc.save(template)
    ctx.aux_model.custom_report_template = str(template)
    gdf = ctx.app_state.alerts_gdf.value.copy()
    gdf["description"] = "logging, roads"
    gdf["status"] = "Confirmed"
    gdf["q_gone"] = "x"
    ctx.app_state.alerts_gdf.value = gdf
    alert = export_mod.capture_alert(ctx.analysis, ctx.widgets, ctx.app_state)
    alert.questions = default_questions()

    result = export_mod._download_data(
        ctx.widgets, ctx.analysis, ctx.app_state, ctx.aux_model, ctx.app_tile_model, alert=alert
    )

    row = gpd.read_file(ctx.folder / "alert_0.gpkg").iloc[0]
    assert row["description"] == questions_mod.label(default_questions()[0], "logging, roads")
    assert "q_gone" not in gpd.read_file(ctx.folder / "alert_0.gpkg").columns
    text = "\n".join(p.text for p in docx.Document(result["report_path"]).paragraphs)
    assert f"PH True alert / {row['description']}" in text


# ---------------------------------------------------------------------------
# The export section's summary badge (Task 9)
# ---------------------------------------------------------------------------
def _downloads_toolbar(body):
    """The container of the S1 file tiles (built hidden, shown by a build)."""
    from gui.tile.analysis.file_tile import FileTile

    for widget in _in(body):
        if hasattr(widget, "viz") and any(
            isinstance(child, FileTile)
            for child in (getattr(widget, "children", None) or [])
        ):
            return widget
    raise AssertionError("no downloads toolbar in the export section body")


def _download_buttons(body):
    """The two file tiles a build sets (the zip and the report), in tree order.
    The GIF tile sits outside their container: it is shown in every state,
    and linked by Export GIF as well as by a Confirmed alert's build."""
    from gui.tile.analysis.file_tile import FileTile

    tiles = [
        child
        for child in (getattr(_downloads_toolbar(body), "children", None) or [])
        if isinstance(child, FileTile)
    ]
    assert [tile.kind for tile in tiles] == ["zip", "docx"]
    return tiles


def _project_poke(app_state):
    """A project object to republish ``app_state.project`` with, as
    ``update_project`` does when the report template is picked."""
    from datetime import datetime

    from deforestation_alerts.project.models import Project

    now = datetime.now()
    return Project(name="t", created_at=now, updated_at=now)


def _export_harness(tmp_path, monkeypatch, set_badge=None, defer=False, labels=None):
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
    # Serve tmp_path, so the built files get a real link.
    monkeypatch.setenv(local_files.DOWNLOAD_ROOT_ENV, str(tmp_path))

    parts = export_mod.build_export(
        widgets=widgets,
        app_state=app_state,
        aux_model=model,
        analysis=analysis,
        app_tile_model=model,
        set_badge=set_badge,
        labels=labels,
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


def _strings(widget):
    out = []
    for c in getattr(widget, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def _state(harness):
    """What the export body shows, per S1 state (the mockup's three rows)."""
    body = harness.parts.body
    zip_tile, docx_tile, gif_tile = harness.parts.tiles
    hint = next(w for w in _in(harness.build_btn) if "(save first)" in (getattr(w, "children", None) or []))
    rebuild = _rebuild_button(body)
    return SimpleNamespace(
        build=_shown(body, harness.build_btn), build_enabled=not harness.build_btn.disabled,
        hint=_shown(body, hint) if _shown(body, harness.build_btn) else False,
        files=_shown(body, zip_tile) and _shown(body, docx_tile),
        gif=_shown(body, gif_tile) and gif_tile.disabled,
        rebuild=_shown(body, rebuild) and not rebuild.disabled,
    )


def test_the_export_body_follows_the_s1_states(tmp_path, monkeypatch):
    """Spec D6 / the S1 mockup: BEFORE A SAVE the dashed tile is disabled and
    says "(save first)"; SAVED it builds; READY it gives way to the file tiles
    and a small *Rebuild* under them. The GIF tile is there in every state,
    locked (the alert is not saved Confirmed here)."""
    (tmp_path / "alert_0.zip").write_bytes(b"z" * 3072)
    harness = _export_harness(tmp_path, monkeypatch)
    zip_tile, docx_tile, _gif = harness.parts.tiles
    label = [c for c in harness.build_btn.children if isinstance(c, str)]
    assert label == ["Build alert files"]
    assert harness.build_btn.children[0].children == ["mdi-cog"]
    assert _strings(_rebuild_button(harness.parts.body)) == ["mdi-refresh", "Rebuild files"]

    before = _state(harness)
    assert (before.build, before.build_enabled, before.hint) == (True, False, True)
    assert (before.files, before.gif, before.rebuild) == (False, True, False)

    harness.save()
    saved = _state(harness)
    assert (saved.build, saved.build_enabled, saved.hint) == (True, True, False)
    assert (saved.files, saved.gif, saved.rebuild) == (False, True, False)

    harness.build()
    ready = _state(harness)
    assert (ready.build, ready.files, ready.gif, ready.rebuild) == (False, True, True, True)
    assert zip_tile.disabled is False and zip_tile.href.endswith("alert_0.zip")
    assert docx_tile.disabled is False and docx_tile.href.endswith("alert_0.docx")
    assert zip_tile.attributes["download"] == "alert_0.zip"
    assert zip_tile.size_text == "3 KB"
    assert docx_tile.size_text == ""  # not on disk in this harness: no size, still linked

    harness.save()  # a new save invalidates the files: back to the build tile
    assert _state(harness) == saved
    assert zip_tile.size_text == "" and zip_tile.disabled is True

    harness.build()
    harness.analysis.reset_alert()  # another alert: back to before a save
    assert _state(harness) == before


def test_rebuild_runs_the_build_again(tmp_path, monkeypatch):
    """*Rebuild* does what the build tile did: the same build, and the
    section stays READY with the new files."""
    harness = _export_harness(tmp_path, monkeypatch)
    builds = []
    real = export_mod._download_data
    monkeypatch.setattr(export_mod, "_download_data", lambda *a, **k: builds.append(1) or real(*a, **k))
    harness.save()
    harness.build()
    rebuild = _rebuild_button(harness.parts.body)

    rebuild.fire_event("click", None)

    assert builds == [1, 1]
    assert _state(harness).rebuild and _state(harness).files
    assert rebuild.loading is False and rebuild.disabled is False


def test_a_template_change_takes_ready_back_to_the_build_tile(tmp_path, monkeypatch):
    harness = _export_harness(tmp_path, monkeypatch)
    harness.save()
    harness.build()
    assert _state(harness).rebuild

    harness.model.custom_report_template = str(tmp_path / "mine.docx")
    harness.app_state.project.value = _project_poke(harness.app_state)

    state = _state(harness)
    assert (state.build, state.build_enabled, state.files, state.rebuild) == (True, True, False, False)


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


# ---------------------------------------------------------------------------
# The GIF (spec D9): in the zip, and on its own button
# ---------------------------------------------------------------------------
_GIF_NAME = "alert_0_s_010124_s_020224.gif"


def _gif_job():
    return SimpleNamespace(before_info=_BEFORE_INFO, after_info=_AFTER_INFO, frames=(1, 2, 3))


def _fake_build_gif(job, dest):
    Path(dest).write_bytes(b"GIF89a")
    return Path(dest)


def test_a_confirmed_alerts_zip_carries_its_gif(download_ctx, monkeypatch):
    from gui.tile.analysis import gif as gif_mod

    monkeypatch.setattr(gif_mod, "build_gif", _fake_build_gif)
    download_ctx.app_state.alerts_gdf.value["status"] = "Confirmed"
    result = export_mod._download_data(download_ctx.widgets, download_ctx.analysis, download_ctx.app_state,
                                       download_ctx.aux_model, download_ctx.app_tile_model, gif=_gif_job())
    assert result["gif_path"] == f"{download_ctx.folder}/{_GIF_NAME}"
    assert _GIF_NAME in zipfile.ZipFile(download_ctx.folder / "alert_0.zip").namelist()


def test_no_gif_for_an_alert_not_saved_as_confirmed(download_ctx, monkeypatch):
    from gui.tile.analysis import gif as gif_mod

    called = []
    monkeypatch.setattr(gif_mod, "build_gif", lambda *a: called.append(a))
    download_ctx.app_state.alerts_gdf.value["status"] = "Need revision"
    result = export_mod._download_data(download_ctx.widgets, download_ctx.analysis, download_ctx.app_state,
                                       download_ctx.aux_model, download_ctx.app_tile_model, gif=_gif_job())
    assert called == [] and "gif_path" not in result
    assert _GIF_NAME not in zipfile.ZipFile(download_ctx.folder / "alert_0.zip").namelist()


def test_a_failed_gif_still_builds_the_zip_and_says_so(download_ctx, monkeypatch):
    from gui.tile.analysis import gif as gif_mod

    def _boom(job, dest):
        raise RuntimeError("thumbnail service down")

    monkeypatch.setattr(gif_mod, "build_gif", _boom)
    download_ctx.app_state.alerts_gdf.value["status"] = "Confirmed"
    result = export_mod._download_data(download_ctx.widgets, download_ctx.analysis, download_ctx.app_state,
                                       download_ctx.aux_model, download_ctx.app_tile_model, gif=_gif_job())
    assert result["gif_warning"] is True and "gif_path" not in result
    assert sorted(zipfile.ZipFile(download_ctx.folder / "alert_0.zip").namelist()) == sorted(
        [_TIF_NAME, "alert_0.gpkg"])


def _gif_tile(body):
    """The Timelapse tile: the Export GIF button until the GIF exists, then
    its download link (``path`` set)."""
    from gui.tile.analysis.file_tile import FileTile

    return next(w for w in _in(body) if isinstance(w, FileTile) and w.kind == "gif")


def _gif_harness(tmp_path, monkeypatch, status=None, **kw):
    """``_export_harness`` with the GIF stubbed and the pair picked.

    ``status`` writes the row's verdict and saved pair BEFORE the picks land,
    as for an alert reopened on its saved pair (no save in this session)."""
    from gui.tile.analysis import gif as gif_mod

    harness = _export_harness(tmp_path, monkeypatch, **kw)
    monkeypatch.setattr(gif_mod, "capture_gif", lambda *a, **k: _gif_job())
    monkeypatch.setattr(gif_mod, "build_gif", _fake_build_gif)
    if status is not None:
        _status(harness, status)
    harness.analysis.before_info.set(_BEFORE_INFO)
    harness.analysis.after_info.set(_AFTER_INFO)
    harness.gif_tile = _gif_tile(harness.parts.body)
    harness.make_gif = lambda: harness.gif_tile.fire_event("click", None)
    return harness


def _status(harness, value):
    """What a save of the current picks writes into the row: the verdict and
    the saved pair (``info[3]``), which the GIF gate compares the picks with."""
    gdf = harness.app_state.alerts_gdf.value
    gdf["status"] = value
    gdf["before_img"] = _BEFORE_INFO[3]
    gdf["after_img"] = _AFTER_INFO[3]


def test_the_gif_button_waits_for_a_confirmed_save(tmp_path, monkeypatch):
    harness = _gif_harness(tmp_path, monkeypatch)
    assert harness.gif_tile.disabled  # nothing saved yet
    _status(harness, "Need revision")
    harness.save()
    assert harness.gif_tile.disabled
    _status(harness, "Confirmed")
    harness.save()
    assert not harness.gif_tile.disabled
    harness.analysis.reset_alert()  # another alert
    assert harness.gif_tile.disabled


def test_the_gif_is_offered_on_its_own_link_and_counted(tmp_path, monkeypatch):
    seen = []
    harness = _gif_harness(tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text))
    _status(harness, "Confirmed")
    harness.save()
    harness.make_gif()
    assert harness.gif_tile.path is not None
    assert harness.gif_tile.attributes["download"] == _GIF_NAME
    assert seen[-1] == "1 file ready"
    assert not harness.gif_tile.disabled and not harness.gif_tile.loading


def test_a_later_false_positive_save_withdraws_the_gif(tmp_path, monkeypatch):
    """Review Focus #3."""
    harness = _gif_harness(tmp_path, monkeypatch)
    _status(harness, "Confirmed")
    harness.save()
    harness.make_gif()
    _status(harness, "False Positive")
    harness.save()
    assert harness.gif_tile.path is None and harness.gif_tile.disabled


def test_a_gif_that_lands_after_the_alert_change_offers_nothing(tmp_path, monkeypatch):
    """Review Focus #4."""
    harness = _gif_harness(tmp_path, monkeypatch, defer=True)
    _status(harness, "Confirmed")
    harness.save()
    harness.run()  # the save's worker
    harness.make_gif()
    assert harness.gif_tile.disabled and harness.gif_tile.loading
    harness.analysis.reset_alert()
    harness.run()  # the GIF lands for the alert that is gone
    assert harness.gif_tile.path is None
    assert harness.gif_tile.disabled and not harness.gif_tile.loading


def test_a_gif_still_rendering_does_not_hold_the_next_alerts_button(tmp_path, monkeypatch):
    """The gate is held while a GIF renders. A save of the NEXT alert that
    lands meanwhile is gated on that alert's own state once the old GIF ends,
    so the button is not left disabled over a Confirmed save."""
    harness = _gif_harness(tmp_path, monkeypatch, defer=True)
    _status(harness, "Confirmed")
    harness.save()
    harness.run()
    harness.make_gif()  # the GIF of this alert starts rendering...
    harness.analysis.reset_alert()  # ...the reviewer moves on...
    harness.analysis.before_info.set(_BEFORE_INFO)
    harness.analysis.after_info.set(_AFTER_INFO)
    harness.save()
    harness.held.pop()()  # ...and saves the next one as Confirmed
    assert harness.gif_tile.disabled  # held while the old GIF renders
    harness.run()  # the old GIF lands, stale
    assert harness.gif_tile.path is None
    assert not harness.gif_tile.disabled and not harness.gif_tile.loading


def test_the_zip_build_of_a_confirmed_alert_offers_its_gif_too(tmp_path, monkeypatch):
    seen = []
    harness = _gif_harness(tmp_path, monkeypatch, set_badge=lambda text, color=None: seen.append(text))
    monkeypatch.setattr(export_mod, "_download_data", lambda *a, **k: {
        "zip_path": str(tmp_path / "alert_0.zip"), "report_path": str(tmp_path / "alert_0.docx"),
        "gif_path": str(tmp_path / _GIF_NAME)})
    _status(harness, "Confirmed")
    harness.save()
    harness.build()
    assert harness.gif_tile.path is not None
    assert seen[-1] == "3 files ready"


def test_a_zip_build_with_nothing_to_animate_still_offers_the_files_and_says_so(tmp_path, monkeypatch):
    """Spec D9: a GIF that cannot be made costs the GIF, not the build."""
    from pysepal.solara.notifications import NoopNotifier

    from gui.tile.analysis import gif as gif_mod

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.warnings = []

        def warning(self, message, *, timeout=None):
            self.warnings.append(message)

    notifier = _Notifier()
    harness = _gif_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(export_mod, "current_notifier", lambda: notifier)
    passed = {}

    def _refuse(*a, **k):
        raise gif_mod.GifRefused(key="analysis_tile.export_labels.gif_no_images")

    def _download(*a, **k):
        passed["gif"] = k.get("gif")
        return {"zip_path": str(tmp_path / "alert_0.zip"), "report_path": str(tmp_path / "alert_0.docx")}

    monkeypatch.setattr(gif_mod, "capture_gif", _refuse)
    monkeypatch.setattr(export_mod, "_download_data", _download)
    _status(harness, "Confirmed")
    harness.save()
    harness.build()
    assert passed["gif"] is None
    assert not harness.download_btns[0].disabled and harness.gif_tile.path is None
    assert notifier.warnings == [
        "The alert files are ready, but the GIF could not be made, so the zip has no GIF."]


def test_the_gif_button_follows_the_saved_pair(tmp_path, monkeypatch):
    """Spec D9: the GIF animates the reviewed pair only. Another pick after
    the Confirmed save disables it; picking the saved pair back enables it."""
    harness = _gif_harness(tmp_path, monkeypatch)
    _status(harness, "Confirmed")
    harness.save()
    assert not harness.gif_tile.disabled
    harness.analysis.before_info.set(["Landsat", 1, None, "LS_other", "_100426"])
    assert harness.gif_tile.disabled
    harness.analysis.before_info.set(_BEFORE_INFO)
    assert not harness.gif_tile.disabled


def test_a_row_without_a_saved_pair_offers_no_gif(tmp_path, monkeypatch):
    """A missing or empty saved pick is no match: the gate stays off."""
    import numpy as np

    harness = _gif_harness(tmp_path, monkeypatch)
    _status(harness, "Confirmed")
    harness.app_state.alerts_gdf.value["after_img"] = np.nan
    harness.save()
    assert harness.gif_tile.disabled


def test_a_gif_capture_that_crashes_still_builds_the_zip(tmp_path, monkeypatch):
    """Spec D9: a failed GIF does not fail the build -- not even one whose
    capture raises something other than ``GifRefused`` at the click."""
    from pysepal.solara.notifications import NoopNotifier

    from gui.tile.analysis import gif as gif_mod

    class _Notifier(NoopNotifier):
        def __init__(self):
            self.warnings = []

        def warning(self, message, *, timeout=None):
            self.warnings.append(message)

    notifier = _Notifier()
    harness = _gif_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(export_mod, "current_notifier", lambda: notifier)

    def _crash(*a, **k):
        raise RuntimeError("scene index is malformed")

    monkeypatch.setattr(gif_mod, "capture_gif", _crash)
    _status(harness, "Confirmed")
    harness.save()
    harness.build()
    assert not harness.download_btns[0].disabled and harness.downloads.viz is True
    assert not harness.build_btn.disabled and not harness.build_btn.loading
    assert harness.gif_tile.path is None
    assert notifier.warnings == [
        "The alert files are ready, but the GIF could not be made, so the zip has no GIF."]


class _Toasts:
    """A notifier that keeps the warnings and errors it is given."""

    def __new__(cls):
        from pysepal.solara.notifications import NoopNotifier

        class _Notifier(NoopNotifier):
            def __init__(self):
                self.warnings, self.errors = [], []

            def warning(self, message, *, timeout=None):
                self.warnings.append(message)

            def error(self, message, *, timeout=None):
                self.errors.append(message)

        return _Notifier()


def test_a_reopened_true_alert_offers_its_gif_without_a_new_save(tmp_path, monkeypatch):
    """Spec D9 (amended): the gate reads the row, not this session's saves. An
    alert saved Confirmed on its pair, reopened on that pair, offers Export
    GIF at once; the badge counts the GIF it makes."""
    seen = []
    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed",
                           set_badge=lambda text, color=None: seen.append(text))
    assert not harness.gif_tile.disabled
    assert harness.build_btn.disabled  # "Download alert data" keeps its own rule
    harness.make_gif()
    assert harness.gif_tile.path is not None
    assert harness.gif_tile.attributes["download"] == _GIF_NAME
    assert seen[-1] == "1 file ready"


def test_a_reopened_alert_not_saved_confirmed_offers_no_gif(tmp_path, monkeypatch):
    harness = _gif_harness(tmp_path, monkeypatch, status="Need revision")
    assert harness.gif_tile.disabled


def test_a_gif_capture_that_crashes_is_said_and_releases_the_button(tmp_path, monkeypatch):
    """A capture failing with something other than ``GifRefused`` at the
    click: a failed task with an error toast, the button released and gated
    again -- not a click that silently does nothing."""
    from gui.tile.analysis import gif as gif_mod

    notifier = _Toasts()
    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed")
    monkeypatch.setattr(export_mod, "current_notifier", lambda: notifier)

    def _crash(*a, **k):
        raise RuntimeError("scene index is malformed")

    monkeypatch.setattr(gif_mod, "capture_gif", _crash)
    harness.make_gif()
    assert notifier.errors and "scene index is malformed" in notifier.errors[0]
    assert not harness.gif_tile.loading
    assert not harness.gif_tile.disabled  # the gate decided again: still a true alert
    assert harness.gif_tile.path is None


def test_a_template_change_leaves_the_gif_alone(tmp_path, monkeypatch):
    """Spec D9: the GIF does not depend on the report template."""
    seen = []
    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed",
                           set_badge=lambda text, color=None: seen.append(text))
    harness.make_gif()
    harness.model.custom_report_template = str(tmp_path / "mine.docx")
    harness.app_state.project.value = _project_poke(harness.app_state)
    assert harness.gif_tile.path is not None and harness.gif_tile.attributes["download"] == _GIF_NAME
    assert seen[-1] == "1 file ready"


def test_a_template_change_does_not_drop_a_gif_still_rendering(tmp_path, monkeypatch):
    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed", defer=True)
    harness.make_gif()  # the GIF starts rendering...
    harness.model.custom_report_template = str(tmp_path / "mine.docx")
    harness.app_state.project.value = _project_poke(harness.app_state)  # ...the template changes...
    harness.run()  # ...and the GIF lands
    assert harness.gif_tile.path is not None
    assert not harness.gif_tile.disabled and not harness.gif_tile.loading


def test_the_zip_of_a_true_alert_with_an_unsaved_pair_has_no_gif_and_says_so(tmp_path, monkeypatch):
    """Spec D9: the zip follows the button's rule. A Confirmed row whose pair
    on screen is not the saved pair gets no GIF capture, and one warning."""
    notifier = _Toasts()
    harness = _gif_harness(tmp_path, monkeypatch)
    monkeypatch.setattr(export_mod, "current_notifier", lambda: notifier)
    passed = {}

    def _download(*a, **k):
        passed["gif"] = k.get("gif")
        return {"zip_path": str(tmp_path / "alert_0.zip"), "report_path": str(tmp_path / "alert_0.docx")}

    monkeypatch.setattr(export_mod, "_download_data", _download)
    _status(harness, "Confirmed")
    harness.save()
    harness.analysis.before_info.set(["Landsat", 1, None, "LS_other", "_100426"])  # not saved
    harness.build()
    assert passed["gif"] is None
    assert notifier.warnings == [
        "The alert files are ready, but the GIF could not be made, so the zip has no GIF."]
    assert harness.gif_tile.path is None


def test_a_gif_that_lands_after_another_pick_offers_nothing(tmp_path, monkeypatch):
    """Stale by revision, not by alert: the pair changed while it rendered."""
    notifier = _Toasts()
    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed", defer=True)
    monkeypatch.setattr(export_mod, "current_notifier", lambda: notifier)
    harness.make_gif()  # the GIF starts rendering...
    harness.analysis.before_info.set(["Landsat", 1, None, "LS_other", "_100426"])  # ...another pick...
    harness.run()  # ...and only now does it land
    assert harness.gif_tile.path is None
    assert notifier.warnings == [export_mod.t("analysis_tile.export_labels.gif_stale")]
    assert not harness.gif_tile.loading
    assert harness.gif_tile.disabled  # the new pair is not the saved one
    harness.analysis.before_info.set(_BEFORE_INFO)  # the saved pair back
    assert not harness.gif_tile.disabled


def test_the_timelapse_tile_says_why_it_is_locked_and_what_a_click_does(tmp_path, monkeypatch):
    """Spec D9: the GIF animates the saved pair, so the locked tile says the
    images on screen are what has to be saved; an open one says a click
    makes it; a made one is its download link, with its size."""
    def _text(w):
        return " ".join(c if isinstance(c, str) else _text(c) for c in (getattr(w, "children", None) or []))

    harness = _gif_harness(tmp_path, monkeypatch)
    tile = harness.gif_tile
    assert tile.disabled and tile.href is None
    assert "Save as a true alert, with the images on screen, to create it" in _text(tile)
    _status(harness, "Confirmed")
    harness.save()
    assert not tile.disabled and tile.href is None and "click to create it" in _text(tile)
    harness.make_gif()
    assert tile.path is not None and tile.href and "mdi-download" in _text(tile)
    assert "Animated GIF · 6 B" in _text(tile)


def test_a_click_on_the_gif_link_makes_no_new_gif(tmp_path, monkeypatch):
    """Once made, the tile is the download link: its click is the browser's."""
    from gui.tile.analysis import gif as gif_mod

    harness = _gif_harness(tmp_path, monkeypatch, status="Confirmed")
    harness.make_gif()
    calls = []
    monkeypatch.setattr(gif_mod, "capture_gif", lambda *a, **k: calls.append(a))
    harness.make_gif()
    assert calls == [] and harness.gif_tile.path is not None


def test_a_switch_while_a_gif_renders_rewords_the_tile_and_holds_it(tmp_path, monkeypatch):
    from pysepal.i18n import set_locale

    from gui.tile.analysis.labels import Labels

    labels = Labels()
    harness = _gif_harness(tmp_path, monkeypatch, defer=True, labels=labels)
    _status(harness, "Confirmed")
    harness.save()
    while harness.held:
        harness.run()
    assert not harness.gif_tile.disabled
    harness.make_gif()
    assert len(harness.held) == 1 and harness.gif_tile.loading, "the GIF worker is held"
    try:
        set_locale("fr")
        labels.run()
        tile = harness.gif_tile
        assert tile._meta.children == [t("analysis_tile.export_labels.tile_gif_make")]
        assert tile.loading is True and tile.disabled is True and tile.path is None
        harness.run()                        # the GIF lands after the switch
        assert tile.path is not None and not tile.loading
    finally:
        set_locale("en")
