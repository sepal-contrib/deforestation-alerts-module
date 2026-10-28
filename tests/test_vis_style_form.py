"""The visualization form's order, bands helper, Raster type and Continuous
fields (gui/widget/vis_style_fields, gui/widget/vis_layer_modal): Name, Source
and the picker come first and nothing shows below the picker until its band
list is read; one band is drawn on its own, two are picked from, three or more
are asked about (RGB when red, green and blue are recognized); the first band is
preselected, a new layer is Continuous, the stretch fills Min and Max from the
AOI, and the legend preview follows the ramp and the range."""

import asyncio
import logging
from types import SimpleNamespace

import ipyvuetify as v
import reacton

from deforestation_alerts.project.models import GeeVisLayer, VisClass
from gui.i18n import t
from pysepal.i18n import set_locale
from gui.scripts import vis_palettes
from gui.widget import vis_layer_modal as vlm
from gui.widget.form_dialog import FIELDS
from gui.widget.text_style import MUTED
from tests._render_helpers import run_rendered
from tests.test_vis_layers import (
    S2_BANDS, _band_count, _bands_choice, _composite, _enabled_combos, _field, _flat, _gee_modal, _pick_all_bands,
    _ramp_select, _rgb, _save_btn, _selected_combo, _settle, _shown, _texts, _tif, _with_aoi,
)

ASSET = {"asset_id": "users/x/img", "type": "IMAGE", "column": "ALL", "value": None}


def _raster_type(rc):
    """The Raster type select, or None while it is not shown."""
    return _shown(rc, "steps.layers.field_raster_type")


def _button(rc, key):
    """The button reading ``key``, or None while it is not shown."""
    found = [b for b in rc.find(v.Btn).widgets if t(key) in _flat(b)]
    assert len(found) <= 1
    return found[0] if found else None


def _enabled(rc, key):
    return _button(rc, key) is not None and not _button(rc, key).disabled


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


async def _one_band_form(rc, asset=ASSET):
    """Pick ``asset`` and wait for its band list: the one-band fields show."""
    vlm.RasterInput.seen.asset.set(asset)
    assert await _settle(lambda: _raster_type(rc) is not None)


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


# ---------------------------------------------------------------- order and the bands helper
def test_the_form_reads_name_source_picker_in_order(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI"])

    async def main(rc):
        assert rc.find(v.TextField).widgets[0].label == t("steps.layers.field_name")
        assert rc.find(v.Select).widgets[0].label == t("steps.raster_input.field_source")
        [fields] = rc.find(v.Html, style_=FIELDS).widgets
        name = _field(rc, "steps.layers.field_name")
        source = _field(rc, "steps.raster_input.field_source", v.Select)
        [picker] = rc.find(v.Html, class_="dv-stub-raster-input").widgets
        order = fields.children
        assert order.index(name) < order.index(source) < order.index(picker)
        assert (source.hint, source.persistent_hint) == (t("steps.raster_input.source_hint"), True)
        # the name's hint stays while the name is filled from the asset, not once typed over
        assert (name.hint, name.persistent_hint) == (t("steps.layers.name_hint"), True)
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: _field(rc, "steps.layers.field_name").v_model == "img_NDVI")
        assert _field(rc, "steps.layers.field_name").persistent_hint is True
        _field(rc, "steps.layers.field_name").v_model = "mine"
        assert _field(rc, "steps.layers.field_name").persistent_hint is False

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_nothing_below_the_picker_before_the_bands_are_read(monkeypatch):
    state, open_, saved, _ = _gee_modal(monkeypatch, ["NDVI"])
    release = asyncio.Event()

    async def get_info_async(obj):
        await release.wait()
        return ["NDVI"]

    gee = SimpleNamespace(get_info_async=get_info_async)

    def nothing_below(rc):
        assert _shown(rc, "steps.layers.field_band") is None and _bands_choice(rc) is None
        assert _raster_type(rc) is None and _ramp_select(rc) is None
        assert not rc.find(v.Html, class_="dv-legend-preview").widgets
        assert _button(rc, "steps.layers.stretch") is None
        assert _shown(rc, "steps.layers.field_min", v.TextField) is None
        # Show in Analysis is the exception
        assert _shown(rc, "steps.layers.field_show_in_analysis", v.Checkbox) is not None

    async def main(rc):
        nothing_below(rc)  # no image picked
        loading = t("steps.raster_input.bands_loading")
        assert loading not in _texts(rc)  # the task is pending at mount, for no image
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: loading in _texts(rc))
        nothing_below(rc)  # the band list is loading
        release.set()
        assert await _settle(lambda: _raster_type(rc) is not None)
        assert loading not in _texts(rc)

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_save_is_disabled_while_the_bands_are_read(monkeypatch):
    state, open_, saved, _ = _gee_modal(monkeypatch, ["NDVI"])
    release = asyncio.Event()

    async def get_info_async(obj):
        await release.wait()
        return ["NDVI"]

    gee = SimpleNamespace(get_info_async=get_info_async)

    async def main(rc):
        _field(rc, "steps.layers.field_name").v_model = "ndvi"
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: t("steps.raster_input.bands_loading") in _texts(rc))
        assert _save_btn(rc).disabled is True
        release.set()
        assert await _settle(lambda: _raster_type(rc) is not None)
        assert _save_btn(rc).disabled is False

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_failed_band_list_shows_only_its_error(monkeypatch):
    state, open_, saved, _ = _gee_modal(monkeypatch, ["NDVI"])

    async def get_info_async(obj):
        raise RuntimeError("boom")

    gee = SimpleNamespace(get_info_async=get_info_async)

    async def main(rc):
        vlm.RasterInput.seen.asset.set(ASSET)
        shown = t("steps.raster_input.bands_failed", error="boom")
        assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
        assert _raster_type(rc) is None and _shown(rc, "steps.layers.field_band") is None
        assert _button(rc, "steps.layers.stretch") is None
        assert _save_btn(rc).disabled is True
        assert _shown(rc, "steps.layers.field_show_in_analysis", v.Checkbox) is not None

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_one_band_image_shows_no_bands_question_and_starts_continuous(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["NDVI"])

    async def main(rc):
        await _one_band_form(rc)
        assert t("steps.layers.bands_one_hint", name="NDVI") in _texts(rc)
        assert _bands_choice(rc) is None and _shown(rc, "steps.layers.field_band") is None
        assert _raster_type(rc).v_model == "continuous"
        _field(rc, "steps.layers.field_name").v_model = "ndvi"
        _save_btn(rc).fire_event("click", None)  # no band picked by hand: the only band is set
        assert await _settle(lambda: open_.value is False)
        [(layer, _prev)] = saved
        assert layer.style == "continuous" and layer.bands == ["NDVI"]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_the_first_band_is_preselected(monkeypatch, tmp_path):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["a", "b", "c"])

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _band_count(rc) == 3)
            assert _bands_choice(rc).v_model == "one"
            assert _field(rc, "steps.layers.field_band", v.Select).v_model == "a"
        finally:
            rc.close()
        tif = _tif(tmp_path / "two.tif", size=10, count=2)
        _box, rc = _render(state, open_, saved, gee)
        try:
            _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
            vlm.RasterInput.seen.path.set(str(tif))
            assert await _settle(lambda: _band_count(rc) == 2)
            assert _field(rc, "steps.layers.field_band", v.Select).v_model == 1  # a GeoTIFF band is its index
        finally:
            rc.close()

    asyncio.run(main())


