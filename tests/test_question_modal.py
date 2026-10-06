"""The add/edit question form (spec D3): its refusals, the key and codes it
makes, and that an edit keeps them."""

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import default_questions
from gui.i18n import t
from gui.widget import question_modal as qm
from gui.widget.enter_key import ENTER_DEFAULT

LOSS = default_questions()[0]


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield


def _draft(**kw):
    d = {"label": "Road access", "multiple": False, "allow_typed": False, "required": False,
         "ask_for": ["Confirmed"], "options": [{"code": None, "label": "Yes", "label_key": None},
                                               {"code": None, "label": "No", "label_key": None}]}
    d.update(kw)
    return d


@pytest.mark.parametrize("draft, key", [
    (_draft(label="  "), "steps.questionnaire.err_label"),
    (_draft(options=[]), "steps.questionnaire.err_options"),
    (_draft(options=[{"code": None, "label": " ", "label_key": None}]), "steps.questionnaire.err_option_blank"),
    (_draft(options=[{"code": None, "label": "Yes", "label_key": None},
                     {"code": None, "label": "yes", "label_key": None}]), "steps.questionnaire.err_option_duplicate"),
    (_draft(options=[{"code": None, "label": "Roads, bridges", "label_key": None}]),
     "steps.questionnaire.err_option_comma"),
    (_draft(ask_for=[]), "steps.questionnaire.err_ask_for"),
], ids=["blank-label", "no-options", "blank-option", "duplicate-option", "comma", "no-verdict"])
def test_question_error_refuses(draft, key):
    assert qm.question_error(draft) == key


def test_typed_answers_alone_need_no_option():
    assert qm.question_error(_draft(options=[], allow_typed=True)) is None


def test_finish_makes_the_key_and_codes_of_a_new_question():
    q = qm.finish_question(_draft(), default_questions(), None)
    assert q.key == "road_access" and q.label == "Road access" and q.label_key is None
    assert [(o.code, o.label) for o in q.options] == [("yes", "Yes"), ("no", "No")]
    assert q.ask_for == ["Confirmed"]


def test_an_edit_keeps_the_key_and_existing_codes_and_a_renamed_built_in_loses_its_key():
    draft = qm.draft_of(LOSS)
    draft["label"] = "Driver"
    draft["options"][2]["label"] = "Selective logging"
    draft["options"].append({"code": None, "label": "Roads", "label_key": None})
    q = qm.finish_question(draft, default_questions(), LOSS)
    assert q.key == "loss_driver" and q.label == "Driver" and q.label_key is None
    assert q.options[2].code == "logging" and q.options[2].label == "Selective logging"
    assert q.options[2].label_key is None
    assert q.options[0].label_key == LOSS.options[0].label_key  # untouched built-in keeps its key
    assert q.options[-1].code == "roads"


def test_draft_of_shows_built_in_labels_in_the_current_language():
    draft = qm.draft_of(LOSS)
    assert draft["label"] == "Loss driver" and draft["options"][2]["label"] == "Logging"


def _render(questions, editing=None):
    open_, saved = solara.reactive(True), []
    _box, rc = reacton.render(qm.QuestionModal(open_=open_, questions=questions, editing=editing,
                                               on_save=lambda q, prev: saved.append((q, prev))),
                              handle_error=False)
    return open_, saved, rc


def _button(rc, label):
    return next(w for w in rc.find(v.Btn).widgets if label in w.children)


def test_adding_a_question_saves_it_and_closes():
    open_, saved, rc = _render(default_questions())
    try:
        assert rc.find(v.Dialog).widget.max_width == "420px"
        rc.find(v.TextField, label=t("steps.questionnaire.field_label")).widget.v_model = "Road access"
        add_opt = _button(rc, t("steps.questionnaire.add_option"))
        add_opt.fire_event("click", None)
        rc.find(v.TextField, label=t("steps.questionnaire.field_option", n=1)).widget.v_model = "Yes"
        add = _button(rc, t("steps.questionnaire.add"))
        assert ENTER_DEFAULT in add.class_ and not add.disabled
        add.fire_event("click", None)
        (q, prev), = saved
        assert q.key == "road_access" and [o.code for o in q.options] == ["yes"] and prev is None
        assert open_.value is False
    finally:
        rc.close()


