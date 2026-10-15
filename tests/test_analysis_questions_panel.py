"""The Questions section (spec D4): collapsed by default, one control per
question, hidden by the verdict, a badge that counts answers, and a rebuild
that keeps drafts when the project's questions change."""

from datetime import datetime

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import Project, Question, QuestionOption, default_questions
from gui.i18n import t
from gui.scripts import questions as qs
from gui.store.state_manager import AppState
from gui.tile.analysis import questions_panel as qp
from gui.tile.analysis.state import AnalysisState
from gui.widget.text_style import MUTED

ROAD = Question(key="road_access", label="Road access", required=True, ask_for=["Confirmed"],
                options=[QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])
FIRE = Question(key="fire", label="Fire scars", multiple=True,
                options=[QuestionOption(code="a", label="A"), QuestionOption(code="b", label="B")])


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    set_locale("en")
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now(),
                                questions=[*default_questions(), ROAD, FIRE]))
    a = AnalysisState()
    return s, a, qp.build_questions_section(s, a)


def test_collapsed_with_one_control_per_question(ctx):
    _s, _a, sec = ctx
    assert not sec.is_open()
    assert isinstance(sec.controls["loss_driver"], v.Combobox)
    assert isinstance(sec.controls["road_access"], v.RadioGroup)
    assert isinstance(sec.controls["fire"], v.ChipGroup) and sec.controls["fire"].multiple


def test_a_control_writes_the_answers_and_marks_the_review_unsaved(ctx):
    _s, a, sec = ctx
    sec.controls["road_access"].v_model = "no"
    sec.controls["road_access"].fire_event("change", "no")
    assert a.answers.value["road_access"] == ["no"] and a.dirty.value


def test_the_verdict_hides_questions_not_asked_for_it(ctx):
    _s, a, sec = ctx
    a.verdict.set("False Positive")
    assert "display: none" in sec.wrappers["loss_driver"].style_
    assert "display: none" in sec.wrappers["road_access"].style_
    assert "display: none" not in (sec.wrappers["fire"].style_ or "")


def test_badge_counts_visible_answers_and_flags_a_missing_required_one(ctx):
    qs_ = [*default_questions(), ROAD, FIRE]
    text, color = qp.badge_text(qs_, {"fire": ["a"]}, "Confirmed")
    assert text == t("analysis_tile.questions.badge_missing", answered=1, total=3) and color == "warning"
    text, color = qp.badge_text(qs_, {"fire": ["a"], "road_access": ["yes"]}, "Confirmed")
    assert text == t("analysis_tile.questions.badge", answered=2, total=3) and color is None
    assert qp.badge_text([], {}, None) == (t("analysis_tile.questions.badge_none"), None)


def test_a_new_alert_shows_its_answers_in_the_controls(ctx):
    _s, a, sec = ctx
    a.answers.set({"road_access": ["yes"], "fire": ["a", "b"], "loss_driver": ["logging", "Roads"]})
    assert sec.controls["road_access"].v_model == "yes"
    assert sec.controls["fire"].v_model == ["a", "b"]
    assert sec.controls["loss_driver"].v_model == [{"text": "Logging", "value": "logging"}, "Roads"]


def test_editing_the_questions_rebuilds_and_keeps_drafts(ctx):
    """Review focus 4."""
    s, a, sec = ctx
    a.answers.set({"road_access": ["yes"], "fire": ["a"]})
    s.update_project("p", lambda live: setattr(live, "questions", [ROAD]))
    assert set(sec.controls) == {"road_access"}
    assert sec.controls["road_access"].v_model == "yes"
    assert a.answers.value["road_access"] == ["yes"]


