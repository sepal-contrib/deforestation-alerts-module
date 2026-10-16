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
from pysepal.i18n import set_locale
from gui.scripts import vis_palettes
from gui.widget import vis_layer_modal as vlm
from tests.test_vis_layers import (
    S2_BANDS, _bands_choice, _card_has, _combo_card, _combo_cards, _enabled_combos, _field, _flat, _gee_modal,
    _pick_all_bands, _rgb, _role_selects, _roles_link, _selected_combo, _settle, _with_aoi,
)

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
    rgb = vlm.preview_layer("file", "rgb", **{**kw, "bands": (3, 2, 1)})
    assert rgb.bands == [3, 2, 1] and rgb.style == "rgb"
    assert vlm.preview_layer("file", "rgb", **{**kw, "bands": (3, 2, None)}) is None  # not three bands yet
    assert vlm.preview_layer("gee", "rgb", **{**kw, "bands": ("B4", None, "B2")}) is None


# ---------------------------------------------------------------- the RGB fields
def _roles_text(rc):
    [line] = rc.find(v.Html, class_="dv-roles").widgets
    return _flat(line)


async def _rgb_form(rc, asset=ASSET):
    vlm.RasterInput.seen.asset.set(asset)
    await _pick_all_bands(rc)


def test_composite_cards_grey_out_without_their_bands(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["B4", "B3", "B2"])

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            assert _enabled_combos(rc) == ["true_color", "custom"]
            for key in ("false_color", "swir"):
                card = _combo_card(rc, key)
                assert _card_has(card, "dv-combo--off") and card.disabled is True and "opacity:.4" in card.style_
            true_color = _combo_card(rc, "true_color")
            assert true_color.disabled is False and "B4 B3 B2" in _flat(true_color)
            assert _combo_card(rc, "custom").disabled is False
            assert _combo_cards(rc)[-1] is _combo_card(rc, "custom")  # Custom is always last
        finally:
            rc.close()

    asyncio.run(main())


def test_the_roles_fold_behind_the_detected_line_and_edit_opens_them(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            assert _role_selects(rc) == []  # folded
            listed = "B2 blue · B3 green · B4 red · B8 NIR · B11 SWIR"
            assert t("steps.layers.roles_detected", list=listed) in _roles_text(rc)
            assert t("steps.layers.roles_edit") in _flat(_roles_link(rc))
            _roles_link(rc).fire_event("click", None)
            assert len(_role_selects(rc)) == 1
            _field(rc, "steps.layers.role_nir", v.Select).v_model = "B4"  # a role change updates the cards
            assert "B4 B4 B3" in _flat(_combo_card(rc, "false_color"))
            _field(rc, "steps.layers.role_swir", v.Select).v_model = None
            assert _card_has(_combo_card(rc, "swir"), "dv-combo--off")
            _roles_link(rc).fire_event("click", None)  # the link folds them again
            assert _role_selects(rc) == []
        finally:
            rc.close()

    asyncio.run(main())


def test_a_hand_picked_band_selects_custom(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            selects = rc.find(v.Row).widgets
            assert any([c.children[0].label for c in row.children if c.children] == [
                t("steps.layers.field_red"), t("steps.layers.field_green"), t("steps.layers.field_blue")]
                for row in selects)  # the three band selects share one row
            _field(rc, "steps.layers.field_red", v.Select).v_model = "B8"
            assert _selected_combo(rc) == "custom" and _rgb(rc) == ["B8", "B3", "B2"]
            _combo_card(rc, "true_color").fire_event("click", None)
            assert _selected_combo(rc) == "true_color" and _rgb(rc) == ["B4", "B3", "B2"]
        finally:
            rc.close()

    asyncio.run(main())


def test_no_recognized_band_preselects_custom(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, ["x", "y", "z"])

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "custom")
            assert _enabled_combos(rc) == ["custom"] and _role_selects(rc) == []
            assert t("steps.layers.roles_none") in _roles_text(rc)
            assert t("steps.layers.roles_set") in _flat(_roles_link(rc))
            _roles_link(rc).fire_event("click", None)
            _field(rc, "steps.layers.role_red", v.Select).v_model = "x"
            assert "x" in _roles_text(rc)
        finally:
            rc.close()

    asyncio.run(main())


def test_an_rgb_edit_on_a_two_band_raster_becomes_continuous(monkeypatch):
    p = _with_aoi()
    p.vis_layers = {"rgb": GeeVisLayer(name="rgb", asset="users/x/img", bands=["B4", "B3", "B2"], min=0, max=3000,
                                       style="rgb")}
    state, open_, saved, gee = _gee_modal(monkeypatch, ["B4", "B3"], p)

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="rgb")
        try:
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
            assert _bands_choice(rc) is None and not rc.find(v.Btn, class_="dv-combo").widgets
            [tile] = _style_tiles(rc, selected=True)
            assert t("steps.layers.style_continuous") in _flat(tile)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "B4"
            [save] = [b for b in rc.find(v.Btn).widgets if t("common.save") in b.children]
            save.fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            [(layer, prev)] = saved
            assert (layer.style, layer.bands, prev) == ("continuous", ["B4"], "rgb")
        finally:
            rc.close()

    asyncio.run(main())