def test_three_spectral_bands_default_to_rgb_and_other_bands_to_one(monkeypatch):
    for bands, mode in ((S2_BANDS, "all"), (["a", "b", "c"], "one")):
        state, open_, saved, gee = _gee_modal(monkeypatch, bands)

        async def main(rc):
            assert _bands_choice(rc) is None  # no band list yet
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: _bands_choice(rc) is not None)
            assert _bands_choice(rc).v_model == mode
            assert (_raster_type(rc) is not None) is (mode == "one")  # RGB has no Raster type

        run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_categorical_is_suggested_by_the_band_name(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["classification"])

    async def main(rc):
        await _one_band_form(rc)
        items = _raster_type(rc).items
        assert items[0]["text"] == t("steps.layers.style_continuous_item")
        assert items[1]["text"] == t("steps.layers.suggested", text=t("steps.layers.style_categorical_item"))
        assert _raster_type(rc).v_model == "continuous"  # suggested, never picked for the user
        assert _button(rc, "steps.layers.find_classes") is None
        _raster_type(rc).v_model = "categorical"
        assert _button(rc, "steps.layers.find_classes") is not None
        assert _ramp_select(rc) is None

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_picking_another_image_resets_band_mode_and_classes(monkeypatch):
    bands_of = {"users/x/lc": ["lc"], "users/x/s2": S2_BANDS}
    state, open_, saved, _ = _gee_modal(monkeypatch, [])

    async def get_info_async(ref):
        return bands_of[ref]

    gee = SimpleNamespace(get_info_async=get_info_async)

    async def main(rc):
        await _one_band_form(rc, {**ASSET, "asset_id": "users/x/lc"})
        _raster_type(rc).v_model = "categorical"
        _field(rc, "steps.layers.class_add").v_model = "1"
        _by_class(rc, "dv-class-add")[0].fire_event("click", None)
        assert _labels(rc) == ["1"]
        vlm.RasterInput.seen.asset.set({**ASSET, "asset_id": "users/x/s2"})
        assert await _settle(lambda: _bands_choice(rc) is not None and _bands_choice(rc).v_model == "all")
        _bands_choice(rc).v_model = "one"
        assert _field(rc, "steps.layers.field_band", v.Select).v_model == "B2"  # the new first band
        assert _labels(rc) == []

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_switching_source_clears_band_roles_and_combo(monkeypatch):
    asked = []

    async def fake_validate(gee_interface, project, **kw):
        asked.append(kw)
        return {"error": "steps.layers.err_incomplete"}

    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)
    monkeypatch.setattr(vlm, "validate_vis_layer", fake_validate)

    async def main(rc):
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
        _field(rc, "steps.layers.field_name").v_model = "x"
        _save_btn(rc).fire_event("click", None)
        assert await _settle(lambda: len(asked) == 1)
        [kw] = asked
        assert (kw["kind"], kw["band"], tuple(kw["bands"]), kw["roles"], kw["combo"]) == (
            "file", None, (None, None, None), {}, None)

    run_rendered(lambda: _render(state, open_, saved, gee), main)


