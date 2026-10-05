"""The shared user-raster input (gui/widget/raster_input.py): the origin is an
outlined Select with a hint (spec D2), and the picker follows it."""

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
def _Harness(initial="gee", disabled=False, show_origin=True, seen=None):
    origin, set_origin = solara.use_state(initial)

    def on_origin(new):
        if seen is not None:
            seen.append(new)
        set_origin(new)

    raster_input.RasterInput(gee_interface=None, origin=origin, on_origin=on_origin,
                             asset=solara.use_reactive(None), path=solara.use_reactive(""),
                             disabled=disabled, show_origin=show_origin)


def _origin(rc):
    return rc.find(v.Select, label=t("steps.raster_input.origin_label")).widget


def test_the_origin_is_an_outlined_select_with_a_hint(pickers):
    box, rc = reacton.render(_Harness(), handle_error=False)
    try:
        select = _origin(rc)
        assert select.v_model == "gee" and select.outlined is True and select.dense is True
        assert select.items == [{"text": t("steps.raster_input.origin_gee"), "value": "gee"},
                                {"text": t("steps.raster_input.origin_file"), "value": "file"}]
        assert select.hint == t("steps.raster_input.origin_hint") and select.persistent_hint is True
        assert select.disabled is False
        assert not rc.find(v.RadioGroup).widgets
        assert pickers.asset_types == ["IMAGE", "IMAGE_COLLECTION"] and pickers.file_label is None
    finally:
        rc.close()


def test_picking_the_origin_switches_the_picker(pickers):
    seen = []
    box, rc = reacton.render(_Harness(seen=seen), handle_error=False)
    try:
        _origin(rc).v_model = "file"
        assert seen == ["file"] and _origin(rc).v_model == "file"
        assert pickers.file_label == t("steps.raster_input.file")
    finally:
        rc.close()


def test_a_disabled_input_disables_the_origin_and_blocks_the_picker(pickers):
    box, rc = reacton.render(_Harness(disabled=True), handle_error=False)
    try:
        assert _origin(rc).disabled is True
        assert rc.find(v.Html, style_="pointer-events:none;opacity:0.6;").widgets
    finally:
        rc.close()


def test_without_the_origin_only_the_picker_is_shown(pickers):
    box, rc = reacton.render(_Harness(initial="file", show_origin=False), handle_error=False)
    try:
        assert not rc.find(v.Select).widgets
        assert pickers.file_label == t("steps.raster_input.file")
    finally:
        rc.close()