def test_the_rgb_stretch_spans_the_three_bands(monkeypatch):
    asked = []

    async def answer(obj):
        asked.append(obj)
        return {"B4_p2": 120, "B4_p98": 2400, "B3_p2": 90, "B3_p98": 2100, "B2_p2": 150, "B2_p98": 2900}

    state, open_, saved, gee = _stretching(monkeypatch, S2_BANDS, answer)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "2900")
            assert _field(rc, "steps.layers.field_min").v_model == "90"
            assert asked == [("STRETCH", "users/x/img", ("B4", "B3", "B2"), tuple(state.project.value.aoi.bounds))]
        finally:
            rc.close()

    asyncio.run(main())


def test_the_custom_card_shows_its_hand_picked_bands(monkeypatch):
    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            _field(rc, "steps.layers.field_red", v.Select).v_model = "B8"
            assert "B8 B3 B2" in _flat(_combo_card(rc, "custom"))
            _field(rc, "steps.layers.field_green", v.Select).v_model = None
            assert "B8 ? B2" in _flat(_combo_card(rc, "custom"))
        finally:
            rc.close()

    asyncio.run(main())


def test_the_band_selects_carry_their_colour_dot_as_a_class(monkeypatch):
    """The dot is a CSS ``::before`` on the select's label, not a label slot: a
    slot's content is dropped once the select is picked from in the browser."""
    from gui.widget.vis_style_fields import CHANNEL_COLORS, CHANNEL_CSS

    state, open_, saved, gee = _gee_modal(monkeypatch, S2_BANDS)

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            for i, key in enumerate(("steps.layers.field_red", "steps.layers.field_green", "steps.layers.field_blue")):
                select = _field(rc, key, v.Select)
                assert f"dv-channel-{i}" in (select.class_ or "").split() and not select.v_slots
                assert f".dv-channel-{i} .v-label::before" in CHANNEL_CSS and CHANNEL_COLORS[i] in CHANNEL_CSS
            assert any(CHANNEL_CSS in t.template for t in rc.find(v.VuetifyTemplate).widgets)  # shipped with the form
            # a long combination name wraps inside its card instead of running into the next one
            assert "white-space:normal" in _combo_card(rc, "false_color").children[0].children[0].style_
        finally:
            rc.close()

    asyncio.run(main())


def test_the_custom_card_names_its_bands_like_the_others():
    from gui.widget.vis_style_fields import CompositeCards

    _box, rc = reacton.render(CompositeCards(combo="custom", on_combo=lambda k: None, roles={},
                                             band_text={1: "Red", 3: "Blue"}, custom=(1, None, 3)),
                              handle_error=False)
    try:
        assert "Red ? Blue" in _flat(_combo_card(rc, "custom"))
    finally:
        rc.close()


def _summary(roles, band_text, loading):
    from gui.widget.vis_style_fields import RolesSummary

    return reacton.render(RolesSummary(roles=roles, band_text=band_text, expanded=False,
                                       on_expanded=lambda x: None, on_role=lambda r, b: None, loading=loading),
                          handle_error=False)