# ---------------------------------------------------------------- the Continuous fields
def test_the_stretch_fills_min_and_max(monkeypatch):
    asked = []

    async def answer(obj):
        asked.append(obj)
        return {"NDVI_p2": 0.05, "NDVI_p98": 0.91}

    state, open_, saved, gee = _stretching(monkeypatch, ["NDVI"], answer)

    async def main(rc):
        await _one_band_form(rc)
        assert _enabled(rc, "steps.layers.stretch")  # the only band is set
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "0.91")
        assert _field(rc, "steps.layers.field_min").v_model == "0.05"
        assert asked == [("STRETCH", "users/x/img", ("NDVI",), tuple(state.project.value.aoi.bounds))]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_stretch_with_no_spread_says_so_under_its_button(monkeypatch):
    async def answer(obj):
        return {"NDVI_p2": 3, "NDVI_p98": 3}

    state, open_, saved, gee = _stretching(monkeypatch, ["NDVI"], answer)

    async def main(rc):
        await _one_band_form(rc)
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        shown = t("steps.layers.err_no_stretch")
        assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
        assert (_field(rc, "steps.layers.field_min").v_model, _field(rc, "steps.layers.field_max").v_model) == (
            "0", "1")

    run_rendered(lambda: _render(state, open_, saved, gee), main)


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
            await _one_band_form(rc)
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: len(asked) == 1)
            assert _field(rc, "steps.layers.field_min").disabled is True  # frozen while it runs
            assert _raster_type(rc).disabled is True and _ramp_select(rc).disabled is True
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

    async def main(rc):
        await _one_band_form(rc)
        assert _ticks(rc) == ["0", "0.5", "1"]
        before = _bar(rc)
        _ramp_select(rc).v_model = "viridis"
        assert _bar(rc) != before and "#440154" in _bar(rc)
        _field(rc, "steps.layers.field_max").v_model = "100"
        assert _ticks(rc) == ["0", "50", "100"]
        _field(rc, "steps.layers.field_max").v_model = "0"  # no range: no preview
        assert not rc.find(v.Html, class_="dv-legend-preview").widgets

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_an_edit_opens_on_its_saved_style(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"lc": GeeVisLayer(name="lc", asset="users/x/img", bands=["lc"], style="categorical",
                                      classes=[VisClass(value=1, color="aa0000")]),
                    "old": GeeVisLayer(name="old", asset="users/x/img", bands=["lc"], palette=["ff0000", "00ff00"])}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["lc"], p)

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert await _settle(lambda: _raster_type(rc) is not None)
            assert _raster_type(rc).v_model == "categorical"
        finally:
            rc.close()
        _box, rc = _render(state, open_, saved, gee, editing="old")
        try:
            assert await _settle(lambda: _ramp_select(rc) is not None)
            assert _raster_type(rc).v_model == "continuous"
            assert _ramp_select(rc).v_model is None  # a palette saved before the ramps selects no ramp
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
    rgb = vlm.preview_layer("file", "rgb", **{**kw, "bands": (3, 2, 1)})
    assert rgb.bands == [3, 2, 1] and rgb.style == "rgb"
    assert vlm.preview_layer("file", "rgb", **{**kw, "bands": (3, 2, None)}) is None  # not three bands yet
    assert vlm.preview_layer("gee", "rgb", **{**kw, "bands": ("B4", None, "B2")}) is None


# ---------------------------------------------------------------- the RGB fields
async def _rgb_form(rc, asset=ASSET):
    vlm.RasterInput.seen.asset.set(asset)
    await _pick_all_bands(rc)


def test_the_composite_select_disables_combinations_without_their_bands(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["SR_B2", "SR_B3", "SR_B4"])

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        assert _enabled_combos(rc) == ["true_color", "custom"]
        items = {i["value"]: i for i in _composite(rc).items}
        for key in ("false_color", "swir"):
            assert items[key]["disabled"] is True
        assert items["true_color"]["text"] == t("steps.layers.combo_item", name="True colour",
                                                bands="SR_B4 / SR_B3 / SR_B2")
        assert [i["value"] for i in _composite(rc).items][-1] == "custom"  # Custom is always last

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_rgb_starts_on_the_first_found_combination(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main(rc):
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: _composite(rc) is not None)
        assert _bands_choice(rc).v_model == "all"
        assert _composite(rc).v_model == "true_color"
        assert _rgb(rc) == ["B4", "B3", "B2"]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_rgb_without_recognized_names_uses_the_first_three_bands(monkeypatch, tmp_path):
    tif = _tif(tmp_path / "three.tif", size=10, count=3)
    state, open_, saved, gee = _gee_modal(monkeypatch, [])

    async def main(rc):
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
        vlm.RasterInput.seen.path.set(str(tif))
        assert await _settle(lambda: _bands_choice(rc) is not None)
        assert _bands_choice(rc).v_model == "one"  # no red, green and blue names: one band
        _bands_choice(rc).v_model = "all"
        assert await _settle(lambda: _composite(rc) is not None)
        assert _composite(rc).v_model == "custom" and _enabled_combos(rc) == ["custom"]
        assert _rgb(rc) == [1, 2, 3]
        assert _composite(rc).hint == t("steps.layers.roles_none_rgb", bands="Band 1, Band 2, Band 3")

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_hand_picked_band_selects_custom(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        selects = rc.find(v.Row).widgets
        assert any([c.children[0].label for c in row.children if c.children] == [
            t("steps.layers.field_red"), t("steps.layers.field_green"), t("steps.layers.field_blue")]
            for row in selects)  # the three band selects share one row
        _field(rc, "steps.layers.field_red", v.Select).v_model = "B8"
        assert _selected_combo(rc) == "custom" and _rgb(rc) == ["B8", "B3", "B2"]
        _composite(rc).v_model = "true_color"
        assert _selected_combo(rc) == "true_color" and _rgb(rc) == ["B4", "B3", "B2"]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_hand_picked_bands_matching_a_combination_select_it(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        _field(rc, "steps.layers.field_red", v.Select).v_model = "B8"
        _field(rc, "steps.layers.field_green", v.Select).v_model = "B4"
        _field(rc, "steps.layers.field_blue", v.Select).v_model = "B3"
        assert _rgb(rc) == ["B8", "B4", "B3"] and _selected_combo(rc) == "false_color"
        _field(rc, "steps.layers.field_blue", v.Select).v_model = "B2"
        assert _selected_combo(rc) == "custom"

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_an_rgb_edit_opens_on_its_saved_bands_and_combination(monkeypatch):
    p = _with_aoi()
    roles = {"red": "B4", "green": "B3", "blue": "B2", "nir": "B8", "swir": "B11"}
    p.vis_layers = {"fc": GeeVisLayer(name="fc", asset="users/x/img", bands=["B8", "B4", "B3"], min=0, max=3000,
                                      style="rgb", roles=roles, combo="false_color")}
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS, p)

    async def main(rc):
        assert await _settle(lambda: _composite(rc) is not None)
        await asyncio.sleep(0.05)  # the first band list keeps the edit's own bands
        assert _bands_choice(rc).v_model == "all"
        assert _composite(rc).v_model == "false_color"
        assert _rgb(rc) == ["B8", "B4", "B3"]
        _save_btn(rc).fire_event("click", None)
        assert await _settle(lambda: open_.value is False)
        [(layer, prev)] = saved
        assert (layer.style, layer.bands, layer.combo, layer.roles, prev) == (
            "rgb", ["B8", "B4", "B3"], "false_color", roles, "fc")

    run_rendered(lambda: _render(state, open_, saved, gee, editing="fc"), main)


def test_an_rgb_edit_on_a_two_band_raster_becomes_continuous(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B4", "B3", "B2"], min=0, max=3000,
                                       style="rgb")}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["B4", "B3"], p)

    async def main(rc):
        assert await _settle(lambda: _band_count(rc) == 2)
        assert _bands_choice(rc) is None and _composite(rc) is None
        assert _raster_type(rc).v_model == "continuous"
        _field(rc, "steps.layers.field_band", v.Select).v_model = "B3"
        _save_btn(rc).fire_event("click", None)
        assert await _settle(lambda: open_.value is False)
        [(layer, prev)] = saved
        assert (layer.style, layer.bands, prev) == ("continuous", ["B3"], "rgb")

    run_rendered(lambda: _render(state, open_, saved, gee, editing="rgb"), main)


