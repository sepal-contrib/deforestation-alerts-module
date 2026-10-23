"""The shared user-raster input (gui/widget/raster_input.py): the picker follows
the origin the caller's Source select sets, and the band-list helpers."""

from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara

from gui.i18n import t
from gui.widget import raster_input


@pytest.fixture
def pickers(monkeypatch):
    """pysepal's pickers list the user's assets and folders: recorders instead."""
    ns = SimpleNamespace(asset_types=None, file_label=None)

    @solara.component
    def _Asset(types, value, gee_interface):
        ns.asset_types = types

    @solara.component
    def _File(extensions, value, label):
        ns.file_label = label

    monkeypatch.setattr(raster_input, "AssetSelectComponent", _Asset)
    monkeypatch.setattr(raster_input, "FileInputComponent", _File)
    return ns


@solara.component
def _Harness(origin="gee", disabled=False):
    raster_input.RasterInput(gee_interface=None, origin=origin, on_origin=lambda _o: None,
                             asset=solara.use_reactive(None), path=solara.use_reactive(""), disabled=disabled)


def test_the_input_is_the_picker_alone_and_follows_the_origin(pickers):
    _box, rc = reacton.render(_Harness(), handle_error=False)
    try:
        assert not rc.find(v.Select).widgets and not rc.find(v.RadioGroup).widgets
        assert pickers.asset_types == ["IMAGE", "IMAGE_COLLECTION"] and pickers.file_label is None
    finally:
        rc.close()
    _box, rc = reacton.render(_Harness(origin="file"), handle_error=False)
    try:
        assert not rc.find(v.Select).widgets
        assert pickers.file_label == t("steps.raster_input.file")
    finally:
        rc.close()


def test_a_disabled_input_blocks_the_picker(pickers):
    _box, rc = reacton.render(_Harness(disabled=True), handle_error=False)
    try:
        assert rc.find(v.Html, style_="pointer-events:none;opacity:0.6;").widgets
    finally:
        rc.close()


# ------------------------------------------- the band list's shared helpers
def _task(pending=False, finished=False, error=False, value=None, exception=None):
    """A ``solara.lab.use_task`` result as the helpers read it."""
    return SimpleNamespace(pending=pending, finished=finished, error=error, value=value, exception=exception)


READING = _task(pending=True)
FAILED = _task(error=True, exception=RuntimeError("boom"))
READ = _task(finished=True, value=(("b1", "b2"), None))


def test_bands_read_is_a_finished_non_empty_list():
    assert raster_input.bands_read(READ) is True
    assert raster_input.bands_read(READING) is False
    assert raster_input.bands_read(FAILED) is False
    assert raster_input.bands_read(_task(finished=True, value=((), None))) is False  # nothing picked
    assert raster_input.bands_read(_task(finished=True, value=None)) is False


@solara.component
def _Status(task, ref):
    with solara.Column():
        raster_input.band_status(task, ref)


def _status_texts(task, ref):
    _box, rc = reacton.render(_Status(task, ref), handle_error=False)
    try:
        return [(w.children, w.class_ or None) for w in rc.find(v.Html).widgets
                if w.children and isinstance(w.children[0], str)]
    finally:
        rc.close()


def test_band_status_says_where_the_band_list_stands():
    loading = t("steps.raster_input.bands_loading")
    assert _status_texts(READING, "users/x/m") == [([loading], None)]
    failed = t("steps.raster_input.bands_failed", error="boom")
    assert _status_texts(FAILED, "users/x/m") == [([failed], "error--text")]
    assert _status_texts(READ, "users/x/m") == []


def test_band_status_shows_nothing_without_a_picked_raster():
    # the task is pending for one render after the raster is cleared (mount, a
    # Source change): no "Reading the bands…" for a raster nobody picked
    assert _status_texts(READING, None) == []
    assert _status_texts(READING, "") == []
    assert _status_texts(FAILED, None) == []


def test_band_choices_are_names_in_earth_engine_and_indexes_in_a_geotiff():
    assert raster_input.band_choices(("B2", "B3"), None) == [("B2", "B2"), ("B3", "B3")]
    header = SimpleNamespace(bands=[SimpleNamespace(name="Band 1", index=1), SimpleNamespace(name="water", index=2)])
    assert raster_input.band_choices(("Band 1", "water"), header) == [("Band 1", 1), ("water", 2)]
    assert raster_input.band_choices((), None) == []


def test_bands_block_save_while_they_load_or_after_they_fail():
    assert raster_input.bands_block_save(READING, "users/x/m") is True
    assert raster_input.bands_block_save(FAILED, "users/x/m") is True
    assert raster_input.bands_block_save(READ, "users/x/m") is False
    # nothing picked: the form's own check says what is missing
    assert raster_input.bands_block_save(READING, None) is False


# ------------------------------------------------------------ the Source select
def _source_items(**kwargs):
    _box, rc = reacton.render(raster_input.SourceSelect(origin="gee", on_origin=lambda _o: None, **kwargs),
                              handle_error=False)
    try:
        return rc.find(v.Select, label=t("steps.raster_input.field_source")).widget.items
    finally:
        rc.close()


def test_the_source_select_offers_earth_engine_then_geotiff_by_default():
    assert _source_items() == [{"text": t("steps.raster_input.source_gee"), "value": "gee"},
                               {"text": t("steps.raster_input.source_file"), "value": "file"}]


def test_the_source_select_lists_its_origins_in_the_given_order():
    assert _source_items(origins=("file", "gee")) == [{"text": t("steps.raster_input.source_file"), "value": "file"},
                                                      {"text": t("steps.raster_input.source_gee"), "value": "gee"}]
    assert _source_items(origins=("file",)) == [{"text": t("steps.raster_input.source_file"), "value": "file"}]
