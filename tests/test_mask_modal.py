"""The Add a mask form's order and band picker (gui/widget/mask_modal): Name,
Source and the picker come first; the bands are read once a raster is picked
and nothing that depends on them shows until they are; one band is a line,
two or more a Band select that starts on the suggested band, else the first."""

import asyncio
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import rasterio
import reacton
import solara
from affine import Affine

from gui.i18n import t
from gui.widget import mask_modal
from gui.widget.form_dialog import FIELDS
from tests.test_masks_card import (
    _StubRasterInput, _gee, _modal, _name_field, _p, _settle, _source, _state, _submit_btn,
)

HANSEN = ["treecover2000", "loss", "gain", "lossyear", "first_b30", "first_b40", "first_b50", "first_b70",
          "last_b30", "last_b40", "last_b50", "last_b70", "datamask"]
ASSET = {"asset_id": "users/x/m", "type": "IMAGE", "column": "ALL", "value": None}
OTHER = {"asset_id": "users/x/other", "type": "IMAGE", "column": "ALL", "value": None}


def _tif(path, descriptions=(None,)):
    with rasterio.open(path, "w", driver="GTiff", width=4, height=4, count=len(descriptions), dtype="uint8",
                       crs="EPSG:32721", transform=Affine(30, 0, 500000, 0, -30, 8900000)) as dst:
        dst.write(np.zeros((len(descriptions), 4, 4), "uint8"))
        for i, d in enumerate(descriptions, start=1):
            if d:
                dst.set_band_description(i, d)
    return path


def _band(rc):
    """The Band select, or None while it is not shown."""
    found = rc.find(v.Select, label=t("steps.prepare.mask_band")).widgets
    return found[0] if found else None


def _texts(rc):
    return " ".join(str(w.children) for w in rc.find(v.Html).widgets)


def _independent_fields_show(rc):
    """The Mode radios and the note never wait for the bands."""
    assert rc.find(v.RadioGroup, v_model="keep").widgets
    assert t("steps.prepare.masks_note") in _texts(rc)


def _render(monkeypatch, gee, p=None, editing=None, validate=None):
    monkeypatch.setattr(mask_modal, "RasterInput", _StubRasterInput)
    if validate is not None:
        monkeypatch.setattr(mask_modal, "validate_mask", validate)
    state, open_, added, saved = _state(p or _p()), solara.reactive(True), [], []
    _box, rc = reacton.render(_modal(state, open_, added, saved, editing=editing, gee=gee), handle_error=False)
    return rc, SimpleNamespace(open_=open_, added=added, saved=saved)


def _recording():
    asked = []

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        return {"error": "steps.alerts.err_missing_band"}

    return asked, fake_validate


def test_mask_form_order(monkeypatch):
    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, ["b"]))
        try:
            [fields] = rc.find(v.Html, style_=FIELDS).widgets
            name, source = _name_field(rc), _source(rc)
            [picker] = rc.find(v.Html, class_="dv-stub-raster-input").widgets
            order = fields.children
            assert order.index(name) < order.index(source) < order.index(picker)
            assert (source.hint, source.persistent_hint) == (t("steps.raster_input.source_hint"), True)
            assert [i["value"] for i in source.items] == ["gee", "file"]
            assert _StubRasterInput.seen.show_origin is False  # the Source select picks the origin
        finally:
            rc.close()

    asyncio.run(main())


def test_nothing_below_the_picker_until_the_bands_are_read(monkeypatch):
    release = asyncio.Event()

    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, HANSEN, release))
        try:
            assert _band(rc) is None  # no raster picked
            _independent_fields_show(rc)
            _StubRasterInput.seen.asset.set(ASSET)
            loading = t("steps.raster_input.bands_loading")
            assert await _settle(lambda: loading in _texts(rc))
            assert _band(rc) is None  # the band list is loading
            _independent_fields_show(rc)
            release.set()
            assert await _settle(lambda: _band(rc) is not None)
            assert loading not in _texts(rc)
            _independent_fields_show(rc)
        finally:
            rc.close()

    asyncio.run(main())


def test_a_failed_band_read_shows_only_its_error(monkeypatch):
    async def get_info_async(obj):
        raise RuntimeError("boom")

    _gee(monkeypatch, [])  # no ee request is built

    async def main():
        rc, _ = _render(monkeypatch, SimpleNamespace(get_info_async=get_info_async))
        try:
            _name_field(rc).v_model = "m"
            _StubRasterInput.seen.asset.set(ASSET)
            shown = t("steps.raster_input.bands_failed", error="boom")
            assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
            assert _band(rc) is None
            assert _submit_btn(rc).disabled is True
            _independent_fields_show(rc)
        finally:
            rc.close()

    asyncio.run(main())