def test_an_rgb_edit_on_a_two_band_raster_preselects_its_first_band(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B4", "B3", "B2"], min=0, max=3000,
                                       style="rgb")}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["B3", "B4"], p)

    async def main(rc):
        assert await _settle(lambda: _band_count(rc) == 2)
        assert await _settle(lambda: _field(rc, "steps.layers.field_band", v.Select).v_model == "B3")
        _save_btn(rc).fire_event("click", None)  # no band picked by hand
        assert await _settle(lambda: open_.value is False)
        [(layer, _prev)] = saved
        assert (layer.style, layer.bands) == ("continuous", ["B3"])

    run_rendered(lambda: _render(state, open_, saved, gee, editing="rgb"), main)


def test_the_rgb_stretch_spans_the_three_bands(monkeypatch):
    asked = []

    async def answer(obj):
        asked.append(obj)
        return {"B4_p2": 120, "B4_p98": 2400, "B3_p2": 90, "B3_p98": 2100, "B2_p2": 150, "B2_p98": 2900}

    state, open_, saved, gee = _stretching(monkeypatch, S2_BANDS, answer)

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "2900")
        assert _field(rc, "steps.layers.field_min").v_model == "90"
        assert asked == [("STRETCH", "users/x/img", ("B4", "B3", "B2"), tuple(state.project.value.aoi.bounds))]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_the_band_selects_carry_their_colour_dot_as_a_class(monkeypatch):
    """The dot is a CSS ``::before`` on the select's label, not a label slot: a
    slot's content is dropped once the select is picked from in the browser."""
    from gui.widget.vis_style_fields import CHANNEL_COLORS, CHANNEL_CSS

    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        for i, key in enumerate(("steps.layers.field_red", "steps.layers.field_green", "steps.layers.field_blue")):
            select = _field(rc, key, v.Select)
            assert f"dv-channel-{i}" in (select.class_ or "").split() and not select.v_slots
            assert f".dv-channel-{i} .v-label::before" in CHANNEL_CSS and CHANNEL_COLORS[i] in CHANNEL_CSS
        assert any(CHANNEL_CSS in t.template for t in rc.find(v.VuetifyTemplate).widgets)  # shipped with the form

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_the_composite_hint_names_a_band_the_list_lacks_as_stored():
    from gui.widget.vis_style_fields import CompositeSelect

    _box, rc = reacton.render(CompositeSelect(combo="custom", on_combo=lambda k: None, roles={"red": "B4", "nir": "B8"},
                                              band_text={"B8": "B8"}, fallback=[]), handle_error=False)
    try:
        assert _composite(rc).hint == t("steps.layers.roles_detected", list="B8 NIR · B4 red")
    finally:
        rc.close()


def test_the_stretch_error_clears_when_the_composite_changes(monkeypatch):
    async def answer(obj):
        return {f"{b}_{p}": 5 for b in obj[2] for p in ("p2", "p98")}  # no spread

    state, open_, saved, gee = _stretching(monkeypatch, S2_BANDS, answer)
    shown = t("steps.layers.err_no_stretch")

    async def main(rc):
        await _rgb_form(rc)
        assert await _settle(lambda: _selected_combo(rc) == "true_color")
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
        _composite(rc).v_model = "false_color"
        assert await _settle(lambda: not rc.find(v.Html, class_="error--text", children=[shown]).widgets)

    run_rendered(lambda: _render(state, open_, saved, gee), main)


# ---------------------------------------------------------------- the Categorical fields
WORLDCOVER = {"hist": {"10": 3, "80": 1},
              "meta": {"Map_class_values": [10, 20, 80], "Map_class_palette": ["006400", "ffbb22", "0064c8"],
                       "Map_class_names": ["Tree cover", "Shrubland", "Water"]}}