# ---------------------------------------------------------------------------
# Beyond the brief: the section's own guarantees, and what the retired
# verdict-card combobox covered (tests/test_questions.py, test_analysis_verdict.py)
# ---------------------------------------------------------------------------
def test_switching_the_verdict_back_restores_the_question_and_its_answer(ctx):
    """Spec D4: a hidden question keeps its draft; switching back shows it."""
    _s, a, sec = ctx
    a.answers.set({"loss_driver": ["logging"]})
    a.verdict.set("False Positive")
    assert "display: none" in sec.wrappers["loss_driver"].style_
    a.verdict.set("Confirmed")
    assert "display: none" not in sec.wrappers["loss_driver"].style_
    assert sec.controls["loss_driver"].v_model == [{"text": "Logging", "value": "logging"}]


def test_a_verdict_that_asks_nothing_says_so(tmp_path, monkeypatch):
    """The default questionnaire asks nothing of a false positive: the body
    says why it is empty and the badge reads "no questions", not "0 of 0"."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now()))
    a = AnalysisState()
    sec = qp.build_questions_section(s, a)
    *_, note = sec.body_box.children
    assert "display: none" in note.style_
    assert sec.badge.children == [t("analysis_tile.questions.badge", answered=0, total=1)]

    a.verdict.set("False Positive")
    assert note.children == ["No questions are asked for this verdict."]
    assert "display: none" not in note.style_ and note.style_ == MUTED
    assert sec.badge.children == [t("analysis_tile.questions.badge_none")]
    assert qp.badge_text(s.project.value.questions, {}, "False Positive") == (
        t("analysis_tile.questions.badge_none"), None)

    a.verdict.set("Confirmed")
    assert "display: none" in note.style_


def test_a_rebuild_closes_the_old_controls_and_their_handlers_never_write(ctx):
    s, a, sec = ctx
    old_road, old_fire = sec.controls["road_access"], sec.controls["fire"]
    old_wrapper = sec.wrappers["fire"]
    s.update_project("p", lambda live: setattr(live, "questions", [ROAD]))
    assert old_road.comm is None and old_fire.comm is None and old_wrapper.comm is None
    assert all(chip.comm is None for chip in old_fire.children)
    assert sec.controls["road_access"].comm is not None
    old_road.fire_event("change", "no")
    old_fire.fire_event("change", ["a"])
    assert a.answers.value == {} and a.dirty.value is False
    sec.controls["road_access"].fire_event("change", "no")
    assert a.answers.value == {"road_access": ["no"]} and a.dirty.value is True


def test_the_badge_and_the_live_header_follow_the_answers(ctx):
    _s, a, sec = ctx
    # No verdict yet: every question shows, none answered, Road access required.
    assert sec.badge.children == [t("analysis_tile.questions.badge_missing", answered=0, total=3)]
    assert sec.badge.color == "warning"
    a.answers.set({"road_access": ["yes"]})
    assert sec.badge.children == [t("analysis_tile.questions.badge", answered=1, total=3)]
    assert not sec.badge.color
    a.verdict.set("False Positive")  # only Fire scars is asked
    assert sec.badge.children == [t("analysis_tile.questions.badge", answered=0, total=1)]


def test_answers_set_from_outside_do_not_mark_the_review_unsaved(ctx):
    """An alert load writes the answers and then clears ``dirty``; showing
    them in the controls must not loop back into an answers write."""
    _s, a, sec = ctx
    a.answers.set({"road_access": ["yes"], "fire": ["b"]})
    assert a.dirty.value is False
    # A change event echoing what the answers already hold is not an edit.
    sec.controls["road_access"].fire_event("change", "yes")
    sec.controls["fire"].fire_event("change", ["b"])
    assert a.dirty.value is False


def test_the_loss_driver_combobox_writes_codes_and_marks_the_alert_dirty(ctx):
    _s, a, sec = ctx
    combobox = sec.controls["loss_driver"]
    assert combobox.return_object  # Vuetify 2 draws a chip from the value itself
    assert combobox.v_model == []  # spec U13: no default driver
    combobox.fire_event("change", [{"text": "Mining", "value": "mining"}, "Dams, bridges"])
    assert a.answers.value["loss_driver"] == ["mining", "Dams; bridges"]
    assert a.dirty.value is True
    assert combobox.v_model == [{"text": "Mining", "value": "mining"}, "Dams; bridges"]


def test_a_chip_group_and_a_cleared_radio_write_their_answers(ctx):
    _s, a, sec = ctx
    sec.controls["fire"].fire_event("change", ["a", "b"])
    assert a.answers.value["fire"] == ["a", "b"]
    sec.controls["road_access"].fire_event("change", "yes")
    sec.controls["road_access"].fire_event("change", None)
    assert a.answers.value["road_access"] == []


def test_a_reset_alert_empties_the_controls(ctx):
    _s, a, sec = ctx
    a.answers.set({"road_access": ["yes"], "fire": ["a"], "loss_driver": ["logging"]})
    a.reset_alert()
    assert sec.controls["road_access"].v_model is None
    assert sec.controls["fire"].v_model == []
    assert sec.controls["loss_driver"].v_model == []


def test_required_questions_carry_a_required_tag(ctx):
    _s, _a, sec = ctx
    number, label, tag, _mark = sec.wrappers["road_access"].children[0].children
    assert label.children == ["Road access"]
    assert isinstance(tag, v.Chip) and tag.children == ["Required"]
    assert not any(isinstance(w, v.Chip) for w in sec.wrappers["fire"].children[0].children)


def test_questions_are_numbered_among_the_asked_and_checked_once_answered(ctx):
    _s, a, sec = ctx
    a.verdict.set("Confirmed")
    shown = [k for k, w in sec.wrappers.items() if "display: none" not in (w.style_ or "")]
    numbers = [sec.wrappers[k].children[0].children[0].children for k in shown]
    assert numbers == [[str(i)] for i in range(1, len(shown) + 1)]
    mark = sec.wrappers["road_access"].children[0].children[-1]
    assert "display: none" in mark.style_
    a.answers.set({"road_access": ["yes"]})
    assert "display: none" not in (mark.style_ or "")


def test_a_picked_verdict_with_a_required_question_unanswered_opens_the_section(ctx):
    from gui.tile.analysis.state import touch

    _s, a, sec = ctx
    assert not sec.is_open()
    a.verdict.set("Confirmed")
    touch(a)  # what the verdict buttons do
    assert sec.is_open()
    sec.set_open(False)
    a.answers.set({"loss_driver": ["logging"]})
    touch(a)  # another edit under the same verdict does not reopen it
    assert not sec.is_open()


def test_an_auto_save_publish_of_the_same_questions_does_not_rebuild(ctx):
    s, _a, sec = ctx
    before = dict(sec.controls)
    s.update_project("p", lambda live: setattr(live, "name", "p"))
    assert sec.controls == before
    assert all(sec.controls[k] is before[k] for k in before)


def test_a_project_without_questions_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now(),
                                questions=[]))
    sec = qp.build_questions_section(s, AnalysisState())
    assert sec.controls == {}
    (note,) = sec.body_box.children
    assert note.children == [t("analysis_tile.questions.none")]
    assert sec.badge.children == [t("analysis_tile.questions.badge_none")]


def test_no_project_builds_an_empty_section():
    sec = qp.build_questions_section(AppState(), AnalysisState())
    assert sec.controls == {} and not sec.is_open()


@pytest.mark.parametrize("code", ["es-ES", "fr", "pt-BR"])
def test_the_options_read_in_this_language_and_store_codes(tmp_path, monkeypatch, code):
    """A review made in another language stores codes (final review B, M2)."""
    set_locale(code)
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now()))
    a = AnalysisState()
    sec = qp.build_questions_section(s, a)
    loss = default_questions()[0]
    combobox = sec.controls["loss_driver"]
    assert [i["value"] for i in combobox.items] == [o.code for o in loss.options]
    assert [i["text"] for i in combobox.items] == [qs.option_label(o) for o in loss.options]
    other = next(o for o in loss.options if o.code == "other")
    assert qs.option_label(other) != "Other"  # really translated
    a.answers.set({"loss_driver": ["other"]})
    assert combobox.v_model == [{"text": qs.option_label(other), "value": "other"}]
    combobox.fire_event("change", combobox.v_model)
    assert a.answers.value["loss_driver"] == ["other"]
    label = sec.wrappers["loss_driver"].children[0].children[1]
    assert label.children[0] == qs.question_label(loss) != "Loss driver"


@pytest.mark.parametrize("code, chips", [
    ("en", ["Logging", "Roads"]),
    ("es-ES", ["Tala", "Roads"]),
    ("fr", ["Exploitation forestière", "Roads"]),
    ("pt-BR", ["Extração de madeira", "Roads"]),
])
def test_a_stored_review_loads_back_as_chips_in_this_language(tmp_path, monkeypatch, code, chips):
    """The chips show labels, never codes: a reviewed alert's drivers come back
    as items, typed text as typed, and a re-save stores the same codes."""
    from tests.test_analysis_alert_info import _alerts, _state, _wire

    set_locale(code)
    s = _state(tmp_path, monkeypatch)
    analysis = AnalysisState()
    _wire(s, analysis)
    sec = qp.build_questions_section(s, analysis)
    gdf = _alerts(2)
    gdf["status"] = ["Confirmed", "Not reviewed"]  # only a reviewed alert opens on its answers
    gdf["description"] = ["logging, Roads", None]
    s.alerts_gdf.set(gdf)  # alert 0 is selected and drawn

    selected = sec.controls["loss_driver"].v_model
    assert [c["text"] if isinstance(c, dict) else c for c in selected] == chips
    loss = default_questions()[0]
    assert qs.encode(loss, selected) == "logging, Roads"  # a re-save keeps the codes
    typed = selected + ["Dams"]  # the user types free text next to the chips
    assert qs.encode(loss, typed) == "logging, Roads, Dams"
    assert analysis.dirty.value is False  # loading is not an edit


def test_an_unreviewed_alert_opens_empty_not_on_the_last_ones_answers(tmp_path, monkeypatch):
    """Moving from a reviewed alert to an unreviewed one must not carry the
    first alert's answers over (a Save there would store them as its own), nor
    start on a default (spec U13: "required" has to mean something)."""
    from tests.test_analysis_alert_info import _alerts, _state, _wire

    s = _state(tmp_path, monkeypatch)
    analysis = AnalysisState()
    _wire(s, analysis)
    sec = qp.build_questions_section(s, analysis)
    gdf = _alerts(2)
    gdf["status"] = ["Confirmed", "Not reviewed"]
    gdf["description"] = ["logging, Roads", None]
    s.alerts_gdf.set(gdf)  # alert 0, reviewed
    assert sec.controls["loss_driver"].v_model == [{"text": "Logging", "value": "logging"}, "Roads"]
    s.actual_alert_id.value = 1
    assert sec.controls["loss_driver"].v_model == []  # U13
    assert analysis.answers.value == {}


def test_the_questionnaire_setting_opens_or_closes_the_section(tmp_path, monkeypatch):
    """``questions_expanded`` sets the section's state when it changes (a
    project opening included); a later publish leaves the reviewer's choice."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    s.new_project_state(Project(name="p", created_at=datetime.now(), updated_at=datetime.now(),
                                questions_expanded=True))
    sec = qp.build_questions_section(s, AnalysisState())
    assert sec.is_open()
    sec.set_open(False)  # the reviewer closes it
    s.update_project("p", lambda live: setattr(live, "questions", [ROAD]))
    assert not sec.is_open()
    s.update_project("p", lambda live: setattr(live, "questions_expanded", False))
    assert not sec.is_open()
    s.update_project("p", lambda live: setattr(live, "questions_expanded", True))
    assert sec.is_open()
