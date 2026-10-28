"""Answers per question (spec D4, D5): drafted in AnalysisState.answers,
loaded from the store for a reviewed alert, written one column per question."""

from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import Project, Question, QuestionOption, default_questions
from deforestation_alerts.scripts import mosaics_helper
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import export
from gui.tile.analysis import review_save
from gui.tile.analysis.state import AnalysisState, capture_alert
from tests._review_helpers import stub_admin_lookups
from tests.test_set_context import _review_alerts, _state, _store

ROAD = Question(key="road_access", label="Road access", required=True,
                options=[QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])
LOSS = default_questions()[0]


pytestmark = pytest.mark.usefixtures("english_locale")


def _set_questions(s, questions):
    s.update_project(s.project.value.name, lambda p: setattr(p, "questions", list(questions)))


def _saved(tmp_path, monkeypatch, questions, status, answers):
    """Press Save on alert 0 of a two-alert set whose project asks
    ``questions``, with ``status`` and ``answers`` chosen, for real (the Earth
    Engine admin lookups stand in). Returns the stored row as a dict; raises
    what the save raised."""
    s = _state(tmp_path, monkeypatch)
    _set_questions(s, questions)
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))
    stub_admin_lookups(monkeypatch)
    widgets = SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None))
    analysis = AnalysisState()
    analysis.verdict.set(status)
    analysis.answers.set(answers)
    s.actual_alert_id.set(0)
    assert export._save_attributes(widgets, s, analysis, ctx) is True
    return pd.read_parquet(_store(tmp_path, "d", "glad-l")).iloc[0].to_dict()


def test_reset_alert_forgets_the_answers():
    a = AnalysisState()
    a.answers.set({"road_access": ["yes"]})
    a.reset_alert()
    assert a.answers.value == {}


def test_capture_alert_takes_a_copy_of_the_answers_and_the_questions(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    _set_questions(s, [LOSS, ROAD])
    analysis = AnalysisState()
    analysis.answers.set({"road_access": ["yes"]})
    alert = capture_alert(analysis, SimpleNamespace(), s)
    analysis.answers.value["road_access"].append("no")  # an edit after the click
    assert alert.answers == {"road_access": ["yes"]}
    assert [q.key for q in alert.questions] == ["loss_driver", "road_access"]
    assert not hasattr(alert, "drivers")


def test_capture_alert_without_a_project_has_no_questions():
    alert = capture_alert(AnalysisState(), SimpleNamespace(), AppState())
    assert alert.answers == {} and alert.questions == []


def test_a_save_writes_one_column_per_question(tmp_path, monkeypatch):
    row = _saved(tmp_path, monkeypatch, [LOSS, ROAD], "Confirmed",
                 {"loss_driver": ["logging", "Roads, bridges"], "road_access": ["no"]})
    assert row["description"] == "logging, Roads; bridges"
    assert row["q_road_access"] == "no"


def test_a_hidden_question_stores_nothing_and_its_required_flag_does_not_block(tmp_path, monkeypatch):
    """Review focus 2: Loss driver is not asked for a false positive."""
    required_loss = LOSS.model_copy(update={"required": True})
    row = _saved(tmp_path, monkeypatch, [required_loss], "False Positive", {"loss_driver": ["logging"]})
    assert row["status"] == "False Positive" and row["description"] == ""


def test_a_hidden_required_question_without_an_answer_does_not_block(tmp_path, monkeypatch):
    """Review focus 2, the plain case: nothing drafted at all."""
    required_loss = LOSS.model_copy(update={"required": True})
    row = _saved(tmp_path, monkeypatch, [required_loss], "False Positive", {})
    assert row["status"] == "False Positive" and row["description"] == ""


def test_a_visible_required_question_without_an_answer_refuses_the_save(tmp_path, monkeypatch):
    with pytest.raises(review_save.SaveRefused) as info:
        _saved(tmp_path, monkeypatch, [LOSS, ROAD], "Confirmed", {"loss_driver": ["other"]})
    assert info.value.key == review_save.REQUIRED_MISSING
    assert info.value.values["question"] == "Road access" and info.value.values["question_key"] == "road_access"
    assert not _store(tmp_path, "d", "glad-l").exists()  # nothing written
    from gui.scripts.error_text import error_text

    assert error_text(info.value) == "Answer “Road access” before saving."  # what the save toasts


def test_a_required_answer_of_blanks_only_is_missing(tmp_path, monkeypatch):
    with pytest.raises(review_save.SaveRefused) as info:
        _saved(tmp_path, monkeypatch, [ROAD], "Confirmed", {"road_access": ["  ", ""]})
    assert info.value.key == review_save.REQUIRED_MISSING


def test_the_refusal_names_the_question_in_the_current_language(tmp_path, monkeypatch):
    set_locale("es-ES")
    required_loss = LOSS.model_copy(update={"required": True})
    with pytest.raises(review_save.SaveRefused) as info:
        _saved(tmp_path, monkeypatch, [required_loss], "Need revision", {})
    from gui.i18n import t

    assert info.value.values["question"] == t("analysis_tile.questionarie.loss_driver_label")
    assert t(review_save.REQUIRED_MISSING, question="X") == "Responda «X» antes de guardar."


def test_a_store_without_the_column_gets_it_on_the_first_save(tmp_path, monkeypatch):
    """Review focus 5: a question added after the set was made."""
    row = _saved(tmp_path, monkeypatch, [ROAD], "Need revision", {"road_access": ["yes"]})
    assert row["q_road_access"] == "yes"
    saved = pd.read_parquet(_store(tmp_path, "d", "glad-l"), columns=["q_road_access", "description"])
    assert saved["q_road_access"].tolist() == ["yes", ""]
    assert saved["description"].isna().all()  # Loss driver is not asked: its column is left as stored


def test_the_live_table_gets_the_new_column_too(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    _set_questions(s, [ROAD])
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x"))
    stub_admin_lookups(monkeypatch)
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    analysis.answers.set({"road_access": ["no"]})
    s.actual_alert_id.set(0)
    assert export._save_attributes(SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None)),
                                   s, analysis, ctx) is True
    assert s.alerts_gdf.value["q_road_access"].tolist() == ["no", ""]


