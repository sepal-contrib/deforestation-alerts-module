"""The Analysis filter's pure helpers (spec §8.2, §8.4): which attributes it
offers, their values with counts, which rows match, and where navigation and
the start position go among the matches."""

import geopandas as gpd
import numpy as np
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import Question, QuestionOption, default_questions
from gui.i18n import t
from gui.scripts.alert_filter import (
    AlertFilter,
    ColumnChoice,
    filter_columns,
    first_unreviewed,
    kinds_of,
    match_mask,
    next_match,
    next_unreviewed_match,
    prev_match,
    start_position,
    value_choices,
)


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


QS = default_questions() + [Question(key="road", label="Road nearby?", options=[
    QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])]


def _alerts(**cols):
    n = len(next(iter(cols.values())))
    base = {"alert_id": list(range(n)), "set_id": ["s"] * n, "alert_sources": ["GLAD-L"] * n,
            "count": [3] * n, "alert_date_min": [2026.01] * n, "alert_date_max": [2026.02] * n,
            "area_ha": [1.5] * n, "before_img": ["2026-01-01"] * n, "before_img_info": [0.5] * n,
            "alert_type_unique": [np.array([1, 2])] * n}
    base.update(cols)
    return gpd.GeoDataFrame(base, geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def _six():
    """Six alerts: two evaluators, mixed statuses, multi-answer loss drivers."""
    return _alerts(
        evaluator=["Ana", "Bruno", "Ana", "Bruno", "Ana", ""],
        status=["Confirmed", "Not reviewed", None, "False Positive", "", "Need revision"],
        description=["logging, mining", "", np.nan, "logging", "Roads; bridges", "mining"],
        q_road=["yes", "no", "", "yes", None, "no"],
        admin1=["North", "South", "North", "North", "South", "South"],
        q_deleted=["a", "b", "a", "b", "a", "b"],
    )


# ---- the attributes offered ---------------------------------------------------

def test_the_columns_offered_and_their_order():
    gdf = _six().assign(region_code=[1, 2, 1, 2, 3, 3], flag=[True, False] * 3, single=["x"] * 6,
                        many=[f"v{i}" for i in range(6)])
    choices = filter_columns(gdf, QS)
    assert [c.column for c in choices] == ["evaluator", "status", "description", "q_road",
                                           "admin1", "region_code", "flag", "many"]
    assert choices[0] == ColumnChoice("evaluator", t("analysis_tile.filter.col_evaluator"))
    assert choices[1].label == t("analysis_tile.facts.status")
    # a question's column wears the question's label and names its key
    assert choices[2] == ColumnChoice("description", t("analysis_tile.questionarie.loss_driver_label"), "loss_driver")
    assert choices[3] == ColumnChoice("q_road", "Road nearby?", "road")
    assert choices[4] == ColumnChoice("admin1", "admin1")


def test_ids_measures_dates_images_and_lists_are_never_offered():
    offered = {c.column for c in filter_columns(_six(), QS)}
    for col in ("alert_id", "set_id", "alert_sources", "count", "alert_date_min", "alert_date_max",
                "area_ha", "before_img", "before_img_info", "alert_type_unique", "geometry"):
        assert col not in offered, col
    assert "q_deleted" not in offered  # a deleted question's answers


def test_review_group_is_not_a_filter_attribute():
    gdf = _six().assign(review_group=[1, 1, 2, 2, 3, 3])
    assert "review_group" not in {c.column for c in filter_columns(gdf, QS)}


def test_a_column_needs_two_to_fifty_distinct_values():
    few = _alerts(code=["a"] * 3)
    assert [c.column for c in filter_columns(few, [])] == []
    many = _alerts(code=[f"c{i}" for i in range(51)])
    assert [c.column for c in filter_columns(many, [])] == []
    ok = _alerts(code=[f"c{i % 50}" for i in range(60)])
    assert [c.column for c in filter_columns(ok, [])] == ["code"]


def test_no_table_offers_nothing_and_a_store_without_the_split_has_no_evaluator():
    assert filter_columns(None, QS) == []
    bare = _six().drop(columns=["evaluator"])
    assert "evaluator" not in {c.column for c in filter_columns(bare, QS)}


# ---- the values offered ---------------------------------------------------------

def test_evaluator_values_count_and_name_the_unassigned():
    choice = ColumnChoice("evaluator", "Evaluator")
    assert value_choices(_six(), choice, QS) == [
        ("Ana", "Ana", 3), ("Bruno", "Bruno", 2), ("", t("analysis_tile.filter.unassigned"), 1)]


def test_status_values_read_every_unreviewed_row_as_not_reviewed():
    got = value_choices(_six(), ColumnChoice("status", "Status"), QS)
    assert got == [("Not reviewed", t("legend.status.not_reviewed"), 3),
                   ("Confirmed", t("legend.status.confirmed"), 1),
                   ("False Positive", t("legend.status.false_positive"), 1),
                   ("Need revision", t("legend.status.needs_revision"), 1)]


def test_a_multi_answer_question_counts_each_code_once_per_row():
    got = value_choices(_six(), ColumnChoice("description", "Loss driver", "loss_driver"), QS)
    assert got == [("logging", t("analysis_tile.questionarie.loss_driver3"), 2),
                   ("mining", t("analysis_tile.questionarie.loss_driver6"), 2),
                   ("Roads; bridges", "Roads; bridges", 1),          # typed, as typed
                   ("", t("analysis_tile.filter.empty_value"), 2)]  # "" and NaN


def test_typed_option_labels_stay_and_the_empty_label_follows_the_language():
    set_locale("es-ES")
    got = value_choices(_six(), ColumnChoice("q_road", "Road nearby?", "road"), QS)
    assert got == [("yes", "Yes", 2), ("no", "No", 2), ("", t("analysis_tile.filter.empty_value"), 2)]
    assert t("analysis_tile.filter.empty_value") != "(empty)"


def test_other_columns_list_their_values_sorted():
    gdf = _six().assign(flag=[True, False, True, True, None, False])
    assert value_choices(gdf, ColumnChoice("admin1", "admin1"), QS) == [("North", "North", 3), ("South", "South", 3)]
    assert value_choices(gdf, ColumnChoice("flag", "flag"), QS) == [
        ("False", "False", 2), ("True", "True", 3), ("", t("analysis_tile.filter.empty_value"), 1)]
    assert value_choices(None, ColumnChoice("admin1", "admin1"), QS) == []


# ---- matching ---------------------------------------------------------------------

def test_no_filter_matches_every_row():
    assert match_mask(_six(), None, QS).tolist() == [True] * 6
    assert match_mask(None, None, QS).tolist() == []
    assert match_mask(None, AlertFilter("evaluator", frozenset({"Ana"})), QS).tolist() == []


def test_values_of_one_attribute_match_with_or():
    gdf = _six()
    assert match_mask(gdf, AlertFilter("evaluator", frozenset({"Ana"})), QS).tolist() == [
        True, False, True, False, True, False]
    assert match_mask(gdf, AlertFilter("evaluator", frozenset({"Bruno", ""})), QS).tolist() == [
        False, True, False, True, False, True]
    assert match_mask(gdf, AlertFilter("status", frozenset({"Not reviewed"})), QS).tolist() == [
        False, True, True, False, True, False]
    assert not match_mask(gdf, AlertFilter("evaluator", frozenset()), QS).any()


def test_a_question_matches_any_of_its_codes_and_empty_matches_the_unanswered():
    gdf = _six()
    f = AlertFilter("description", frozenset({"mining"}))
    assert match_mask(gdf, f, QS).tolist() == [True, False, False, False, False, True]
    f = AlertFilter("description", frozenset({"", "Roads; bridges"}))
    assert match_mask(gdf, f, QS).tolist() == [False, True, True, False, True, False]
    # labels written by an older release read back to their codes
    old = gdf.assign(description=["Logging, Mining", "", "", "", "", ""])
    assert match_mask(old, AlertFilter("description", frozenset({"mining"})), QS).tolist()[0] is True


def test_a_store_without_the_column_reads_as_all_empty():
    bare = _six().drop(columns=["evaluator"])
    assert match_mask(bare, AlertFilter("evaluator", frozenset({""})), QS).all()
    assert not match_mask(bare, AlertFilter("evaluator", frozenset({"Ana"})), QS).any()


# ---- navigation among the matches -----------------------------------------------------

def test_next_and_prev_match_skip_the_others_and_stop_at_the_ends():
    mask = np.array([True, False, True, False, True, False])
    assert next_match(mask, 0) == 2 and next_match(mask, 2) == 4 and next_match(mask, 4) is None
    assert prev_match(mask, 4) == 2 and prev_match(mask, 0) is None
    # from a position outside the filter, the neighbours on either side
    assert next_match(mask, 1) == 2 and prev_match(mask, 3) == 2
    assert next_match(mask, -1) == 0 and prev_match(mask, 6) == 4
    assert next_match(np.zeros(3, bool), -1) is None and prev_match(np.zeros(3, bool), 3) is None


def test_the_unreviewed_helpers_keep_to_the_matches():
    gdf = _six()
    ana = match_mask(gdf, AlertFilter("evaluator", frozenset({"Ana"})), QS)  # rows 0, 2, 4
    assert first_unreviewed(gdf, ana) == 2
    assert next_unreviewed_match(gdf, ana, 2) == 4
    assert next_unreviewed_match(gdf, ana, 4) == 2  # wraps round
    assert next_unreviewed_match(gdf, ana, 0) == 2
    only = np.array([False, False, True, False, False, False])
    assert next_unreviewed_match(gdf, only, 2) is None  # never the alert on screen
    assert first_unreviewed(gdf, np.array([True, False, False, True, False, True])) is None
    assert first_unreviewed(None, ana) is None and next_unreviewed_match(None, ana, 0) is None


def test_without_a_status_column_every_match_is_unreviewed():
    bare = _six().drop(columns=["status"])
    assert first_unreviewed(bare, np.ones(6, bool)) == 0
    assert next_unreviewed_match(bare, np.ones(6, bool), 0) == 1


# ---- the start position -------------------------------------------------------------------

def test_without_a_filter_the_start_is_the_saved_position_clamped():
    gdf = _six()
    assert start_position(gdf, None, 3, QS) == 3
    assert start_position(gdf, None, 99, QS) == 5
    assert start_position(gdf, None, -4, QS) == 0
    assert start_position(None, None, 3, QS) == -1
    assert start_position(gdf.iloc[0:0], None, 3, QS) == -1


def test_a_filter_keeps_a_matching_position_else_the_first_unreviewed_match():
    gdf = _six()
    ana = AlertFilter("evaluator", frozenset({"Ana"}))
    assert start_position(gdf, ana, 4, QS) == 4          # matches: stays
    assert start_position(gdf, ana, 1, QS) == 2          # Bruno's: Ana's first unreviewed
    reviewed = gdf.assign(status=["Confirmed"] * 6)
    assert start_position(reviewed, ana, 1, QS) == 0     # all of Ana's reviewed: her first
    nobody = AlertFilter("evaluator", frozenset({"Carla"}))
    assert start_position(gdf, nobody, 3, QS) == 3       # nothing matches: stays
    assert start_position(gdf, nobody, 99, QS) == 5


# ---- the Kind column (growth alerts, spec §6) -----------------------------------

LINK = "old#3@20250101000000000000"


def _kinds():
    """Five alerts: new, growth, growth under the minimum, new under it, new."""
    return _alerts(
        status=["Not reviewed"] * 5,
        grows_from=[[], [LINK], [LINK], [], None],
        below_min=[False, False, True, True, False],
    )


def test_kind_filter_offered_only_with_growth():
    cols = filter_columns(_kinds(), QS)
    names = [c.column for c in cols]
    assert names[names.index("status") + 1] == "kind"
    assert next(c for c in cols if c.column == "kind").label == t("analysis_tile.facts.kind")
    choices = value_choices(_kinds(), ColumnChoice("kind", "Kind"), QS)
    assert [(v_, label, n) for v_, label, n in choices] == [
        ("new", t("analysis_tile.facts.kind_new"), 2),
        ("growth", t("analysis_tile.facts.kind_growth"), 1),
        ("growth_small", t("analysis_tile.facts.kind_growth_small"), 1),
        ("new_small", t("analysis_tile.facts.kind_new_small"), 1),
    ]


def test_growth_columns_are_never_offered_raw():
    gdf = _kinds()
    gdf["grows_from_auto"] = [[], [LINK], [], [], [LINK]]
    assert [c.column for c in filter_columns(gdf, QS)] == ["status", "kind"]


def test_kind_filter_absent_on_old_set():
    assert "kind" not in [c.column for c in filter_columns(_six(), QS)]
    assert "kind" not in [c.column for c in filter_columns(_alerts(status=["Not reviewed"] * 2,
                                                                     grows_from=[[], []],
                                                                     below_min=[False, False]), QS)]


def test_kind_filter_matches():
    gdf = _kinds()
    assert match_mask(gdf, AlertFilter("kind", frozenset({"growth", "growth_small"})), QS).tolist() == [
        False, True, True, False, False]
    assert match_mask(gdf, AlertFilter("kind", frozenset({"new"})), QS).tolist() == [True, False, False, False, True]
    assert kinds_of(_six()).tolist() == ["new"] * 6
