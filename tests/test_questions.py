"""Answers per question (spec D2): codes in the store, labels on screen and in
exports, legacy loss-driver labels read back, deleted questions' columns
dropped from exports."""

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import Question, QuestionOption, default_questions
from gui.scripts import questions as qs

LOSS = default_questions()[0]
ROAD = Question(key="road_access", label="Road access",
                options=[QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def test_labels_follow_the_locale_for_built_ins_and_stay_as_typed_otherwise():
    assert qs.question_label(LOSS) == "Loss driver"
    assert qs.question_label(ROAD) == "Road access"
    set_locale("fr")
    assert qs.option_label(LOSS.options[2]) == "Exploitation forestière"
    assert qs.items(ROAD) == [{"text": "Yes", "value": "yes"}, {"text": "No", "value": "no"}]


def test_encode_takes_codes_items_labels_and_typed_text():
    assert qs.encode(ROAD, ["yes"]) == "yes"
    assert qs.encode(ROAD, [{"text": "No", "value": "no"}]) == "no"
    assert qs.encode(ROAD, ["Yes", "yes"]) == "yes"          # a label reads back to its code, once
    assert qs.encode(ROAD, ["Maybe"]) == "Maybe"             # typed
    assert qs.encode(ROAD, None) == "" and qs.encode(ROAD, []) == ""
    assert qs.encode(ROAD, "no") == "no"                     # a single answer


def test_a_typed_answer_never_splits_into_two():
    """Review focus 1: the store joins answers with ', '."""
    stored = qs.encode(ROAD, ["Roads, bridges"])
    assert stored == "Roads; bridges"
    assert qs.codes(ROAD, stored) == ["Roads; bridges"]


def test_codes_reads_legacy_driver_labels_but_not_for_other_questions():
    assert qs.codes(LOSS, "Logging, Mining and Other") == ["logging", "mining", "other"]
    assert qs.codes(LOSS, "Tala") == ["logging"]            # a Spanish label of a built-in option
    assert qs.codes(ROAD, "Yes and No") == ["Yes and No"]   # no legacy join outside loss_driver
    assert qs.codes(ROAD, None) == [] and qs.codes(ROAD, float("nan")) == []


def test_as_items_and_label():
    assert qs.as_items(ROAD, ["yes", "Maybe"]) == [{"text": "Yes", "value": "yes"}, "Maybe"]
    assert qs.selection(ROAD, "no") == [{"text": "No", "value": "no"}]
    assert qs.label(ROAD, "yes, Maybe") == "Yes, Maybe"
    assert qs.label(ROAD, "") == ""


def test_a_removed_option_shows_its_stored_text():
    """U5: an answer whose option is gone reads as what was stored."""
    assert qs.label(ROAD, "unclear") == "unclear"


def test_visible_follows_ask_for_and_shows_everything_before_a_verdict():
    assert qs.visible(LOSS, None)
    assert qs.visible(LOSS, "Confirmed") and not qs.visible(LOSS, "False Positive")
    assert qs.visible(ROAD, "False Positive")


@pytest.mark.parametrize("verdict", ["", "Not reviewed"])
def test_a_value_that_is_not_a_verdict_shows_every_question(verdict):
    """Spec D4: an unreviewed alert's status is no verdict yet."""
    assert qs.visible(LOSS, verdict)


def test_with_labels_translates_current_questions_and_drops_deleted_ones():
    gdf = gpd.GeoDataFrame({"description": ["logging, other"], "q_road_access": ["yes"],
                            "q_gone": ["x"], "status": ["Confirmed"]}, geometry=[box(0, 0, 1, 1)])
    out = qs.with_labels(gdf, [LOSS, ROAD])
    assert out.loc[0, "description"] == "Logging, Other"
    assert out.loc[0, "q_road_access"] == "Yes"
    assert "q_gone" not in out.columns and out.loc[0, "status"] == "Confirmed"
    assert gdf.loc[0, "q_road_access"] == "yes"  # a copy


def test_with_labels_drops_description_when_loss_driver_was_deleted():
    gdf = pd.DataFrame({"description": ["logging"], "q_road_access": ["no"]})
    out = qs.with_labels(gdf, [ROAD])
    assert list(out.columns) == ["q_road_access"] and out.loc[0, "q_road_access"] == "No"


def test_with_labels_tolerates_a_missing_column_and_none():
    assert qs.with_labels(None, [ROAD]) is None
    out = qs.with_labels(pd.DataFrame({"status": ["Confirmed"]}), [LOSS, ROAD])
    assert list(out.columns) == ["status"]


def test_a_stored_comma_reads_back_as_stored():
    """Only a save turns a typed comma into ";": a store written before that
    rule shows its text as it was stored (U5: stored answers are never rewritten)."""
    assert qs.codes(ROAD, "Roads,bridges") == ["Roads,bridges"]
    assert qs.label(ROAD, "Roads,bridges") == "Roads,bridges"


def test_with_labels_keeps_a_missing_answer_missing():
    """As ``loss_drivers.with_labels`` did: an unanswered row stays empty, not "nan"."""
    out = qs.with_labels(pd.DataFrame({"q_road_access": ["yes", None, ""]}), [ROAD])
    got = out["q_road_access"].tolist()
    assert got[0] == "Yes" and pd.isna(got[1]) and got[2] == ""