def test_the_roles_line_names_the_roles_while_the_bands_load():
    _box, rc = _summary({"red": "B4", "green": "B3", "blue": "B2"}, {}, loading=True)
    try:
        text = _roles_text(rc)
        assert t("steps.layers.roles_detected", list="B4 red · B3 green · B2 blue") in text
        assert t("steps.layers.roles_none") not in text
    finally:
        rc.close()
    _box, rc = _summary({}, {}, loading=True)  # nothing yet: never "No bands recognized" while loading
    try:
        assert t("steps.layers.roles_none") not in _roles_text(rc)
    finally:
        rc.close()
    _box, rc = _summary({"red": "B4", "nir": "B8"}, {"B8": "B8"}, loading=False)  # a band the list lacks
    try:
        assert t("steps.layers.roles_detected", list="B8 NIR · B4 red") in _roles_text(rc)
    finally:
        rc.close()


def test_the_stretch_error_clears_when_the_composite_changes(monkeypatch):
    async def answer(obj):
        return {f"{b}_{p}": 5 for b in obj[2] for p in ("p2", "p98")}  # no spread

    state, open_, saved, gee = _stretching(monkeypatch, S2_BANDS, answer)
    shown = t("steps.layers.err_no_stretch")

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _rgb_form(rc)
            assert await _settle(lambda: _selected_combo(rc) == "true_color")
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: bool(rc.find(v.Html, class_="error--text", children=[shown]).widgets))
            _combo_card(rc, "false_color").fire_event("click", None)
            assert await _settle(lambda: not rc.find(v.Html, class_="error--text", children=[shown]).widgets)
        finally:
            rc.close()

    asyncio.run(main())


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
    return [f.v_model for f in rc.find(v.TextField, label=t("steps.layers.class_label")).widgets]


def _swatches(rc):
    return [b.style_ for b in _by_class(rc, "dv-class-swatch")]


def _hide_icons(rc):
    return [b.children[0].children[0] for b in _by_class(rc, "dv-class-hide")]


def _error_shown(rc, key):
    return bool(rc.find(v.Html, class_="error--text", children=[t(key)]).widgets)


async def _categorical(rc, band="Map"):
    """Pick the Categorical tile and ``band`` once the band list is in."""
    vlm.RasterInput.seen.asset.set(ASSET)
    assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) >= 1)
    [tile] = [b for b in _style_tiles(rc) if t("steps.layers.style_categorical") in _flat(b)]
    tile.fire_event("click", None)
    _field(rc, "steps.layers.field_band", v.Select).v_model = band


async def _find(rc):
    _button(rc, "steps.layers.find_classes").fire_event("click", None)


def test_find_classes_lists_worldcover_with_its_names(monkeypatch):
    asked = []
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER, asked))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _categorical(rc)
            assert not rc.find(v.TextField, label=t("steps.layers.field_min")).widgets  # no range for classes
            await _find(rc)
            assert await _settle(lambda: _labels(rc) == ["Tree cover", "Water"])
            assert ["#006400" in s for s in _swatches(rc)] == [True, False]
            assert "#0064c8" in _swatches(rc)[1]
            assert asked == [("CLASSES", "users/x/img", "Map", tuple(state.project.value.aoi.bounds))]
            chips = _flat(_preview(rc))
            assert "Tree cover" in chips and "Water" in chips
        finally:
            rc.close()

    asyncio.run(main())


