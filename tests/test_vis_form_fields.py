"""The visualization form's dropdown fields (gui/widget/vis_style_fields): the
Bands helper (a line for one band, the Band select for two, the Bands question
for three or more), the Raster type, the Colour ramp with its Invert switch,
and the Composite with its detected-roles hint."""

import ipyvuetify as v
import reacton

from gui.i18n import t
from gui.scripts import vis_palettes
from gui.widget import vis_style_fields as vsf
from tests.test_vis_layers import _flat

S2 = ["B2", "B3", "B4", "B8", "B11", "B12"]


def _items(names):
    return [{"text": n, "value": n} for n in names]


def _selects(rc, key):
    return rc.find(v.Select, label=t(key)).widgets


def _text(rc):
    """All text under the rendered widgets."""
    return " ".join(_flat(w) for w in rc.find(v.Html).widgets)


def _bands_field(**kwargs):
    calls = []
    props = dict(band_items=_items(S2), mode="all", on_mode=lambda m: calls.append(("mode", m)), band=None,
                 on_band=lambda b: calls.append(("band", b)), rgb_found=True)
    props.update(kwargs)
    _box, rc = reacton.render(vsf.BandsField(**props), handle_error=False)
    return rc, calls


def test_one_band_shows_a_line_and_no_select():
    rc, _ = _bands_field(band_items=_items(["NDVI"]))
    try:
        assert not rc.find(v.Select).widgets
        assert t("steps.layers.bands_one_hint", name="NDVI") in _text(rc)
    finally:
        rc.close()


def test_two_bands_show_only_the_band_select():
    rc, calls = _bands_field(band_items=_items(["VV", "VH"]), mode="one")
    try:
        assert not _selects(rc, "steps.layers.field_bands")
        [band] = _selects(rc, "steps.layers.field_band")
        assert len(band.items) == 2 and "dv-band" in band.class_.split()
        assert band.hint == t("steps.layers.bands_two_hint") and band.persistent_hint
        band.v_model = "VH"
        assert calls == [("band", "VH")]
    finally:
        rc.close()


def test_three_bands_ask_one_or_rgb_and_suggest_rgb_when_found():
    rc, calls = _bands_field(rgb_found=True, mode="all")
    try:
        [bands] = _selects(rc, "steps.layers.field_bands")
        assert [i["text"] for i in bands.items] == [
            t("steps.layers.bands_one"), t("steps.layers.suggested", text=t("steps.layers.bands_all"))]
        assert [i["value"] for i in bands.items] == ["one", "all"]
        assert bands.hint == t("steps.layers.bands_found", count=6) and bands.persistent_hint
        assert not _selects(rc, "steps.layers.field_band")
        bands.v_model = "one"
        assert calls == [("mode", "one")]
    finally:
        rc.close()

    rc, _ = _bands_field(rgb_found=True, mode="one", band="B4")
    try:
        [band] = _selects(rc, "steps.layers.field_band")
        assert band.v_model == "B4" and len(band.items) == 6
    finally:
        rc.close()

    rc, _ = _bands_field(rgb_found=False, mode="one")
    try:
        [bands] = _selects(rc, "steps.layers.field_bands")
        assert [i["text"] for i in bands.items] == [t("steps.layers.bands_one"), t("steps.layers.bands_all")]
        assert bands.hint == t("steps.layers.bands_not_found", count=6)
    finally:
        rc.close()


def test_raster_type_suggests_categorical():
    picked = []
    _box, rc = reacton.render(vsf.RasterTypeSelect(value="continuous", on_value=picked.append, suggested=True),
                              handle_error=False)
    try:
        [select] = _selects(rc, "steps.layers.field_raster_type")
        assert select.items == [
            {"text": t("steps.layers.style_continuous_item"), "value": "continuous"},
            {"text": t("steps.layers.suggested", text=t("steps.layers.style_categorical_item")),
             "value": "categorical"}]
        select.v_model = "categorical"
        assert picked == ["categorical"]
    finally:
        rc.close()
    _box, rc = reacton.render(vsf.RasterTypeSelect(value="continuous", on_value=picked.append, suggested=False),
                              handle_error=False)
    try:
        [select] = _selects(rc, "steps.layers.field_raster_type")
        assert select.items[1]["text"] == t("steps.layers.style_categorical_item")
    finally:
        rc.close()


def _css(rc):
    # solara.Style renders a VuetifyTemplate holding the <style>
    return "".join(w.template for w in rc.find(v.VuetifyTemplate).widgets)


