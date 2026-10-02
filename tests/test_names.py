from datetime import datetime

import pytest

from deforestation_alerts.project import names
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, BandMap, CustomSource, MaskLayer, Project, add_custom_source,
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
])
def test_source_name_rules(name, key):
    assert names.validate_source_name(name, _p("GLAD-L")) == key


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