def test_one_band_mask_shows_a_line_and_sets_the_band(tmp_path, monkeypatch):
    path = _tif(tmp_path / "forest.tif")

    async def main():
        rc, seen = _render(monkeypatch, object())
        try:
            _name_field(rc).v_model = "forest1"
            _source(rc).v_model = "file"
            _StubRasterInput.seen.path.set(str(path))
            line = t("steps.prepare.mask_band_one", name="Band 1")
            assert line == "One band (Band 1): the mask is made from it."
            assert await _settle(lambda: line in _texts(rc))
            assert _band(rc) is None
            assert await _settle(lambda: not _submit_btn(rc).disabled)
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: seen.open_.value is False)
            [(fields, _mode)] = seen.added
            assert (fields["origin"], fields["ref"], fields["band"]) == ("file", str(path), 1)
        finally:
            rc.close()

    asyncio.run(main())


def test_mask_band_defaults_to_the_suggested_band(monkeypatch):
    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, HANSEN))
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == "datamask")
            band = _band(rc)
            texts = [i["text"] for i in band.items]
            assert texts[-1] == t("steps.layers.suggested", text="datamask") == "datamask (suggested)"
            assert texts[:-1] == HANSEN[:-1]  # only the suggested band is marked
            assert [i["value"] for i in band.items] == HANSEN
            assert band.hint == "13 bands. datamask looks like a mask; non-zero pixels are inside it."
            assert band.persistent_hint is True
            assert (band.dense, band.outlined) == (True, True)
        finally:
            rc.close()

    asyncio.run(main())


def test_mask_band_defaults_to_the_first_band(monkeypatch):
    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, ["B2", "B3", "B4"]))
        try:
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == "B2")
            band = _band(rc)
            assert [i["text"] for i in band.items] == ["B2", "B3", "B4"]
            assert band.hint == "3 bands. Pick the one whose non-zero pixels mark the mask."
        finally:
            rc.close()

    asyncio.run(main())


def test_geotiff_mask_band_value_is_the_index(tmp_path, monkeypatch):
    path = _tif(tmp_path / "mdd_masks_2024.tif", ("urban", "water_mask", "cloud_mask"))

    async def main():
        rc, _ = _render(monkeypatch, object())
        try:
            _source(rc).v_model = "file"
            _StubRasterInput.seen.path.set(str(path))
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == 2)
            assert _band(rc).items == [{"text": "urban", "value": 1},
                                       {"text": "water_mask (suggested)", "value": 2},
                                       {"text": "cloud_mask", "value": 3}]
        finally:
            rc.close()

    asyncio.run(main())


def test_mask_edit_keeps_its_saved_band(monkeypatch):
    # _p()'s mask "forest" is Earth Engine "UMD/x", band "b"
    asked, fake_validate = _recording()

    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, ["a", "b", "datamask"]), editing="forest",
                        validate=fake_validate)
        try:
            assert await _settle(lambda: _band(rc) is not None)
            await asyncio.sleep(0.05)
            assert _band(rc).v_model == "b"
            # another raster takes the default rule
            _StubRasterInput.seen.asset.set(OTHER)
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == "datamask")
            _submit_btn(rc, t("steps.alerts.save")).fire_event("click", None)
            assert await _settle(lambda: bool(asked))
            assert (asked[0]["ref"], asked[0]["band"]) == ("users/x/other", "datamask")
        finally:
            rc.close()

    asyncio.run(main())


def test_mask_edit_with_a_missing_band_takes_the_default(monkeypatch):
    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, ["a", "datamask"]), editing="forest")
        try:
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == "datamask")
        finally:
            rc.close()

    asyncio.run(main())


def test_source_change_clears_the_raster_and_band(monkeypatch):
    asked, fake_validate = _recording()

    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, HANSEN), validate=fake_validate)
        try:
            _name_field(rc).v_model = "m"
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _band(rc) is not None and _band(rc).v_model == "datamask")
            _source(rc).v_model = "file"
            assert _StubRasterInput.seen.origin == "file"
            assert _StubRasterInput.seen.asset.value is None and _StubRasterInput.seen.path.value == ""
            assert _band(rc) is None
            _source(rc).v_model = "gee"  # the asset is not picked again
            assert _StubRasterInput.seen.asset.value is None
            await asyncio.sleep(0.05)
            assert _band(rc) is None
            _submit_btn(rc).fire_event("click", None)
            assert await _settle(lambda: bool(asked))
            assert (asked[0]["ref"], asked[0]["band"]) == (None, None)
        finally:
            rc.close()

    asyncio.run(main())


def test_mask_save_disabled_while_the_bands_load(monkeypatch):
    release = asyncio.Event()

    async def main():
        rc, _ = _render(monkeypatch, _gee(monkeypatch, HANSEN, release))
        try:
            _name_field(rc).v_model = "m"
            await asyncio.sleep(0.05)
            assert _submit_btn(rc).disabled is False  # nothing picked: Add says what is missing
            _StubRasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: t("steps.raster_input.bands_loading") in _texts(rc))
            assert _submit_btn(rc).disabled is True
            release.set()
            assert await _settle(lambda: _band(rc) is not None)
            assert _submit_btn(rc).disabled is False
        finally:
            rc.close()

    asyncio.run(main())
