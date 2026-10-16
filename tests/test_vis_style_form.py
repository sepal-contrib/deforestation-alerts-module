"""The visualization form's Bands question, Style tiles and Continuous fields
(gui/widget/vis_style_fields, gui/widget/vis_layer_modal): only a raster of
three or more bands is asked about its bands, a new layer is Continuous, the
stretch fills Min and Max from the AOI, and the legend preview follows the
ramp and the range."""

import asyncio
import logging
from types import SimpleNamespace

import ipyvuetify as v
import reacton

from deforestation_alerts.project.models import GeeVisLayer, VisClass
from gui.i18n import t
from gui.scripts import vis_palettes
from gui.widget import vis_layer_modal as vlm
from tests.test_vis_layers import S2_BANDS, _bands_choice, _field, _flat, _gee_modal, _settle, _with_aoi

ASSET = {"asset_id": "users/x/img", "type": "IMAGE", "column": "ALL", "value": None}


def _style_tiles(rc, selected=False):
    cls = "dv-style--selected" if selected else "dv-style"
    return [b for b in rc.find(v.Btn).widgets if cls in (b.class_ or "").split()]


def _button(rc, key):
    [btn] = [b for b in rc.find(v.Btn).widgets if t(key) in _flat(b)]
    return btn


def _preview(rc):
    [box] = rc.find(v.Html, class_="dv-legend-preview").widgets
    return box


def _ticks(rc):
    return [c.children[0] for c in _preview(rc).children[1].children]


def _bar(rc):
    return _preview(rc).children[0].style_


def _render(state, open_, saved, gee, editing=None):
    return reacton.render(vlm.VisLayerModal(open_=open_, app_state=state, gee_interface=gee,
                                            on_save=lambda layer, prev: saved.append((layer, prev)),
                                            editing=editing), handle_error=False)


def _stretching(monkeypatch, bands, answer):
    """An Earth Engine form whose stretch request is answered by ``answer``."""
    state, open_, saved, _ = _gee_modal(monkeypatch, bands)
    monkeypatch.setattr(vlm.vis_probes, "stretch_request",
                        lambda asset, bs, bounds: ("STRETCH", asset, tuple(bs), tuple(bounds)))

    async def get_info_async(obj):
        if isinstance(obj, tuple) and obj[0] == "STRETCH":
            return await answer(obj)
        return bands

    return state, open_, saved, SimpleNamespace(get_info_async=get_info_async)


def test_a_one_band_image_shows_no_bands_question_and_starts_continuous(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI"])

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            assert _bands_choice(rc) is None
            [tile] = _style_tiles(rc, selected=True)
            assert t("steps.layers.style_continuous") in _flat(tile) and len(_style_tiles(rc)) == 2
            _field(rc, "steps.layers.field_band", v.Select).v_model = "NDVI"
            _field(rc, "steps.layers.field_name").v_model = "ndvi"
            [save] = [b for b in rc.find(v.Btn).widgets if t("steps.layers.add") in b.children]
            save.fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            [(layer, _prev)] = saved
            assert layer.style == "continuous" and layer.bands == ["NDVI"]
        finally:
            rc.close()

    asyncio.run(main())


def test_three_spectral_bands_default_to_rgb_and_other_bands_to_one(monkeypatch):
    for bands, mode in ((S2_BANDS, "all"), (["a", "b", "c"], "one")):
        state, open_, saved, gee = _gee_modal(monkeypatch, bands)

        async def main():
            _box, rc = _render(state, open_, saved, gee)
            try:
                assert _bands_choice(rc) is None  # no band list yet
                vlm.RasterInput.seen.asset.set(ASSET)
                assert await _settle(lambda: _bands_choice(rc) is not None)
                assert _bands_choice(rc).v_model == mode
                assert bool(_style_tiles(rc)) is (mode == "one")  # RGB has no Style tiles
            finally:
                rc.close()

        asyncio.run(main())


def test_the_stretch_fills_min_and_max(monkeypatch):
    asked = []

    async def answer(obj):
        asked.append(obj)
        return {"NDVI_p2": 0.05, "NDVI_p98": 0.91}

    state, open_, saved, gee = _stretching(monkeypatch, ["NDVI"], answer)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            assert _button(rc, "steps.layers.stretch").disabled is True  # no band yet
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "NDVI"
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "0.91")
            assert _field(rc, "steps.layers.field_min").v_model == "0.05"
            assert asked == [("STRETCH", "users/x/img", ("NDVI",), tuple(state.project.value.aoi.bounds))]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_stretch_with_no_spread_says_so_under_its_button(monkeypatch):
    async def answer(obj):
        return {"NDVI_p2": 3, "NDVI_p98": 3}

    state, open_, saved, gee = _stretching(monkeypatch, ["NDVI"], answer)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "NDVI"
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            shown = t("steps.layers.err_no_stretch")
            assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
            assert (_field(rc, "steps.layers.field_min").v_model, _field(rc, "steps.layers.field_max").v_model) == (
                "0", "1")
        finally:
            rc.close()

    asyncio.run(main())