def _probing(monkeypatch, bands, classes_answer=None, stretch_answer=None, p=None):
    """An Earth Engine form whose find-classes and stretch requests are answered by the given coroutines."""
    state, open_, saved, _ = _gee_modal(monkeypatch, bands, p)
    monkeypatch.setattr(vlm.vis_probes, "classes_request",
                        lambda asset, band, bounds: ("CLASSES", asset, band, tuple(bounds)))
    monkeypatch.setattr(vlm.vis_probes, "stretch_request",
                        lambda asset, bs, bounds: ("STRETCH", asset, tuple(bs), tuple(bounds)))

    async def get_info_async(obj):
        if isinstance(obj, tuple) and obj[0] == "CLASSES":
            return await classes_answer(obj)
        if isinstance(obj, tuple) and obj[0] == "STRETCH":
            return await stretch_answer(obj)
        return bands

    return state, open_, saved, SimpleNamespace(get_info_async=get_info_async)


def _answer(info, asked=None):
    async def answer(obj):
        if asked is not None:
            asked.append(obj)
        return info

    return answer


def _by_class(rc, cls, widget=v.Btn):
    return [w for w in rc.find(widget).widgets if cls in (w.class_ or "").split()]


def _labels(rc):
    return [f.v_model for f in _by_class(rc, "dv-class-label", v.TextField)]


def test_class_labels_have_one_header_not_a_floating_label_per_row():
    # a floating label needs the field's top padding, which the compact rows
    # drop: it rose into the row above, over the previous field's underline
    from gui.widget.vis_style_fields import ClassList

    classes = [VisClass(value=0, color="e41a1c", label="0"), VisClass(value=1, color="377eb8", label="")]
    _box, rc = reacton.render(ClassList(classes, lambda _: None), handle_error=False)
    try:
        fields = _by_class(rc, "dv-class-label", v.TextField)
        assert [f.label for f in fields] == [None, None]
        assert [f.placeholder for f in fields] == [t("steps.layers.class_label")] * 2
        header = rc.find(v.Html, class_="dv-class-header").widgets
        assert len(header) == 1 and t("steps.layers.class_label") in _flat(header[0])
    finally:
        rc.close()


def _swatches(rc):
    return [b.style_ for b in _by_class(rc, "dv-class-swatch")]


def _hide_icons(rc):
    return [b.children[0].children[0] for b in _by_class(rc, "dv-class-hide")]


def _error_shown(rc, key):
    return bool(rc.find(v.Html, class_="error--text", children=[t(key)]).widgets)


async def _categorical(rc, band="Map"):
    """Pick Categorical once the band list is in, and ``band`` when the image has more than one."""
    await _one_band_form(rc)
    _raster_type(rc).v_model = "categorical"
    select = _shown(rc, "steps.layers.field_band")
    if select is not None:
        select.v_model = band


async def _find(rc):
    _button(rc, "steps.layers.find_classes").fire_event("click", None)


def test_find_classes_lists_worldcover_with_its_names(monkeypatch):
    asked = []
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER, asked))

    async def main(rc):
        await _categorical(rc)
        assert not rc.find(v.TextField, label=t("steps.layers.field_min")).widgets  # no range for classes
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["Tree cover", "Water"])
        assert ["#006400" in s for s in _swatches(rc)] == [True, False]
        assert "#0064c8" in _swatches(rc)[1]
        assert asked == [("CLASSES", "users/x/img", "Map", tuple(state.project.value.aoi.bounds))]
        chips = _flat(_preview(rc))
        assert "Tree cover" in chips and "Water" in chips

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def _note_shown(rc):
    return t("steps.layers.zero_one_note") in _texts(rc)


def test_a_zero_one_find_labels_1_with_the_band_and_shows_the_note(monkeypatch, tmp_path):
    zero_one = {"hist": {"0": 40, "1": 3}, "meta": {}}
    state, open_, saved, gee = _probing(monkeypatch, ["loss", "gain"], _answer(zero_one))

    async def main(rc):
        await _categorical(rc, band="loss")
        assert not _note_shown(rc)
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["0", "loss"])
        assert _hide_icons(rc) == ["mdi-eye-off", "mdi-eye"]  # 0 starts hidden, 1 shown
        assert _note_shown(rc)
        note = rc.find(v.Html, children=[t("steps.layers.zero_one_note")]).widgets
        assert len(note) == 1 and note[0].style_ == MUTED  # one muted line
        _field(rc, "steps.layers.field_band", v.Select).v_model = "gain"  # another band: the note goes
        assert await _settle(lambda: not _note_shown(rc))

    run_rendered(lambda: _render(state, open_, saved, gee), main)

    tif = _tif(tmp_path / "loss.tif", size=10)
    monkeypatch.setattr(vlm.vis_probes, "file_classes", lambda path, band, bounds: {"values": [0, 1], "known": None})

    async def geotiff():
        _box, rc = _render(state, open_, saved, gee)
        try:
            _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
            vlm.RasterInput.seen.path.set(str(tif))
            assert await _settle(lambda: _raster_type(rc) is not None)
            _raster_type(rc).v_model = "categorical"
            await _find(rc)
            assert await _settle(lambda: _labels(rc) == ["0", "Band 1"])  # the band's text, never its index
            assert _note_shown(rc)
        finally:
            rc.close()

    asyncio.run(geotiff())


