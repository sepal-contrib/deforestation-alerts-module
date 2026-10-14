import pytest

from gui.scripts import vis_palettes as vp


def test_ramps_round_trip_through_ramp_choice():
    for key in vp.RAMPS:
        for inv in (False, True):
            assert vp.ramp_choice(vp.ramp_palette(key, inv)) == (key, inv)
    assert vp.ramp_choice(["ff0000", "00ff00"]) == (None, False)
    assert vp.ramp_choice([]) == (None, False)


def test_ramp_palette_accepts_hash_prefixed_input_in_choice():
    pal = ["#" + c for c in vp.ramp_palette("viridis")]
    assert vp.ramp_choice(pal) == ("viridis", False)


@pytest.mark.parametrize("names, expected", [
    (["B2", "B3", "B4", "B8", "B11", "B12"], {"blue": "B2", "green": "B3", "red": "B4", "nir": "B8", "swir": "B11"}),
    (["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"],
     {"blue": "SR_B2", "green": "SR_B3", "red": "SR_B4", "nir": "SR_B5", "swir": "SR_B6"}),
    (["red", "green", "blue", "nir"], {"red": "red", "green": "green", "blue": "blue", "nir": "nir"}),
    (["R", "G", "B", "N"], {"red": "R", "green": "G", "blue": "B", "nir": "N"}),
    (["band_1", "band_2"], {}),
])
def test_guess_roles(names, expected):
    assert vp.guess_roles(names) == expected


def test_combos_need_their_roles():
    roles = {"red": "B4", "green": "B3", "blue": "B2", "nir": "B8"}
    assert vp.available_combos(roles) == ["true_color", "false_color"]
    assert vp.combo_bands("false_color", roles) == ["B8", "B4", "B3"]
    assert vp.combo_bands("swir", roles) is None
    assert vp.combo_bands("swir", {**roles, "swir": "B11"}) == ["B11", "B8", "B4"]


def test_ramp_css_and_colormap():
    assert vp.ramp_css("grayscale") == "linear-gradient(to right, #000000, #ffffff)"
    assert vp.ramp_css("grayscale", True) == "linear-gradient(to right, #ffffff, #000000)"
    cmap = vp.ramp_colormap(vp.ramp_palette("grayscale"))
    assert cmap(0.0)[:3] == (0.0, 0.0, 0.0)
