from datetime import datetime

import pytest

from deforestation_alerts.project import names
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, BandMap, MaskLayer, Project, add_custom_source,
    add_predefined_source,
)


def _p(*sources):
    p = Project(name="p", created_at=datetime.now(), updated_at=datetime.now())
    for s in sources:
        if s in ("GLAD-L", "RADD", "GLAD-S2"):
            add_predefined_source(p, s)
        else:
            _custom(p, s)
    return p


def _custom(p, name):
    return add_custom_source(p, name=name, origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd",
                             date_encoding="yyddd", band_map=BandMap(alert="Alert", date="Date"), native=None)


@pytest.mark.parametrize("name, key", [
    ("", "steps.alerts.name_empty"),
    ("   ", "steps.alerts.name_empty"),
    ("x" * 25, "steps.alerts.name_too_long"),
    ("radd", "steps.alerts.name_reserved"),
    ("GLAD-S2", "steps.alerts.name_reserved"),
    ("glad l", "steps.alerts.name_token_taken"),   # token glad-l is GLAD-L's
    ("森林", "steps.alerts.name_token_na"),
    ("Cut and burn", "steps.alerts.name_separator"),  # format_list's joiners
    ("and burn", "steps.alerts.name_separator"),
    ("Cut, burn", "steps.alerts.name_separator"),
])
def test_source_name_rules(name, key):
    assert names.validate_source_name(name, _p("GLAD-L")) == key


@pytest.mark.parametrize("name", ["Cut And burn", "Sandy", "Cut,burn"])
def test_source_names_that_survive_the_alert_sources_round_trip(name):
    from deforestation_alerts.scripts.alert_codes import format_list, parse_formatted_string

    assert names.validate_source_name(name, _p("GLAD-L")) is None
    for items in ([name, "RADD"], ["RADD", name], ["RADD", name, "GLAD-L"]):
        assert parse_formatted_string(format_list(items)) == items


def test_ccdc_is_an_ordinary_name_now():
    assert names.validate_source_name("CCDC", _p()) is None


def test_duplicates_and_shared_tokens():
    p = _p("My Src")
    assert names.validate_source_name("my src", p) == "steps.alerts.name_duplicate"
    assert names.validate_source_name("my-src", p) == "steps.alerts.name_token_taken"
    assert names.validate_source_name("My Src", p, current="My Src") is None


def test_set_slugs_must_stay_distinct():
    assert names.validate_source_name("l", _p("GLAD-L", "glad")) == "steps.alerts.name_set_collision"
    assert names.validate_source_name("l-radd", _p("GLAD-L", "RADD", "glad")) == "steps.alerts.name_set_collision"
    assert names.validate_source_name("forest", _p("GLAD-L", "glad")) is None


def test_a_recorded_set_keeps_its_slug_even_after_its_source_is_gone():
    p = _p("GLAD-L")
    p.alert_sets = {"x__abc": AlertSet(id="x__abc", period_id="x", sources=["abc"], digit_map={3: "abc"},
                                       params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                       footprint_rel_path="f", fingerprint="f", created_at=datetime.now())}
    assert names.validate_source_name("ABC", p) == "steps.alerts.name_set_collision"


def test_mask_name_rules():
    p = _p()
    p.masks = {"Forest 1": MaskLayer.model_construct(name="Forest 1")}  # only the names matter here
    assert names.validate_mask_name("forest 1", p) == "steps.prepare.mask_name_duplicate"
    assert names.validate_mask_name("forest-1", p) == "steps.prepare.mask_name_token_taken"
    assert names.validate_mask_name("", p) == "steps.prepare.mask_name_empty"
    assert names.validate_mask_name("x" * 25, p) == "steps.prepare.mask_name_too_long"
    assert names.validate_mask_name("**", p) == "steps.prepare.mask_name_token_na"
    assert names.validate_mask_name("GLAD-L", p) is None  # no mask name is reserved


from deforestation_alerts.project.names import MAX_NAME, name_from_ref, suggest_name  # noqa: E402


def test_name_from_ref_takes_the_last_asset_segment_or_the_file_stem():
    assert name_from_ref("projects/x/assets/alerts/radd_2024") == "radd_2024"
    assert name_from_ref("/home/u/data/my mask.tif") == "my mask"
    assert name_from_ref("users/a/b/") == "b"
    assert name_from_ref("") == ""
    assert name_from_ref(None) == ""


def test_suggest_name_truncates_and_suffixes_until_accepted():
    taken = {"forest_cover", "forest_cover_2"}
    assert suggest_name("forest_cover", lambda n: n not in taken) == "forest_cover_3"
    long = "a" * 40
    got = suggest_name(long, lambda n: True)
    assert got == "a" * MAX_NAME
    got = suggest_name(long, lambda n: n != "a" * MAX_NAME)
    assert got == "a" * (MAX_NAME - 2) + "_2" and len(got) <= MAX_NAME


def test_suggest_name_gives_up_with_empty_string():
    assert suggest_name("x", lambda n: False) == ""
    assert suggest_name("", lambda n: True) == ""


def test_a_long_base_is_shortened_so_the_combination_suffix_stays_whole():
    from deforestation_alerts.project.names import suggest_name_with_tail

    got = suggest_name_with_tail("a_very_long_asset_name_for_a_mosaic", "_nir_swir_red", lambda n: True)
    assert got == "a_very_long" + "_nir_swir_red" and len(got) == MAX_NAME
    got = suggest_name_with_tail("a_very_long_asset_name_for_a_mosaic", " false colour", lambda n: True)
    assert got.endswith(" false colour") and len(got) == MAX_NAME
    assert suggest_name_with_tail("short", "_rgb", lambda n: True) == "short_rgb"
    taken = {"a_very_long" + "_nir_swir_red"}
    got = suggest_name_with_tail("a_very_long_asset_name_for_a_mosaic", "_nir_swir_red", lambda n: n not in taken)
    assert got.endswith("_nir_swir_red_2") and len(got) <= MAX_NAME
    assert suggest_name_with_tail("x", "_a", lambda n: False) == ""
    assert suggest_name_with_tail("", "", lambda n: True) == ""