def test_a_hidden_class_and_a_renamed_label_are_saved(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _categorical(rc)
            await _find(rc)
            assert await _settle(lambda: len(_labels(rc)) == 2)
            _by_class(rc, "dv-class-hide")[1].fire_event("click", None)
            assert _hide_icons(rc) == ["mdi-eye", "mdi-eye-off"]
            rc.find(v.TextField, label=t("steps.layers.class_label")).widgets[0].v_model = "Forest"
            _field(rc, "steps.layers.field_name").v_model = "wc"
            _button(rc, "steps.layers.add").fire_event("click", None)
            assert await _settle(lambda: open_.value is False)
            [(layer, _prev)] = saved
            assert layer.style == "categorical" and layer.bands == ["Map"]
            assert layer.classes == [VisClass(value=10, color="006400", label="Forest"),
                                     VisClass(value=80, color="0064c8", label="Water", visible=False)]
        finally:
            rc.close()

    asyncio.run(main())


def test_the_palette_menu_recolors_every_class(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
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
        finally:
            rc.close()

    asyncio.run(main())


def test_add_a_value_refuses_a_duplicate_and_appends_a_new_one(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
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
        finally:
            rc.close()

    asyncio.run(main())


def test_changing_the_band_clears_the_found_classes(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map", "other"], _answer(WORLDCOVER))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
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
            assert await _settle(lambda: _labels(rc) == [])
            # and so does a new kind; "Add a value" starts empty again too
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "Map"
            await _find(rc)
            assert await _settle(lambda: len(_labels(rc)) == 2)
            _field(rc, "steps.layers.class_add").v_model = "80"
            _by_class(rc, "dv-class-add")[0].fire_event("click", None)
            assert _field(rc, "steps.layers.class_add").error_messages
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "file"
            assert _labels(rc) == []
            assert _field(rc, "steps.layers.class_add").v_model == ""
            assert _field(rc, "steps.layers.class_add").error_messages == []
        finally:
            rc.close()

    asyncio.run(main())


def test_changing_the_band_clears_the_probe_errors(monkeypatch):
    fractions = {"hist": {"0.5": 1, "1": 2}, "meta": {}}
    no_spread = {"a_p2": 3, "a_p98": 3}
    state, open_, saved, gee = _probing(monkeypatch, ["a", "b"], _answer(fractions), _answer(no_spread))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
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
            # a new kind clears them too
            _field(rc, "steps.layers.field_band", v.Select).v_model = "a"
            await _find(rc)
            assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "file"
            assert not _error_shown(rc, "steps.layers.err_not_integer")
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "gee"
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
            [tile] = [b for b in _style_tiles(rc) if t("steps.layers.style_continuous") in _flat(b)]
            tile.fire_event("click", None)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "a"
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: _error_shown(rc, "steps.layers.err_no_stretch"))
            _field(rc, "steps.layers.field_kind", v.Select).v_model = "file"
            assert not _error_shown(rc, "steps.layers.err_no_stretch")
        finally:
            rc.close()

    asyncio.run(main())


def _categorical_edit():
    p = _with_aoi()
    p.vis_layers = {"lc": GeeVisLayer(name="lc", asset="users/x/img", bands=["Map"], style="categorical",
                                      classes=[VisClass(value=1, color="aa0000", label="Forest"),
                                               VisClass(value=2, color="00aa00", label="Crops", visible=False)])}
    return p


def test_an_edit_opens_with_its_classes(monkeypatch):
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], p=_categorical_edit())

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert _labels(rc) == ["Forest", "Crops"]
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            await asyncio.sleep(0.05)
            assert _labels(rc) == ["Forest", "Crops"]  # the first band load keeps them
            assert _hide_icons(rc) == ["mdi-eye", "mdi-eye-off"]
            assert ["#aa0000" in s for s in _swatches(rc)] == [True, False]
        finally:
            rc.close()

    asyncio.run(main())


def test_a_band_with_fractions_says_so_inline(monkeypatch):
    fractions = {"hist": {"0.5": 1, "1": 2}, "meta": {}}
    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(fractions), p=_categorical_edit())

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            await _find(rc)
            assert await _settle(lambda: _error_shown(rc, "steps.layers.err_not_integer"))
            assert _labels(rc) == ["Forest", "Crops"]  # the list is left unchanged
        finally:
            rc.close()

    asyncio.run(main())


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
            assert _field(rc, "steps.layers.field_band", v.Select).disabled is True  # frozen while it runs
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

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            await _categorical(rc)
            await _find(rc)
            assert await _settle(lambda: _labels(rc) == ["10", "80"])
            rc.find(v.ColorPicker).widgets[0].v_model = "#123456"
            rc.find(v.TextField, label=t("steps.layers.class_label")).widgets[1].v_model = "Water"
            _by_class(rc, "dv-class-hide")[1].fire_event("click", None)
            _field(rc, "steps.layers.class_add").v_model = "99"
            _by_class(rc, "dv-class-add")[0].fire_event("click", None)
            assert _labels(rc) == ["10", "Water", "99"]
            await _find(rc)
            assert await _settle(lambda: _labels(rc) == ["10", "20", "Water", "99"])
            assert "#123456" in _swatches(rc)[0]  # a hand-picked colour stays
            assert _hide_icons(rc) == ["mdi-eye", "mdi-eye", "mdi-eye-off", "mdi-eye"]
        finally:
            rc.close()

    asyncio.run(main())