def test_classes_the_image_declares_get_no_zero_one_note(monkeypatch):
    declared = {"hist": {"0": 4, "1": 3}, "meta": {"loss_class_values": [0, 1], "loss_class_palette": ["000000", "ff0000"],
                                                   "loss_class_names": ["Stable", "Lost"]}}
    state, open_, saved, gee = _probing(monkeypatch, ["loss"], _answer(declared))

    async def main(rc):
        await _categorical(rc, band="loss")
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["Stable", "Lost"])
        assert not _note_shown(rc)

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_hidden_class_and_a_renamed_label_are_saved(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        _by_class(rc, "dv-class-hide")[1].fire_event("click", None)
        assert _hide_icons(rc) == ["mdi-eye", "mdi-eye-off"]
        _by_class(rc, "dv-class-label", v.TextField)[0].v_model = "Forest"
        _field(rc, "steps.layers.field_name").v_model = "wc"
        _button(rc, "steps.layers.add").fire_event("click", None)
        assert await _settle(lambda: open_.value is False)
        [(layer, _prev)] = saved
        assert layer.style == "categorical" and layer.bands == ["Map"]
        assert layer.classes == [VisClass(value=10, color="006400", label="Forest"),
                                 VisClass(value=80, color="0064c8", label="Water", visible=False)]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_the_palette_menu_recolors_every_class(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        items = _by_class(rc, "dv-palette")
        assert [_flat(b).strip() for b in items] == [t(f"steps.layers.palette_{k}")
                                                     for k in vis_palettes.QUALITATIVE]
        [paired] = [b for b in items if t("steps.layers.palette_paired") in _flat(b)]
        paired.fire_event("click", None)
        want = vis_palettes.qualitative_colors("paired", 2)
        assert [f"#{c}" in s for c, s in zip(want, _swatches(rc))] == [True, True]
        assert _labels(rc) == ["Tree cover", "Water"]  # labels stay
        # the swatch's colour picker writes back bare hex, alpha dropped
        picker = rc.find(v.ColorPicker).widgets[0]
        picker.v_model = "#AA00BBFF"
        assert "#aa00bb" in _swatches(rc)[0]
        assert rc.find(v.ColorPicker).widgets[0].v_model == "#aa00bb"

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_add_a_value_refuses_a_duplicate_and_appends_a_new_one(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        field = _field(rc, "steps.layers.class_add")
        [add] = _by_class(rc, "dv-class-add")
        for text in ("80", "2.5", ""):
            field.v_model = text
            add.fire_event("click", None)
            assert _field(rc, "steps.layers.class_add").error_messages == [t("steps.layers.err_class_value")]
            assert len(_labels(rc)) == 2
        _field(rc, "steps.layers.class_add").v_model = "5"
        add.fire_event("click", None)
        assert _labels(rc) == ["Tree cover", "Water", "5"]
        assert f"#{vis_palettes.qualitative_colors('set1', 3)[2]}" in _swatches(rc)[2]
        assert _field(rc, "steps.layers.class_add").error_messages == []
        assert _field(rc, "steps.layers.class_add").v_model == ""

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_changing_the_band_clears_the_found_classes(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map", "other"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        _field(rc, "steps.layers.class_add").v_model = "80"
        _by_class(rc, "dv-class-add")[0].fire_event("click", None)
        assert _field(rc, "steps.layers.class_add").error_messages
        _field(rc, "steps.layers.field_band", v.Select).v_model = "other"
        assert _labels(rc) == []
        # "Add a value" starts over with the list
        assert _field(rc, "steps.layers.class_add").v_model == ""
        assert _field(rc, "steps.layers.class_add").error_messages == []
        # the old band's classes are never saved on the new band
        _field(rc, "steps.layers.field_name").v_model = "wc"
        _button(rc, "steps.layers.add").fire_event("click", None)
        assert await _settle(lambda: rc.find(v.Html, children=[t("steps.layers.err_no_classes")]).widgets)
        assert saved == []
        # and a new raster clears them too
        _field(rc, "steps.layers.field_band", v.Select).v_model = "Map"
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        vlm.RasterInput.seen.asset.set({**ASSET, "asset_id": "users/x/other"})
        assert await _settle(lambda: _band_count(rc) == 2 and _labels(rc) == [])
        # and so does a new kind; "Add a value" starts empty again too
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: _band_count(rc) == 2)
        _field(rc, "steps.layers.field_band", v.Select).v_model = "Map"
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        _field(rc, "steps.layers.class_add").v_model = "80"
        _by_class(rc, "dv-class-add")[0].fire_event("click", None)
        assert _field(rc, "steps.layers.class_add").error_messages
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
        assert _shown(rc, "steps.layers.class_add", v.TextField) is None  # no file: nothing below the picker
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "gee"  # the same image, read again
        assert await _settle(lambda: _band_count(rc) == 2)
        assert _labels(rc) == []
        assert _field(rc, "steps.layers.class_add").v_model == ""
        assert _field(rc, "steps.layers.class_add").error_messages == []

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_changing_the_band_clears_the_probe_errors(monkeypatch):
    fractions = {"hist": {"0.5": 1, "1": 2}, "meta": {}}
    no_spread = {"a_p2": 3, "a_p98": 3}
    state, open_, saved, gee = _probing(monkeypatch, ["a", "b"], _answer(fractions), _answer(no_spread))

    async def main(rc):
        vlm.RasterInput.seen.asset.set(ASSET)
        assert await _settle(lambda: _band_count(rc) == 2)
        _field(rc, "steps.layers.field_band", v.Select).v_model = "a"
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: _error_shown(rc, "steps.layers.err_no_stretch"))
        _field(rc, "steps.layers.field_band", v.Select).v_model = "b"
        assert not _error_shown(rc, "steps.layers.err_no_stretch")
        await _categorical(rc, band="a")
        await _find(rc)
        assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
        _field(rc, "steps.layers.field_band", v.Select).v_model = "b"
        assert not _error_shown(rc, "steps.layers.err_not_integer")
        # a new kind clears them too: they are gone once the same image is read again
        _field(rc, "steps.layers.field_band", v.Select).v_model = "a"
        await _find(rc)
        assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "gee"
        assert await _settle(lambda: _band_count(rc) == 2)
        assert not _error_shown(rc, "steps.layers.err_not_integer")
        _raster_type(rc).v_model = "continuous"
        _field(rc, "steps.layers.field_band", v.Select).v_model = "a"
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: _error_shown(rc, "steps.layers.err_no_stretch"))
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "file"
        _field(rc, "steps.raster_input.field_source", v.Select).v_model = "gee"
        assert await _settle(lambda: _band_count(rc) == 2)
        assert not _error_shown(rc, "steps.layers.err_no_stretch")

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def _categorical_edit():
    p = _with_aoi()
    p.vis_layers = {"lc": GeeVisLayer(name="lc", asset="users/x/img", bands=["Map"], style="categorical",
                                      classes=[VisClass(value=1, color="aa0000", label="Forest"),
                                               VisClass(value=2, color="00aa00", label="Crops", visible=False)])}
    return p


def test_an_edit_opens_with_its_classes(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], p=_categorical_edit())

    async def main(rc):
        assert await _settle(lambda: _labels(rc) == ["Forest", "Crops"])
        await asyncio.sleep(0.05)
        assert _labels(rc) == ["Forest", "Crops"]  # the first band load keeps them
        assert _hide_icons(rc) == ["mdi-eye", "mdi-eye-off"]
        assert ["#aa0000" in s for s in _swatches(rc)] == [True, False]

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def test_a_band_with_fractions_says_so_inline(monkeypatch):
    fractions = {"hist": {"0.5": 1, "1": 2}, "meta": {}}
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(fractions), p=_categorical_edit())

    async def main(rc):
        assert await _settle(lambda: _enabled(rc, "steps.layers.find_classes"))
        await _find(rc)
        assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
        assert _labels(rc) == ["Forest", "Crops"]  # the list is left unchanged

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def test_closing_during_find_classes_cancels_it(monkeypatch, caplog):
    asked, done, release = [], [], asyncio.Event()

    async def answer(obj):
        asked.append(obj)
        await release.wait()
        done.append(obj)
        return WORLDCOVER

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], answer)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _categorical(rc)
            await _find(rc)
            assert await _settle(lambda: len(asked) == 1)
            assert _raster_type(rc).disabled is True  # frozen while it runs
            open_.set(False)
            release.set()
            await asyncio.sleep(0.1)
            assert done == [] and saved == []
        finally:
            rc.close()

    with caplog.at_level(logging.ERROR):
        asyncio.run(main())
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


