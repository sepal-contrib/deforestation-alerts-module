"""The browser walks read their labels from the English catalogue and the
app's own class names. A renamed key or class breaks this test, instead of a
walk timing out twenty minutes in."""

import pytest

from gui.widget.enter_key import ENTER_DEFAULT
from tools.walk import phases, walklib


def test_the_walks_press_the_apps_enter_default():
    assert walklib.ENTER_BUTTON == f"button.{ENTER_DEFAULT}"


def test_the_modal_scope_leaves_the_rail_dialog_out():
    # MapApp's rail dialog is a v-dialog too (content-class "dialog-container").
    assert walklib.MODAL == ".v-dialog--active:not(.dialog-container)"
    assert phases.MODAL is walklib.MODAL


def test_en_reads_the_english_catalogues():
    assert walklib.en("common.cancel") == "Cancel"
    assert walklib.en("steps.alerts.add") == "Add source"
    with pytest.raises(KeyError):
        walklib.en("steps.alerts.no_such_key")
    with pytest.raises(KeyError):
        walklib.en("steps.project.count_sources")  # a plural node, not a text


def test_the_labels_the_walks_pick_exist():
    assert phases.SOURCE_TYPE == "Source type"
    assert phases.RASTER_SOURCE == walklib.en("steps.raster_input.origin_label")
    assert phases.ORIGIN == {"gee": "Earth Engine asset", "file": "GeoTIFF in my SEPAL folder"}
    assert phases.MODE_OFF == walklib.en("steps.prepare.mask_mode_off")
    for key in ("steps.project.empty_heading", "steps.project.new", "steps.project.search",
                "steps.project.no_match", "steps.project.name_label", "steps.project.name_chars",
                "steps.integrate.attr_add", "steps.integrate.attr_load", "steps.integrate.attr_loaded",
                "steps.period.new", "steps.period.created"):
        assert walklib.en(key), key
