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


def _state_with_template(tmp_path, monkeypatch, path):
    set_locale("en")
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now(),
                                report_template=str(path)))
    return s


def _template_toggle(rc):
    (btn,) = [b for b in rc.find(v.Btn).widgets if "defo-template-toggle" in (b.class_ or "")]
    return btn


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
        _template_toggle(rc).fire_event("click", None)  # the placeholders sit in the expanded template card
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
        _template_toggle(rc).fire_event("click", None)  # the placeholders sit in the expanded template card
        texts = [w.text for w in rc.find(CopyChipWidget).widgets]
        assert len(texts) == len(set(texts))
        assert "{road_access}" in texts and "{verdict}" in texts
    finally:
        rc.close()


def test_the_step_ends_with_start_analysis_gated_on_an_active_set(tmp_path, monkeypatch):
    """*Start analysis* is the step's last control. It writes the navigation
    signal (``_NavBridge`` opens the screen); with no active set it is
    disabled, with the reason under it."""
    import solara
    s = _state(tmp_path, monkeypatch)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        (start,) = [w for w in rc.find(v.Btn).widgets if "defo-start-analysis" in (w.class_ or "")]
        assert start.disabled and t("workflow.lock_analysis") in _strings(rc.container)
    finally:
        rc.close()

    monkeypatch.setattr(qstep, "active_set", lambda p: object())
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        btns = rc.find(v.Btn).widgets
        (start,) = [w for w in btns if "defo-start-analysis" in (w.class_ or "")]
        assert not start.disabled and t("workflow.lock_analysis") not in _strings(rc.container)
        start.fire_event("click", None)
        assert s.current_page_view.value == "analysis_tile"
    finally:
        rc.close()


def test_a_switch_says_whether_analysis_shows_the_questions_expanded(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    assert s.project.value.questions_expanded is False
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        (switch,) = [w for w in rc.find(v.Switch).widgets if w.label == t("steps.questionnaire.expanded")]
        assert switch.v_model is False
        switch.v_model = True
        assert s.project.value.questions_expanded is True
        (switch,) = [w for w in rc.find(v.Switch).widgets if w.label == t("steps.questionnaire.expanded")]
        assert switch.v_model is True
    finally:
        rc.close()


def test_the_report_template_is_one_line_until_expanded(tmp_path, monkeypatch):
    from pysepal.sepalwidgets.file_input import FileInput
    from gui.widget.copy_chip import CopyChipWidget
    s = _state(tmp_path, monkeypatch)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        texts = _strings(rc.container)
        assert t("steps.questionnaire.template_default") in texts
        assert t("steps.questionnaire.template_chip_default") in texts
        assert not rc.find(FileInput).widgets and not rc.find(CopyChipWidget).widgets
        _template_toggle(rc).fire_event("click", None)
        assert rc.find(FileInput).widgets and rc.find(CopyChipWidget).widgets
        _template_toggle(rc).fire_event("click", None)
        assert not rc.find(FileInput).widgets
    finally:
        rc.close()


def test_a_custom_template_is_named_by_its_file(tmp_path, monkeypatch):
    from pysepal.sepalwidgets.file_input import FileInput
    mine = tmp_path / "field_report.docx"
    mine.write_bytes(b"x")
    s = _state_with_template(tmp_path, monkeypatch, mine)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        texts = _strings(rc.container)
        assert "field_report.docx" in texts and t("steps.questionnaire.template_chip_custom") in texts
        assert t("steps.questionnaire.template_missing") not in texts
        assert not rc.find(FileInput).widgets
        assert not _template_toggle(rc).disabled
    finally:
        rc.close()


def test_a_missing_template_holds_the_card_open(tmp_path, monkeypatch):
    from pysepal.sepalwidgets.file_input import FileInput
    s = _state_with_template(tmp_path, monkeypatch, tmp_path / "gone.docx")
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        texts = _strings(rc.container)
        assert t("steps.questionnaire.template_chip_missing") in texts
        assert t("steps.questionnaire.template_missing") in texts
        assert rc.find(FileInput).widgets  # open: pick it again or clear it
        assert _template_toggle(rc).disabled
    finally:
        rc.close()


def _styles(w):
    """Every ``style_`` / ``style`` string in a widget tree."""
    out = [x for x in (getattr(w, "style_", None), getattr(w, "style", None)) if isinstance(x, str)]
    for c in getattr(w, "children", None) or []:
        if not isinstance(c, str):
            out.extend(_styles(c))
    return out


def test_a_review_copys_template_card_expands_to_the_read_only_picker(tmp_path, monkeypatch):
    """Spec 2026-10-14 §5: a review copy keeps the creator's template, so the
    picker under the caret is dimmed and out of reach of the pointer."""
    from pysepal.sepalwidgets.file_input import FileInput
    from deforestation_alerts.project.models import ReviewCopy
    from deforestation_alerts.project.review_copy import read_only
    mine = tmp_path / "creators.docx"
    mine.write_bytes(b"x")
    set_locale("en")
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(
        name="p", created_at=datetime.now(), updated_at=datetime.now(), report_template=str(mine),
        review_copy=ReviewCopy(evaluator="Ana", evaluator_index=1, set_id="x", split_id="sp",
                               set_fingerprint="f", origin_project="orig", imported_at=datetime.now())))
    assert read_only(s.project.value)
    _box, rc = reacton.render(qstep.QuestionnaireStep(app_state=s), handle_error=False)
    try:
        texts = _strings(rc.container)
        assert "creators.docx" in texts and t("steps.questionnaire.template_chip_custom") in texts
        assert not rc.find(FileInput).widgets
        _template_toggle(rc).fire_event("click", None)
        assert rc.find(FileInput).widgets
        assert any("pointer-events:none" in st for st in _styles(rc.container))  # dimmed, out of reach
    finally:
        rc.close()