def test_a_new_find_keeps_the_edits_and_adds_the_new_values(monkeypatch):
    answers = [{"hist": {"10": 3, "80": 1}, "meta": {}}, {"hist": {"10": 1, "20": 1, "80": 1}, "meta": {}}]

    async def answer(obj):
        return answers.pop(0)

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], answer)

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["10", "80"])
        rc.find(v.ColorPicker).widgets[0].v_model = "#123456"
        _by_class(rc, "dv-class-label", v.TextField)[1].v_model = "Water"
        _by_class(rc, "dv-class-hide")[1].fire_event("click", None)
        _field(rc, "steps.layers.class_add").v_model = "99"
        _by_class(rc, "dv-class-add")[0].fire_event("click", None)
        assert _labels(rc) == ["10", "Water", "99"]
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["10", "20", "Water", "99"])
        assert "#123456" in _swatches(rc)[0]  # a hand-picked colour stays
        assert _hide_icons(rc) == ["mdi-eye", "mdi-eye", "mdi-eye-off", "mdi-eye"]

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_enter_in_add_a_value_adds_it_and_does_not_save(monkeypatch):
    from gui.widget.enter_key import ENTER_OWN

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        field = _field(rc, "steps.layers.class_add")
        assert ENTER_OWN in field.class_.split()  # the dialog's Enter leaves it alone (enter_key)
        field.v_model = "7"
        field.fire_event("keydown.enter", None)
        assert _labels(rc) == ["Tree cover", "Water", "7"]
        await asyncio.sleep(0.05)
        assert open_.value is True and saved == []

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_probe_error_follows_a_language_switch(monkeypatch):
    fractions = {"hist": {"0.5": 1}, "meta": {}}
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(fractions))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _categorical(rc)
            await _find(rc)
            assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
            set_locale("fr")
            assert t("steps.layers.err_not_integer").startswith("Cette bande")
            assert _error_shown(rc, "steps.layers.err_not_integer")
        finally:
            set_locale("en")
            rc.close()

    asyncio.run(main())


