"""Dealing a set's alerts between evaluators (``project/split.py``, spec §4.1):
pure functions, no files."""

import numpy as np
import pandas as pd
import pytest

from deforestation_alerts.errors import KeyedError
from deforestation_alerts.project.models import Question, QuestionOption, default_questions
from deforestation_alerts.project.split import (
    EVALUATOR_COLUMN,
    MAX_EVALUATORS,
    SplitError,
    assignments,
    count_names,
    deal,
    normalize_names,
    questions_digest,
)
from gui.i18n import t
from gui.scripts.error_text import error_text


def test_the_constants():
    assert EVALUATOR_COLUMN == "evaluator" and MAX_EVALUATORS == 50


def test_split_error_carries_its_key_and_values():
    err = SplitError("evaluators.err.too_many", max=50)
    assert err.key == "evaluators.err.too_many" and err.params == {"max": 50} and str(err) == err.key
    assert isinstance(err, KeyedError) and isinstance(err, ValueError)
    assert error_text(err) == t("evaluators.err.too_many", max=50)


def test_names_are_stripped_and_blanks_dropped_in_order():
    assert normalize_names(["  Ana ", "", "   ", "Bo", "Ñandú"]) == ["Ana", "Bo", "Ñandú"]


def test_a_name_listed_twice_ignoring_case_is_refused():
    with pytest.raises(SplitError) as err:
        normalize_names(["Ana", "bo", "ANA"])
    assert err.value.key == "evaluators.err.duplicate_name" and err.value.params == {"name": "ANA"}


def test_no_names_and_too_many_names_are_refused():
    with pytest.raises(SplitError) as err:
        normalize_names(["", "  "])
    assert err.value.key == "evaluators.err.no_names"
    assert len(normalize_names([f"n{i}" for i in range(50)])) == 50
    with pytest.raises(SplitError) as err:
        normalize_names([f"n{i}" for i in range(51)])
    assert err.value.key == "evaluators.err.too_many" and err.value.params == {"max": 50}


def test_count_names():
    assert count_names(3, "Evaluator") == ["Evaluator 1", "Evaluator 2", "Evaluator 3"]
    assert len(count_names(50, "E")) == 50
    for bad in (0, -1, 51, True, 2.5):
        with pytest.raises(SplitError) as err:
            count_names(bad, "E")
        assert err.value.key == "evaluators.err.bad_count" and err.value.params == {"max": 50}


def test_deal_is_round_robin_over_the_unreviewed_rows_in_store_order():
    statuses = ["Not reviewed", "Confirmed", None, "", "Need revision", "Not reviewed", np.nan]
    assert deal(statuses, ["A", "B"], include_reviewed=False) == ["A", "", "B", "A", "", "B", "A"]


def test_deal_includes_reviewed_rows_when_asked():
    statuses = ["Not reviewed", "Confirmed", "False Positive", "Not reviewed"]
    assert deal(statuses, ["A", "B", "C"], include_reviewed=True) == ["A", "B", "C", "A"]


def test_fewer_alerts_than_evaluators_and_no_eligible_alert():
    assert deal(["Not reviewed"], ["A", "B", "C"], include_reviewed=False) == ["A"]
    assert deal(["Confirmed", "Need revision"], ["A"], include_reviewed=False) == ["", ""]
    assert deal([], ["A"], include_reviewed=True) == []
    with pytest.raises(SplitError):
        deal(["Not reviewed"], [], include_reviewed=True)


def test_assignments_reads_missing_values_and_a_missing_column_as_unassigned():
    df = pd.DataFrame({EVALUATOR_COLUMN: ["A", None, "", np.nan, "B"]}, index=[3, 4, 5, 6, 7])
    got = assignments(df)
    assert got.tolist() == ["A", "", "", "", "B"] and list(got.index) == [3, 4, 5, 6, 7]
    bare = pd.DataFrame({"status": ["x", "y"]}, index=[8, 9])
    assert assignments(bare).tolist() == ["", ""] and list(assignments(bare).index) == [8, 9]
    assert assignments(None).tolist() == []


def _q(key, multiple=False, codes=(), label=""):
    return Question(key=key, label=label, multiple=multiple, options=[QuestionOption(code=c, label=c) for c in codes])


def test_questions_digest_ignores_labels_and_option_order():
    a = [_q("road", codes=("yes", "no"), label="Road?")]
    b = [_q("road", codes=("no", "yes"), label="Is there a road?")]
    assert questions_digest(a) == questions_digest(b)
    assert len(questions_digest(a)) == 64 and int(questions_digest(a), 16) >= 0


def test_questions_digest_follows_keys_codes_and_multiple():
    base = questions_digest([_q("road", codes=("yes", "no"))])
    assert questions_digest([_q("road", codes=("yes", "no", "maybe"))]) != base
    assert questions_digest([_q("road", multiple=True, codes=("yes", "no"))]) != base
    assert questions_digest([_q("track", codes=("yes", "no"))]) != base
    assert questions_digest([]) != base


def test_questions_digest_is_the_canonical_json_hash():
    import hashlib

    expected = hashlib.sha256(b'[["road",false,["no","yes"]]]').hexdigest()
    assert questions_digest([_q("road", codes=("yes", "no"))]) == expected
    assert questions_digest(default_questions()) == questions_digest(default_questions())
