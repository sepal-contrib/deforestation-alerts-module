"""The Questionnaire step (spec D3): the list, its actions, the delete
confirmation's count and the placeholder list."""

from datetime import datetime

import geopandas as gpd
import ipyvuetify as v
import reacton
from pysepal.i18n import set_locale
from shapely.geometry import box

from deforestation_alerts.project.models import Project, Question, QuestionOption
from gui.i18n import t, tn
from gui.store.state_manager import AppState
from gui.tile import questionnaire_step as qstep

ROAD = Question(key="road_access", label="Road access", required=True, ask_for=["Confirmed"],
                options=[QuestionOption(code="yes", label="Yes")])


def _state(tmp_path, monkeypatch, questions=None):
    set_locale("en")
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    kw = {} if questions is None else {"questions": questions}
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now(), **kw))
    return s


def test_tags_say_how_a_question_is_asked():
    set_locale("en")
    values = [c["value"] for c in qstep.question_tags(ROAD)]
    assert values == [t("steps.questionnaire.tag_one"), tn("steps.questionnaire.tag_options", 1),
                      t("steps.questionnaire.tag_required"), t("analysis_tile.verdict.true")]
    assert values[1] == "1 option"


def test_answered_count_reads_the_question_column():
    gdf = gpd.GeoDataFrame({"q_road_access": ["yes", "", None]}, geometry=[box(0, 0, 1, 1)] * 3)
    assert qstep.answered_count(gdf, ROAD) == 1
    assert qstep.answered_count(gdf.drop(columns="q_road_access"), ROAD) == 0
    assert qstep.answered_count(None, ROAD) == 0


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def test_moving_and_deleting_rewrite_the_project(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.update_project("p", lambda live: live.questions.append(ROAD))
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        assert "Road access" in _strings(rc.container) and "{road_access}" in _strings(rc.container)
        up = [b for b in rc.find(v.Btn).widgets if "mdi-arrow-up" in _strings(b)]
        up[-1].fire_event("click", None)      # Road access moves above Loss driver
        assert [q.key for q in s.project.value.questions] == ["road_access", "loss_driver"]
        delete = [b for b in rc.find(v.Btn).widgets if "mdi-delete-outline" in _strings(b)]
        delete[0].fire_event("click", None)
        confirm = [b for b in rc.find(v.Btn).widgets if t("common.delete") in b.children]
        confirm[-1].fire_event("click", None)
        assert [q.key for q in s.project.value.questions] == ["loss_driver"]
    finally:
        rc.close()


def test_a_placeholder_chip_copies_in_the_browser_and_tells_the_kernel():
    from gui.widget import copy_chip
    # ipyvue's legacy loader evaluates only the object literal.
    assert copy_chip._TEMPLATE.split("<script>")[1].strip().startswith("module.exports = {")
    assert "navigator.clipboard.writeText" in copy_chip._TEMPLATE
    assert 'execCommand("copy")' in copy_chip._TEMPLATE   # http (no secure context) fallback
    copied = []
    box, rc = reacton.render(copy_chip.CopyChipWidget.element(text="{verdict}", review=True,
                                                              on_copied_count=copied.append))
    try:
        chip = rc.find(copy_chip.CopyChipWidget).widget
        assert chip.text == "{verdict}" and chip.review
        chip.copied_count += 1      # what the browser script does after a successful copy
        assert copied == [1]
    finally:
        rc.close()


def test_every_placeholder_is_offered_as_a_chip(tmp_path, monkeypatch):
    from deforestation_alerts.project.questions import REPORT_PLACEHOLDERS
    from gui.widget.copy_chip import CopyChipWidget
    s = _state(tmp_path, monkeypatch)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        chips = {w.text: w.review for w in rc.find(CopyChipWidget).widgets}
        assert {"{" + n + "}" for n in REPORT_PLACEHOLDERS} <= set(chips)
        assert chips["{verdict}"] and chips["{loss_driver}"] and not chips["{" + REPORT_PLACEHOLDERS[0] + "}"]
    finally:
        rc.close()


def test_the_table_has_move_icons_and_disables_an_action():
    from gui.widget.product_table import ProductTable, action_icon
    assert (action_icon("move_up"), action_icon("move_down")) == ("mdi-arrow-up", "mdi-arrow-down")
    row = {"key": "a", "cells": [{"value": "A"}],
           "actions": [{"kind": "move_up", "disabled": True, "on_click": lambda: None}]}
    _box, rc = reacton.render(ProductTable(title="T", columns=[{"label": "C"}], rows=[row], empty_text="-"),
                              handle_error=False)
    try:
        assert [b.disabled for b in rc.find(v.Btn).widgets if "mdi-arrow-up" in _strings(b)] == [True]
    finally:
        rc.close()


def _delete_dialog_text(s):
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        delete = [b for b in rc.find(v.Btn).widgets if "mdi-delete-outline" in _strings(b)]
        delete[0].fire_event("click", None)
        return " ".join(_strings(rc.container))
    finally:
        rc.close()


def test_the_delete_confirmation_counts_answers_or_says_they_are_kept(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    first = s.project.value.questions[0]
    col = qstep.column(first)
    s.alerts_gdf.set(gpd.GeoDataFrame({col: ["x", "", None]}, geometry=[box(0, 0, 1, 1)] * 3))
    text = _delete_dialog_text(s)
    assert t("steps.questionnaire.delete_message", count=1) in text
    assert "1 alert of" in text and "1 alerts" not in text
    s.alerts_gdf.set(None)       # no alert set loaded: no "0 alerts", only the promise
    text = _delete_dialog_text(s)
    assert t("steps.questionnaire.delete_message_no_set") in text and "0 alert" not in text


def test_the_step_needs_a_project_not_an_aoi(tmp_path, monkeypatch):
    from gui.store.workflow_steps import STEPS, step_index
    assert STEPS[step_index("questionnaire")].lock_reason_key == "workflow.lock_project"
    set_locale("en")
    assert t("workflow.lock_project") == "Open a project first."
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=AppState()), handle_error=False)
    try:
        assert "Open a project first." in _strings(rc.container)
    finally:
        rc.close()


def test_a_hand_edited_manifest_with_clashing_keys_still_renders(tmp_path, monkeypatch):
    """Final review M1: two questions sharing a key, one keyed ``verdict`` and
    one keyed after a report placeholder must not crash the step (reacton
    refuses duplicate element keys); each placeholder is offered once."""
    from deforestation_alerts.project.questions import REPORT_PLACEHOLDERS
    from gui.widget.copy_chip import CopyChipWidget
    clash = [ROAD, ROAD.model_copy(update={"label": "Road again"}),
             Question(key="verdict", label="Verdict?", options=[QuestionOption(code="y", label="Y")]),
             Question(key=REPORT_PLACEHOLDERS[0], label="Admin", options=[QuestionOption(code="y", label="Y")])]
    s = _state(tmp_path, monkeypatch, questions=clash)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        texts = [w.text for w in rc.find(CopyChipWidget).widgets]
        assert len(texts) == len(set(texts))
        assert "{road_access}" in texts and "{verdict}" in texts
    finally:
        rc.close()