def test_option_fields_are_outlined_so_floated_labels_do_not_collide():
    """Underlined dense fields stacked with no gap let an option's floated label
    run into the row above it (seen in the browser walk)."""
    _open, _saved, rc = _render(default_questions())
    try:
        _button(rc, t("steps.questionnaire.add_option")).fire_event("click", None)
        _button(rc, t("steps.questionnaire.add_option")).fire_event("click", None)
        fields = [rc.find(v.TextField, label=t("steps.questionnaire.field_option", n=n)).widget for n in (1, 2)]
        assert all(f.outlined for f in fields)
    finally:
        rc.close()


def test_an_invalid_form_shows_why_and_disables_the_primary_button():
    open_, saved, rc = _render(default_questions())
    try:
        rc.find(v.TextField, label=t("steps.questionnaire.field_label")).widget.v_model = "Road access"
        assert t("steps.questionnaire.err_options") in str(rc.find(v.Alert).widgets[0].children)
        assert _button(rc, t("steps.questionnaire.add")).disabled
        assert saved == [] and open_.value is True
    finally:
        rc.close()


def test_editing_a_built_in_saves_in_place_and_hands_over_the_previous_key():
    open_, saved, rc = _render(default_questions(), editing=LOSS)
    try:
        assert t("steps.questionnaire.edit_title") in str(rc.container)
        save = _button(rc, t("common.save"))
        assert not save.disabled and ENTER_DEFAULT in save.class_
        rc.find(v.TextField, label=t("steps.questionnaire.field_label")).widget.v_model = "Driver"
        _button(rc, t("common.save")).fire_event("click", None)
        (q, prev), = saved
        assert prev == "loss_driver" and q.key == "loss_driver" and q.label == "Driver"
        assert open_.value is False
    finally:
        rc.close()


def test_a_new_option_never_takes_a_code_the_draft_or_the_question_had():
    # "Logging" added above the renamed "logging" option: it must not also be "logging"
    draft = qm.draft_of(LOSS)
    draft["options"][2]["label"] = "Selective logging"
    draft["options"].insert(2, {"code": None, "label": "Logging", "label_key": None})
    q = qm.finish_question(draft, default_questions(), LOSS)
    codes = [o.code for o in q.options]
    assert len(set(codes)) == len(codes)
    assert q.options[2].code == "logging_2" and q.options[3].code == "logging"


def test_a_deleted_options_code_is_not_reused():
    draft = qm.draft_of(LOSS)
    del draft["options"][2]  # logging
    draft["options"].append({"code": None, "label": "Logging", "label_key": None})
    q = qm.finish_question(draft, default_questions(), LOSS)
    assert "logging" not in [o.code for o in q.options]
    assert q.options[-1].code == "logging_2"


def _host(order):
    """A caller owning ``open_`` and ``editing`` as reactives; ``order`` names which it sets first."""
    open_, editing = solara.reactive(False), solara.reactive(None)
    questions = default_questions()

    @solara.component
    def Host():
        QuestionModal = qm.QuestionModal
        QuestionModal(open_=open_, questions=questions, editing=editing.value, on_save=lambda q, prev: None)

    def reopen(question):
        open_.set(False)
        if order == "editing first":
            editing.set(question)
            open_.set(True)
        else:
            open_.set(True)
            editing.set(question)

    _box, rc = reacton.render(Host(), handle_error=False)
    return reopen, rc


@pytest.mark.parametrize("order", ["editing first", "open first"])
def test_reopening_for_another_question_shows_that_question(order):
    reopen, rc = _host(order)
    try:
        label = lambda: rc.find(v.TextField, label=t("steps.questionnaire.field_label")).widget.v_model  # noqa: E731
        reopen(LOSS)
        assert label() == "Loss driver"
        other = default_questions()[0].model_copy(update={"key": "other", "label": "Other one", "label_key": None})
        reopen(other)
        assert label() == "Other one"
        reopen(None)
        assert label() == ""
        reopen(LOSS)
        assert label() == "Loss driver"
    finally:
        rc.close()