def test_ramp_select_lists_every_ramp_and_styles_the_menu():
    calls = []
    _box, rc = reacton.render(vsf.RampSelect(value="viridis", on_value=calls.append, invert=False,
                                             on_invert=lambda x: calls.append(("invert", x))), handle_error=False)
    try:
        [select] = _selects(rc, "steps.layers.field_ramp")
        assert [i["value"] for i in select.items] == list(vis_palettes.RAMPS)
        assert [i["text"] for i in select.items] == [t(f"steps.layers.ramp_{k}") for k in vis_palettes.RAMPS]
        assert select.menu_props["contentClass"] == "dv-ramp-menu"
        assert "dv-ramp-select" in select.class_.split()
        assert vis_palettes.ramp_menu_css("dv-ramp-menu", False) in _css(rc)
        # the chosen ramp is drawn as its bar and name in the selection slot
        [slot] = [s for s in select.v_slots if s["name"] == "selection"]
        content = slot["children"]
        assert t("steps.layers.ramp_viridis") in content.children
        assert vis_palettes.ramp_css("viridis", False) in content.children[0].style_
        [switch] = rc.find(v.Switch, label=t("steps.layers.field_invert")).widgets
        assert switch.disabled is False
        select.v_model = "magma"
        switch.v_model = True
        assert calls == ["magma", ("invert", True)]
    finally:
        rc.close()

    _box, rc = reacton.render(vsf.RampSelect(value="viridis", on_value=calls.append, invert=True,
                                             on_invert=calls.append), handle_error=False)
    try:
        assert vis_palettes.ramp_menu_css("dv-ramp-menu", True) in _css(rc)
        [select] = _selects(rc, "steps.layers.field_ramp")
        [slot] = [s for s in select.v_slots if s["name"] == "selection"]
        assert vis_palettes.ramp_css("viridis", True) in slot["children"].children[0].style_
    finally:
        rc.close()

    _box, rc = reacton.render(vsf.RampSelect(value=None, on_value=calls.append, invert=False,
                                             on_invert=calls.append), handle_error=False)
    try:
        [switch] = rc.find(v.Switch, label=t("steps.layers.field_invert")).widgets
        assert switch.disabled is True
    finally:
        rc.close()


def _composite(roles, bands, fallback=()):
    picked = []
    _box, rc = reacton.render(vsf.CompositeSelect(combo=None, on_combo=picked.append, roles=roles,
                                                  band_text={b: b for b in bands}, fallback=list(fallback)),
                              handle_error=False)
    [select] = _selects(rc, "steps.layers.field_composite")
    return rc, select, picked


def test_composite_items_name_their_bands_and_disable_missing_roles():
    rc, select, picked = _composite({"red": "B4", "green": "B3", "blue": "B2"}, S2)
    try:
        assert select.items == [
            {"value": "true_color", "text": t("steps.layers.combo_item", name="True colour", bands="B4 / B3 / B2")},
            {"value": "false_color", "disabled": True,
             "text": t("steps.layers.combo_not_found", name="False-colour infrared")},
            {"value": "swir", "disabled": True, "text": t("steps.layers.combo_not_found", name="SWIR / NIR / red")},
            {"value": "custom", "text": t("steps.layers.combo_custom_item")}]
        # the roles in the band list's order (B2 comes first)
        assert select.hint == t("steps.layers.roles_detected", list="B2 blue · B3 green · B4 red")
        assert select.persistent_hint
        select.v_model = "true_color"
        assert picked == ["true_color"]
    finally:
        rc.close()

    rc, select, _ = _composite({"red": "B4", "green": "B3", "blue": "B2"}, ["B4", "B3", "B2"])
    try:
        assert select.hint == t("steps.layers.roles_detected", list="B4 red · B3 green · B2 blue")
    finally:
        rc.close()

    rc, select, _ = _composite({}, ["Band 1", "Band 2", "Band 3"], fallback=["Band 1", "Band 2", "Band 3"])
    try:
        assert select.hint == t("steps.layers.roles_none_rgb", bands="Band 1, Band 2, Band 3")
        assert [i.get("disabled", False) for i in select.items] == [True, True, True, False]
    finally:
        rc.close()


def test_the_new_and_changed_strings_read_as_specified():
    assert t("steps.layers.bands_one") == "Select one band"
    assert t("steps.layers.bands_all") == "RGB composite"
    assert t("steps.layers.roles_detected", list="B4 red") == "Detected from the band names: B4 red"
    assert t("steps.layers.suggested", text="RGB composite") == "RGB composite (suggested)"