def test_the_palette_menu_selects_the_palette_the_colours_follow(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main(rc):
        await _categorical(rc)
        await _find(rc)
        assert await _settle(lambda: len(_labels(rc)) == 2)
        assert _by_class(rc, "dv-palette--selected") == []  # WorldCover's own colours follow none
        [paired] = [b for b in _by_class(rc, "dv-palette") if t("steps.layers.palette_paired") in _flat(b)]
        paired.fire_event("click", None)
        [selected] = _by_class(rc, "dv-palette--selected")
        assert t("steps.layers.palette_paired") in _flat(selected)
        rc.find(v.ColorPicker).widgets[0].v_model = "#123456"  # a hand-picked colour: none again
        assert _by_class(rc, "dv-palette--selected") == []

    run_rendered(lambda: _render(state, open_, saved, gee), main)


def test_a_geotiff_categorical_edit_keeps_its_classes(monkeypatch, tmp_path):
    from deforestation_alerts.project.models import FileVisLayer
    from tests.test_vis_layers import _tif

    tif = _tif(tmp_path / "lc.tif", size=10, count=2)
    p = _with_aoi()
    p.vis_layers = {"lc": FileVisLayer(name="lc", path=str(tif), band=2, style="categorical",
                                       classes=[VisClass(value=1, color="aa0000", label="Forest")])}
    state, open_, saved, gee = _gee_modal(monkeypatch, [], p)

    async def main(rc):
        assert await _settle(lambda: _band_count(rc) == 2)
        assert _labels(rc) == ["Forest"]
        await asyncio.sleep(0.05)
        assert _field(rc, "steps.layers.field_band", v.Select).v_model == 2
        assert _labels(rc) == ["Forest"]

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def _file_edit(monkeypatch, tmp_path, style, classes=()):
    """A form editing a two-band GeoTIFF layer on band 2, whose file probes are recorded."""
    from deforestation_alerts.project.models import FileVisLayer
    from tests.test_vis_layers import _tif

    tif = _tif(tmp_path / "lc.tif", size=10, count=2)
    p = _with_aoi()
    p.vis_layers = {"lc": FileVisLayer(name="lc", path=str(tif), band=2, style=style, classes=list(classes))}
    asked = []

    def file_stretch(path, bands, bounds):
        asked.append(("STRETCH", path, bands, tuple(bounds)))
        return (3.0, 42.0)

    def file_classes(path, band, bounds):
        asked.append(("CLASSES", path, band, tuple(bounds)))
        return {"values": [1, 7], "known": None}

    monkeypatch.setattr(vlm.vis_probes, "file_stretch", file_stretch)
    monkeypatch.setattr(vlm.vis_probes, "file_classes", file_classes)
    state, open_, saved, gee = _gee_modal(monkeypatch, [], p)
    return state, open_, saved, gee, str(tif), asked


def test_stretch_runs_on_the_geotiff_band(monkeypatch, tmp_path):
    state, open_, saved, gee, tif, asked = _file_edit(monkeypatch, tmp_path, "continuous")

    async def main(rc):
        assert await _settle(lambda: _enabled(rc, "steps.layers.stretch"))
        _button(rc, "steps.layers.stretch").fire_event("click", None)
        assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "42")
        assert _field(rc, "steps.layers.field_min").v_model == "3"
        assert asked == [("STRETCH", tif, [2], tuple(state.project.value.aoi.bounds))]

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def test_find_classes_runs_on_the_geotiff_band(monkeypatch, tmp_path):
    state, open_, saved, gee, tif, asked = _file_edit(
        monkeypatch, tmp_path, "categorical", [VisClass(value=1, color="aa0000", label="Forest")])

    async def main(rc):
        assert await _settle(lambda: _enabled(rc, "steps.layers.find_classes"))
        await _find(rc)
        assert await _settle(lambda: _labels(rc) == ["Forest", "7"])
        assert asked == [("CLASSES", tif, 2, tuple(state.project.value.aoi.bounds))]

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def test_the_probe_buttons_are_disabled_without_an_aoi(monkeypatch):
    from tests.test_vis_layers import _p

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER), _answer({}), p=_p())

    async def main(rc):
        await _one_band_form(rc)
        await asyncio.sleep(0.05)
        assert _button(rc, "steps.layers.stretch").disabled is True
        await _categorical(rc)
        await asyncio.sleep(0.05)
        assert _button(rc, "steps.layers.find_classes").disabled is True

    run_rendered(lambda: _render(state, open_, saved, gee), main)


# ---------------------------------------------------------------- final-review fixes
def test_an_rgb_edit_switched_to_one_band_starts_on_the_first_band(monkeypatch):
    p = _with_aoi()
    roles = {"red": "B4", "green": "B3", "blue": "B2", "nir": "B8", "swir": "B11"}
    p.vis_layers = {"fc": GeeVisLayer(name="fc", asset="users/x/img", bands=["B8", "B4", "B3"], min=0, max=3000,
                                      style="rgb", roles=roles, combo="false_color")}
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS, p)

    async def main(rc):
        assert await _settle(lambda: _composite(rc) is not None)
        _bands_choice(rc).v_model = "one"
        assert await _settle(lambda: _shown(rc, "steps.layers.field_band") is not None)
        assert _field(rc, "steps.layers.field_band", v.Select).v_model == S2_BANDS[0]

    run_rendered(lambda: _render(state, open_, saved, gee, editing="fc"), main)


def test_a_categorical_edit_switched_to_continuous_starts_on_the_default_ramp(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], p=_categorical_edit())

    async def main(rc):
        assert await _settle(lambda: _raster_type(rc) is not None)
        _raster_type(rc).v_model = "continuous"
        assert await _settle(lambda: _ramp_select(rc) is not None)
        assert _ramp_select(rc).v_model == vis_palettes.DEFAULT_RAMP
        _save_btn(rc).fire_event("click", None)
        assert await _settle(lambda: open_.value is False)
        [(layer, _prev)] = saved
        assert layer.style == "continuous"
        assert layer.palette == vis_palettes.ramp_palette(vis_palettes.DEFAULT_RAMP)

    run_rendered(lambda: _render(state, open_, saved, gee, editing="lc"), main)


def test_the_edits_saved_mode_is_not_kept_for_another_image(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"nd": GeeVisLayer(name="nd", asset="users/x/img", bands=["NDVI"], min=0, max=1)}
    state, open_, saved, _ = _gee_modal(monkeypatch, [], p)
    held = asyncio.Event()

    async def get_info_async(ref):
        if ref == "users/x/img":
            await held.wait()  # the edit's own band list stays pending
            return ["NDVI"]
        return S2_BANDS

    gee = SimpleNamespace(get_info_async=get_info_async)

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="nd")
        try:
            await asyncio.sleep(0.05)
            vlm.RasterInput.seen.asset.set({**ASSET, "asset_id": "users/x/s2"})
            assert await _settle(lambda: _bands_choice(rc) is not None)
            assert _bands_choice(rc).v_model == "all"  # S2's default, not the edit's one band
            assert _shown(rc, "steps.layers.field_band") is None
        finally:
            held.set()
            rc.close()

    asyncio.run(main())