def test_enter_in_add_a_value_adds_it_and_does_not_save(monkeypatch):
    from gui.widget.enter_key import ENTER_OWN

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER))

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
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
        finally:
            rc.close()

    asyncio.run(main())


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

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
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
        finally:
            rc.close()

    asyncio.run(main())


def test_a_geotiff_categorical_edit_keeps_its_classes(monkeypatch, tmp_path):
    from deforestation_alerts.project.models import FileVisLayer
    from tests.test_vis_layers import _tif

    tif = _tif(tmp_path / "lc.tif", size=10, count=2)
    p = _with_aoi()
    p.vis_layers = {"lc": FileVisLayer(name="lc", path=str(tif), band=2, style="categorical",
                                       classes=[VisClass(value=1, color="aa0000", label="Forest")])}
    state, open_, saved, gee = _gee_modal(monkeypatch, [], p)

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert _labels(rc) == ["Forest"]
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 2)
            await asyncio.sleep(0.05)
            assert _field(rc, "steps.layers.field_band", v.Select).v_model == 2
            assert _labels(rc) == ["Forest"]
        finally:
            rc.close()

    asyncio.run(main())


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

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert await _settle(lambda: not _button(rc, "steps.layers.stretch").disabled)
            _button(rc, "steps.layers.stretch").fire_event("click", None)
            assert await _settle(lambda: _field(rc, "steps.layers.field_max").v_model == "42")
            assert _field(rc, "steps.layers.field_min").v_model == "3"
            assert asked == [("STRETCH", tif, [2], tuple(state.project.value.aoi.bounds))]
        finally:
            rc.close()

    asyncio.run(main())


def test_find_classes_runs_on_the_geotiff_band(monkeypatch, tmp_path):
    state, open_, saved, gee, tif, asked = _file_edit(
        monkeypatch, tmp_path, "categorical", [VisClass(value=1, color="aa0000", label="Forest")])

    async def main():
        _box, rc = _render(state, open_, saved, gee, editing="lc")
        try:
            assert await _settle(lambda: not _button(rc, "steps.layers.find_classes").disabled)
            await _find(rc)
            assert await _settle(lambda: _labels(rc) == ["Forest", "7"])
            assert asked == [("CLASSES", tif, 2, tuple(state.project.value.aoi.bounds))]
        finally:
            rc.close()

    asyncio.run(main())


def test_the_probe_buttons_are_disabled_without_an_aoi(monkeypatch):
    from tests.test_vis_layers import _p

    state, open_, saved, gee = _probing(monkeypatch, ["Map"], _answer(WORLDCOVER), _answer({}), p=_p())

    async def main():
        _box, rc = _render(state, open_, saved, gee)
        try:
            vlm.RasterInput.seen.asset.set(ASSET)
            assert await _settle(lambda: len(_field(rc, "steps.layers.field_band", v.Select).items) == 1)
            _field(rc, "steps.layers.field_band", v.Select).v_model = "Map"
            await asyncio.sleep(0.05)
            assert _button(rc, "steps.layers.stretch").disabled is True
            await _categorical(rc)
            await asyncio.sleep(0.05)
            assert _button(rc, "steps.layers.find_classes").disabled is True
        finally:
            rc.close()

    asyncio.run(main())