def test_a_store_missing_a_non_answer_column_still_saves(tmp_path, monkeypatch):
    """Only answer columns are padded with "": under pandas' string inference a
    padded ``area_ha`` is a str column, and the drawn area (a float) would not
    fit in it. Other columns are left to ``.at``'s own enlargement, as before."""
    from shapely.geometry import box as _box

    s = _state(tmp_path, monkeypatch)
    _set_questions(s, [ROAD])
    ctx = SetContext(s)
    s.alerts_gdf.set(_review_alerts("x").drop(columns=["area_ha"]))
    stub_admin_lookups(monkeypatch)
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    analysis.answers.set({"road_access": ["yes"]})
    drawn = gpd.GeoDataFrame(geometry=[_box(-55.0, -24.0, -54.999, -23.999)], crs="EPSG:4326")
    analysis.defo_layer.set(drawn)
    s.actual_alert_id.set(0)
    assert export._save_attributes(SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None)),
                                   s, analysis, ctx) is True
    saved = pd.read_parquet(_store(tmp_path, "d", "glad-l"), columns=["area_ha", "q_road_access"])
    assert saved.loc[0, "area_ha"] == pytest.approx(mosaics_helper.calculate_total_area(drawn))
    assert saved["q_road_access"].tolist() == ["yes", ""]
    assert s.alerts_gdf.value.loc[0, "area_ha"] == pytest.approx(mosaics_helper.calculate_total_area(drawn))


def test_drafts_of_deleted_questions_are_ignored(tmp_path, monkeypatch):
    """Review focus 4."""
    row = _saved(tmp_path, monkeypatch, [LOSS], "Confirmed", {"loss_driver": ["mining"], "gone": ["x"]})
    assert "q_gone" not in row and row["description"] == "mining"


def test_a_project_without_questions_saves_the_verdict_alone(tmp_path, monkeypatch):
    row = _saved(tmp_path, monkeypatch, [], "Confirmed", {"loss_driver": ["mining"]})
    assert row["status"] == "Confirmed" and row["description"] is None


# ---------------------------------------------------------------------------
# view_actual_alert: a reviewed alert opens on its stored answers
# ---------------------------------------------------------------------------
def _viewed(tmp_path, monkeypatch, alert_id, *, with_road_column=True):
    """``view_actual_alert`` on alert ``alert_id`` of two (0 reviewed, 1 not),
    in a project asking Loss driver and Road access. Returns the AnalysisState."""
    from gui.tile.analysis import analysis_tile

    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now(), questions=[LOSS, ROAD])
    app_state.new_project_state(p)
    columns = {"status": ["Confirmed", "Not reviewed"], "alert_id": [0, 1], "set_id": ["x", "x"],
               "alert_sources": ["GLAD-L"] * 2, "alert_date_min": [2026.010] * 2,
               "alert_date_max": [2026.020] * 2, "alert_polygon": [None, None],
               "description": ["logging, Roads", "mining"], "bounding_box": [box(0, 0, 1, 1)] * 2}
    if with_road_column:
        columns["q_road_access"] = ["yes", "no"]
    app_state.alerts_gdf.value = gpd.GeoDataFrame(
        columns, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326")
    app_state.actual_alert_id.value = alert_id
    analysis = AnalysisState()
    analysis.answers.set({"road_access": ["the previous alert's"]})
    analysis_tile.view_actual_alert(SimpleNamespace(), app_state, analysis, SetContext(app_state),
                                    SimpleNamespace(v_model=None))
    return analysis


def test_a_reviewed_alert_opens_on_its_stored_answers(tmp_path, monkeypatch):
    analysis = _viewed(tmp_path, monkeypatch, 0)
    assert analysis.answers.value == {"loss_driver": ["logging", "Roads"], "road_access": ["yes"]}
    assert analysis.dirty.value is False  # loading is not a change


def test_an_unreviewed_alert_opens_on_no_answers(tmp_path, monkeypatch):
    """Spec U13: no default (not even Loss driver's "other"), so "required" means something."""
    analysis = _viewed(tmp_path, monkeypatch, 1)
    assert analysis.answers.value == {}


def test_a_store_without_a_questions_column_loads_no_answer_for_it(tmp_path, monkeypatch):
    analysis = _viewed(tmp_path, monkeypatch, 0, with_road_column=False)
    assert analysis.answers.value == {"loss_driver": ["logging", "Roads"], "road_access": []}
