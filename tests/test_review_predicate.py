"""The one "reviewed" rule (spec §3, ``project/review_status.py``) and the
model fields of the evaluator split and the review copy."""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    EvaluatorReturn,
    EvaluatorSplit,
    Project,
    ReviewCopy,
)
from deforestation_alerts.project.review_status import NOT_REVIEWED, is_reviewed, reviewed_mask

NOW = datetime(2026, 10, 10, 12, 0)


@pytest.mark.parametrize("status, expected", [
    ("Confirmed", True), ("False Positive", True), ("Need revision", True),
    ("Not reviewed", False), ("", False), (None, False), (np.nan, False), (float("nan"), False), (pd.NA, False),
])
def test_is_reviewed(status, expected):
    assert is_reviewed(status) is expected


def test_not_reviewed_is_the_status_integrate_writes():
    assert NOT_REVIEWED == "Not reviewed"


@pytest.mark.parametrize("dtype", [object, "str"])
def test_reviewed_mask_is_index_aligned(dtype):
    df = pd.DataFrame({"status": pd.Series(["Confirmed", "", None, "Not reviewed", "Need revision"], dtype=dtype,
                                           index=[10, 11, 12, 13, 14])})
    mask = reviewed_mask(df)
    assert mask.dtype == bool and list(mask.index) == [10, 11, 12, 13, 14]
    assert mask.tolist() == [True, False, False, False, True]


def test_reviewed_mask_without_a_status_column_or_rows():
    bare = pd.DataFrame({"alert_id": [0, 1]}, index=[5, 6])
    mask = reviewed_mask(bare)
    assert mask.tolist() == [False, False] and list(mask.index) == [5, 6] and mask.dtype == bool
    assert reviewed_mask(pd.DataFrame({"status": []})).tolist() == []
    assert reviewed_mask(None).tolist() == []


def _set(**kw):
    return AlertSet(id="x", period_id="p", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
                    store_rel_path="s/alert_db.parquet", combined_rel_path="s/c.tif",
                    footprint_rel_path="s/footprint.tif", fingerprint="f", created_at=NOW, **kw)


def test_a_set_and_a_project_default_to_no_split_and_no_review_copy():
    p = Project(name="d", created_at=NOW, updated_at=NOW)
    assert p.review_copy is None and _set().split is None
    split = EvaluatorSplit(split_id="a" * 32, evaluators=["Ana"], created_at=NOW)
    assert split.include_reviewed is False and split.packaged is False
    assert split.questions_digest == "" and split.returns == {}
    assert EvaluatorSplit(split_id="b", evaluators=["B"], created_at=NOW).returns is not split.returns


def test_split_and_review_copy_round_trip_through_the_manifest_json():
    ret = EvaluatorReturn(merged_at=NOW, taken=3, unchanged=1, conflicts=2, choice="theirs", ignored=0)
    split = EvaluatorSplit(split_id="a" * 32, evaluators=["Ana", "Bo"], include_reviewed=True, created_at=NOW,
                           packaged=True, questions_digest="d" * 64, returns={"Ana": ret})
    p = Project(name="d", created_at=NOW, updated_at=NOW)
    p.alert_sets = {"x": _set(split=split)}
    p.review_copy = ReviewCopy(evaluator="Bo", evaluator_index=2, set_id="x", split_id="a" * 32,
                               set_fingerprint="f", origin_project="creator", imported_at=NOW)
    back = Project.model_validate_json(p.model_dump_json())
    assert back == p
    assert back.alert_sets["x"].split.returns["Ana"].choice == "theirs"
    assert back.review_copy.evaluator_index == 2


def test_a_return_without_conflicts_has_no_choice():
    assert EvaluatorReturn(merged_at=NOW, taken=1, unchanged=0, conflicts=0, ignored=4).choice is None
    with pytest.raises(ValueError):
        EvaluatorReturn(merged_at=NOW, taken=1, unchanged=0, conflicts=1, choice="both", ignored=0)


def test_a_manifest_saved_before_the_split_loads_unchanged():
    p = Project(name="d", created_at=NOW, updated_at=NOW)
    p.alert_sets = {"x": _set()}
    raw = p.model_dump(mode="json")
    del raw["review_copy"]
    del raw["alert_sets"]["x"]["split"]
    back = Project.model_validate(raw)
    assert back.review_copy is None and back.alert_sets["x"].split is None
    assert back.schema_version == 1


def test_a_split_is_not_part_of_the_sets_fingerprint_inputs():
    """``fingerprint.set_inputs`` hashes ``AlertSetParams``: a split never makes a set STALE."""
    assert "split" not in AlertSetParams.model_fields


def test_evaluator_is_reserved_for_questions_and_attribute_layers():
    from deforestation_alerts.project.questions import RESERVED_KEYS, new_key
    from deforestation_alerts.scripts import raster_attribute as ra

    assert "evaluator" in RESERVED_KEYS and "evaluator" in ra.ALERT_RESERVED_COLUMNS
    assert new_key("Evaluator", []) == "evaluator_2"
    cfg = ra.build_attribute_config("/r.tif", "local",
                                    [{"include": True, "band": "evaluator", "stat": "mean", "column": "evaluator"}])
    assert [b["column"] for b in cfg["bands"]] == ["evaluator_2"]


def test_the_review_vocabulary_agrees_with_itself():
    from deforestation_alerts.project import review_status as rs

    assert set(rs.STATUS_CODES) == {rs.NOT_REVIEWED, *rs.VERDICTS}
    assert rs.STATUS_CODES[rs.NOT_REVIEWED] == 0
    assert set(rs.VERDICT_LABEL_KEYS) == set(rs.VERDICTS)
    assert set(rs.LOSS_VERDICTS) < set(rs.VERDICTS)
    assert all(rs.is_reviewed(v) for v in rs.VERDICTS)


def test_no_question_key_or_attribute_column_takes_a_store_column():
    from deforestation_alerts.project.questions import RESERVED_KEYS
    from deforestation_alerts.project.store_schema import RESERVED_COLUMNS
    from deforestation_alerts.scripts.raster_attribute import ALERT_RESERVED_COLUMNS

    assert {"alert_id", "set_id", "evaluator"} <= RESERVED_COLUMNS
    assert RESERVED_COLUMNS <= RESERVED_KEYS
    assert RESERVED_COLUMNS <= ALERT_RESERVED_COLUMNS