def test_closing_during_a_stretch_cancels_it(monkeypatch, caplog):
    asked, done, release = [], [], asyncio.Event()

    async def answer(obj):
        asked.append(obj)
        await release.wait()
        done.append(obj)
        return {"NDVI_p2": 0.05, "NDVI_p98": 0.91}

    state, open_, saved, gee = _stretching(monkeypatch, ["NDVI"], answer)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "NDVI"
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            assert _field(rc, "steps.layers.field_min").disabled is True  # frozen while it runs
            assert all(b.disabled for b in _style_tiles(rc))
            open_.set(False)
            release.set()
            await asyncio.sleep(0.1)
            assert done == [] and saved == []  # cancelled at its await
        finally:
            rc.close()

    with caplog.at_level(logging.ERROR):
        asyncio.run(main())
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_the_legend_preview_follows_the_ramp_and_range(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI"])

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            assert _ticks(rc) == ["0", "0.5", "1"]
            before = _bar(rc)
            [card] = [b for b in rc.find(v.Btn).widgets if t("steps.layers.ramp_viridis") in _flat(b)]
            card.fire_event("click", None)
            assert _bar(rc) != before and "#440154" in _bar(rc)
            _field(rc, "steps.layers.field_max").v_model = "100"
            assert _ticks(rc) == ["0", "50", "100"]
            _field(rc, "steps.layers.field_max").v_model = "0"  # no range: no preview
            assert not rc.find(v.Html, class_="dv-legend-preview").widgets
        finally:
            rc.close()

    asyncio.run(main())


def test_an_edit_opens_on_its_saved_style(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"lc": GeeVisLayer(name="lc", asset="users/x/img", bands=["lc"], style="categorical",
                                      classes=[VisClass(value=1, color="aa0000")])}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["lc"], p)

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            [tile] = _style_tiles(rc, selected=True)
            assert t("steps.layers.style_categorical") in _flat(tile)
        finally:
            rc.close()

    asyncio.run(main())


def test_preview_layer():
    kw = dict(band="NDVI", bands=(None, None, None), ramp="viridis", invert=False, palette=(), colormap=None,
              classes=(), vmin="0", vmax="1")
    layer = vlm.preview_layer("gee", "continuous", **kw)
    assert layer.palette == vis_palettes.ramp_palette("viridis") and (layer.min, layer.max) == (0, 1)
    assert vlm.preview_layer("gee", "continuous", **{**kw, "vmax": "0"}) is None
    assert vlm.preview_layer("file", "continuous", **{**kw, "band": 2}).band == 2
    hidden = [VisClass(value=1, color="aa0000", visible=False)]
    assert vlm.preview_layer("gee", "categorical", **{**kw, "classes": hidden}) is None
    shown = [VisClass(value=1, color="aa0000")]
    assert vlm.preview_layer("gee", "categorical", **{**kw, "classes": shown, "vmax": ""}).classes == shown
