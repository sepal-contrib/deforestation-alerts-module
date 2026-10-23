from deforestation_alerts.project.models import VisClass
from gui.scripts import vis_classes as vc
from gui.scripts import vis_palettes


def test_found_values_get_the_palette_in_order_and_their_value_as_label():
    got = vc.classes_from_values([30, 10, 20], palette="set1")
    assert [c.value for c in got] == [10, 20, 30]
    assert [c.color for c in got] == vis_palettes.qualitative_colors("set1", 3)
    assert [c.label for c in got] == ["10", "20", "30"] and all(c.visible for c in got)


def test_known_class_metadata_wins():
    known = {"values": [10, 80], "palette": ["006400", "0064C8"], "names": ["Tree cover", "Water"]}
    got = vc.classes_from_values([10, 50, 80], known=known)
    assert [(c.value, c.color, c.label) for c in got][0] == (10, "006400", "Tree cover")
    assert got[2].color == "0064c8" and got[1].label == "50"


def test_a_zero_one_band_hides_zero():
    zero, one = vc.classes_from_values([1, 0])
    assert (zero.visible, one.visible, one.color) == (False, True, vis_palettes.qualitative_colors("set1", 1)[0])


def test_ee_remap_drops_hidden_classes():
    cls = [VisClass(value=10, color="aa0000"), VisClass(value=20, color="00aa00", visible=False),
           VisClass(value=30, color="0000aa")]
    assert vc.ee_remap(cls) == ([10, 30], [0, 1], {"min": 0, "max": 1, "palette": ["aa0000", "0000aa"]})


def test_the_lut_is_discrete_for_adjacent_and_sparse_values():
    cls = [VisClass(value=v, color="aa0000") for v in (1, 2, 10)]
    assert vc.lut_text(cls) == "0.75:0,1:1,1.25:0,1.75:0,2:2,2.25:0,9.75:0,10:3,10.25:0"


def test_a_single_class_colormap_has_two_stops():
    assert vc.class_colormap([VisClass(value=1, color="aa0000")]).N == 2


def test_recolor_keeps_value_label_and_visibility():
    cls = [VisClass(value=5, color="aa0000", label="Five", visible=False), VisClass(value=7, color="00aa00")]
    got = vc.recolor(cls, "tab10")
    assert [(c.value, c.label, c.visible) for c in got] == [(5, "Five", False), (7, "", True)]
    assert [c.color for c in got] == vis_palettes.qualitative_colors("tab10", 2)


def test_legend_colors_labels_use_visible_classes_sorted_with_hash():
    cls = [VisClass(value=30, color="0000aa", label="Water"), VisClass(value=20, color="00aa00", visible=False),
           VisClass(value=10, color="aa0000")]
    assert vc.legend_colors_labels(cls) == (("#aa0000", "#0000aa"), ("10", "Water"))


def test_colormap_uses_visible_colours_with_hash():
    cls = [VisClass(value=2, color="0000aa"), VisClass(value=1, color="aa0000"),
           VisClass(value=3, color="00aa00", visible=False)]
    cmap = vc.class_colormap(cls)
    assert cmap.N == 2 and cmap(0)[:3] == (0.6666666666666666, 0.0, 0.0)


def test_qualitative_colors_cycle_and_are_bare():
    got = vis_palettes.qualitative_colors("set2", 10)
    assert len(got) == 10 and got[8] == got[0] and all(len(c) == 6 and c == c.lower() and "#" not in c for c in got)
    assert vis_palettes.qualitative_css("paired").count("#") == 12


def test_a_new_find_keeps_the_listed_classes_and_adds_the_new_values():
    listed = [VisClass(value=10, color="123456", label="Forest", visible=False),
              VisClass(value=99, color="abcdef", label="by hand")]
    found = vc.classes_from_values([10, 20], palette="set1")
    merged = vc.merge_found(listed, found)
    assert merged == [listed[0], found[1], listed[1]]


def test_palette_of_names_the_palette_the_classes_are_painted_from():
    classes = vc.classes_from_values([1, 2, 3], palette="set1")
    assert vc.palette_of(classes) == "set1"
    assert vc.palette_of(vc.recolor(classes, "paired")) == "paired"
    assert vc.palette_of([classes[0].model_copy(update={"color": "000000"}), *classes[1:]]) is None
    assert vc.palette_of([]) is None


def test_looks_categorical_by_name_tokens():
    for name in ("classification", "landcover", "LULC", "lossyear", "forest_mask", "datamask", "lc_2020", "loss"):
        assert vc.looks_categorical(name), name
    for name in ("B4", "NDVI", "treecover2000", "SR_B5", "Band 1", "glossy"):
        assert not vc.looks_categorical(name), name


def test_suggest_mask_band():
    # a name token ending in "mask" first, then a categorical name, else none
    assert vc.suggest_mask_band(["treecover2000", "loss", "gain", "lossyear", "datamask"]) == 4
    assert vc.suggest_mask_band(["urban", "water_mask", "cloud_mask"]) == 1
    assert vc.suggest_mask_band(["B2", "B3", "B4"]) is None
    assert vc.suggest_mask_band(["forest", "loss"]) == 1
    assert vc.suggest_mask_band(["loss", "datamask"]) == 1  # a mask token beats an earlier categorical name
    assert vc.suggest_mask_band(["Band 1"]) is None
    assert vc.suggest_mask_band([]) is None


def test_a_zero_one_find_names_1_after_the_band_and_hides_0():
    zero, one = vc.classes_from_values([1, 0, 1], band_label="loss")
    assert (zero.value, zero.visible, zero.label) == (0, False, "0")
    assert (one.value, one.visible, one.label) == (1, True, "loss")


def test_declared_classes_win_over_the_band_label():
    known = {"values": [0, 1], "palette": ["000000", "00ff00"], "names": ["No forest", "Forest"]}
    zero, one = vc.classes_from_values([0, 1], known=known, band_label="mask")
    assert (zero.label, one.label) == ("No forest", "Forest")


def test_the_band_label_only_applies_to_zero_one():
    assert [c.label for c in vc.classes_from_values([1, 2], band_label="lc")] == ["1", "2"]


def test_is_zero_one():
    assert vc.is_zero_one([0, 1, 1]) and not vc.is_zero_one([1]) and not vc.is_zero_one([0, 1, 2])
