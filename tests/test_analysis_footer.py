"""The dock (user, 2026-10-26, option B): the Export section, then Save and
Next, then the save-state line, fixed to the panel's bottom edge; Next (and
every other alert change started on the screen) asks Discard / Stay over
unsaved work; a required refusal opens the Questions section.

The Save button and the save-state line moved here from the verdict card
(block B), so their tests moved with them from tests/test_analysis_verdict.py.
"""

from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v
import pandas as pd
import pytest

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis import export as export_mod
from gui.tile.analysis import review_save
from gui.tile.analysis.footer import (
    FOOTER_STYLE,
    build_footer,
    footer_widgets,
    next_unreviewed_widget,
    prompt_widgets,
)
from gui.tile.analysis.state import AnalysisState
from gui.widget.buttons import label_of
from tests._pane_helpers import make_panes
from tests.conftest import _alerts_gdf


pytestmark = pytest.mark.usefixtures("english_locale")


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    """``build_controls`` as the tile builds it, on stand-in models (the
    setup of tests/test_analysis_sections.py's ``_controls``)."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(
        Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    analysis = AnalysisState()
    model = SimpleNamespace(recipe_folder_path=str(tmp_path), custom_report_template=None,
                            alert_set=None, remember_position=lambda *_: None)
    id_field = v.TextField(v_model=None)
    controls = analysis_tile.build_controls(
        widgets=SimpleNamespace(panes=make_panes()),
        app_state=app_state, analysis=analysis,
        aux_model=model, app_tile_model=model, id_field=id_field)
    return SimpleNamespace(controls=controls, app_state=app_state, analysis=analysis, id_field=id_field)


def _texts(root, out=None):
    """Every string of ``root``'s tree, labels included."""
    out = [] if out is None else out
    for attr in ("msg", "label"):
        value = getattr(root, attr, None)
        if isinstance(value, str):
            out.append(value)
    for child in getattr(root, "children", None) or []:
        if isinstance(child, str):
            out.append(child)
        else:
            _texts(child, out)
    return out


def _three_alerts(app_state):
    """Three alerts on screen, the first one current, so a move is a real
    change of ``actual_alert_id`` and not a clamp."""
    one = _alerts_gdf()
    app_state.alerts_gdf.value = pd.concat([one, one, one], ignore_index=True)
    app_state.actual_alert_id.value = 0


def _prompt_shown(footer_root):
    """Whether the unsaved-work prompt is up, and the button row given way."""
    prompt, _discard, _stay = prompt_widgets(footer_root)
    buttons = footer_root.children[1]
    shown = "display: none" not in prompt.style_
    # The row by class: Vuetify's ``.d-flex`` is ``!important`` and would
    # beat an inline ``display: none``.
    assert shown == (buttons.class_ == "d-none"), "the prompt and the buttons share one place"
    return shown


# ---------------------------------------------------------------------------
# The dock in the assembled panel
# ---------------------------------------------------------------------------
def test_the_dock_is_export_then_save_and_next_then_the_state_line(panel):
    sections = analysis_tile.panel_sections(panel.controls)
    footer = sections.footer.root
    # Not one of the scrolling controls: ``build_screen`` docks it under them.
    assert footer not in panel.controls.children
    # Fixed by the layout (a flex sibling of the scrolling part), not sticky:
    # no sticky offset, no negative margin to cancel the panel's padding.
    assert "sticky" not in footer.style_ and "margin-bottom" not in footer.style_
    assert footer.style_ == FOOTER_STYLE
    save, nxt, state_line = footer_widgets(footer)
    prompt, _discard, _stay = prompt_widgets(footer)
    # The literal English (the fixture pins the locale), not ``t()`` of the
    # same key, which passes on the ``⟦key⟧`` placeholder too.
    assert label_of(save) == "Save" and label_of(nxt) == "Next"
    # The order inside the dock: Export, the buttons, the (hidden)
    # prompt, the state line; then the group save's confirmation, a dialog
    # that draws nothing in place.
    assert footer.children == [sections.export.root, footer.children[1], prompt, state_line,
                               sections.footer.confirm.dialog]
    assert footer.children[1].children == [save, nxt, next_unreviewed_widget(footer)]
    assert "display: none" in prompt.style_
    # The background is an opaque surface: the panel content scrolls above it.
    assert "background-color" in footer.style_


def test_the_docks_export_chevron_points_up_while_closed(panel):
    """The dock's list opens upward, so its closed chevron points there; the
    sections above keep their right-pointing one."""
    sections = analysis_tile.panel_sections(panel.controls)
    export_chevron = sections.export.header.children[0]
    assert not sections.export.is_open()
    assert export_chevron.children == ["mdi-chevron-up"]
    sections.export.header.fire_event("click", None)
    assert export_chevron.children == ["mdi-chevron-down"]
    sections.export.header.fire_event("click", None)
    assert export_chevron.children == ["mdi-chevron-up"]
    assert sections.refine.header.children[0].children == ["mdi-chevron-right"]


def test_the_panel_order_is_head_verdict_questions_refine_earlier(panel):
    from gui.tile.analysis import context, review_panel

    sections = analysis_tile.panel_sections(panel.controls)
    _banner, block_a, block_b, questions, refine, earlier, nearby = panel.controls.children
    context.facts_widgets(block_a)              # block A owns the facts...
    assert set(review_panel.verdict_widgets(block_b)) == {
        "Confirmed", "False Positive", "Need revision"}  # ...block B the verdict
    assert [questions, refine, earlier, nearby] == [sections.questions.root, sections.refine.root,
                                                    sections.earlier.root, sections.nearby.root]


def test_the_verdict_card_no_longer_holds_the_save(panel):
    """The Save and Next buttons and the state line left block B for the dock."""
    _banner, _block_a, block_b, *_ = panel.controls.children
    texts = _texts(block_b)
    assert "Save" not in texts and "Next" not in texts


def test_the_export_body_has_no_template_field_and_one_list(panel):
    sections = analysis_tile.panel_sections(panel.controls)
    parts = sections.export_parts
    assert [tile.kind for tile in parts.tiles] == ["zip", "docx", "gif"]
    # The report template lives in the Questionnaire step (Task 4).
    texts = _texts(sections.export.body_box)
    for key in ("report_template_label", "report_template_hint"):
        assert t(f"analysis_tile.export_labels.{key}") not in texts
    # Before a save: one list, every row disabled, the build row last and
    # saying what it waits for.
    from tests.conftest import _build_button, _shown

    build = _build_button(parts.body)
    assert parts.body.children == [*parts.tiles, build]
    assert build.disabled and (build.title, build.detail) == ("Build files", "Save this alert first")
    for tile in parts.tiles:
        assert _shown(parts.body, tile) and tile.disabled


def test_the_footer_registers_its_save_with_the_runner():
    """One writer at a time: the runner holds the Save while it saves. Next
    saves nothing, so it is not held."""
    guarded = []

    def run_save(button=None, then=None):
        pass

    run_save.guard = lambda *buttons: guarded.extend(buttons)
    app_state, analysis = AppState(), AnalysisState()
    footer = build_footer(run_save, v.Html(tag="div"), app_state, analysis)
    assert list(map(id, guarded)) == [id(footer.save)]


# ---------------------------------------------------------------------------
# A required refusal opens the Questions section
# ---------------------------------------------------------------------------
def _refuse_with(monkeypatch, key, **values):
    def _refuse(*_a, **_k):
        raise review_save.SaveRefused(key=key, **values)

    monkeypatch.setattr(export_mod, "_save_attributes", _refuse)
    monkeypatch.setattr(export_mod, "spawn_in_context", lambda target, *a, **k: target())


def test_a_required_refusal_opens_the_questions_section(panel, monkeypatch):
    sections = analysis_tile.panel_sections(panel.controls)
    assert not sections.questions.is_open()
    _refuse_with(monkeypatch, review_save.REQUIRED_MISSING,
                 question="Road access", question_key="road_access")
    save, _next, _ = footer_widgets(sections.footer.root)

    save.fire_event("click", None)

    assert sections.questions.is_open()
    assert "display: none" not in sections.questions.body_box.style_
    assert save.loading is False and save.disabled is False


def test_any_other_refusal_leaves_the_questions_section_alone(panel, monkeypatch):
    """Only the missing answer is in the Questions section; a refusal for the
    verdict or the drawing has nothing to show there."""
    sections = analysis_tile.panel_sections(panel.controls)
    _refuse_with(monkeypatch, "analysis_tile.questionarie.confirmation_missing")
    save, _next, _ = footer_widgets(sections.footer.root)

    save.fire_event("click", None)

    assert not sections.questions.is_open()


def test_the_refusal_hook_runs_after_the_warning_and_a_failing_hook_is_only_logged(
    tmp_path, monkeypatch, caplog
):
    """``on_refused`` gets the refusal itself, once the reviewer has been told
    (the warning is on the bus), and a hook that raises loses nothing: the
    task is still FAILED with its reason and the button comes back."""
    import logging

    from pysepal.solara.notifications.state import TaskStatus

    from tests.test_feedback_notifications import _save_harness

    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    seen = []

    def _hook(refusal):
        seen.append((refusal.key, list(feedback.toasts())))
        raise RuntimeError("hook broke")

    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model, on_refused=_hook)
    button = v.Btn()
    analysis.verdict.set(None)  # no verdict: refused
    with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts.analysis"):
        run_save(button)

    warning = ("warning", t("analysis_tile.questionarie.confirmation_missing"))
    assert seen == [("analysis_tile.questionarie.confirmation_missing", [warning])]
    assert feedback.task().status == TaskStatus.FAILED
    assert feedback.errors == []  # the hook's failure is not an error toast
    assert "the refusal hook failed" in caplog.text
    assert button.loading is False and button.disabled is False


def test_a_save_that_wrote_does_not_call_the_refusal_hook(tmp_path, monkeypatch):
    from tests.test_feedback_notifications import _save_harness

    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    seen = []
    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model,
                                           on_refused=seen.append)
    analysis.verdict.set("Confirmed")
    run_save(None)
    assert ("success", t("analysis_tile.export_labels.save_done")) in feedback.toasts()
    assert seen == []


# ---------------------------------------------------------------------------
# Save, Next and the save-state line (moved from the verdict card)
# ---------------------------------------------------------------------------
def _footer(monkeypatch, tmp_path, saves=None):
    """The footer alone, on a fake runner (``saves(button, then)``), over
    three alerts with the first one on screen."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(
        Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
    _three_alerts(app_state)
    analysis = AnalysisState()
    calls = saves or (lambda button, then: None)
    footer = build_footer(lambda button=None, then=None: calls(button, then),
                          v.Html(tag="div"), app_state, analysis)
    return footer, analysis, app_state


def test_the_state_line_reads_unsaved_then_saved(tmp_path, monkeypatch):
    footer, analysis, _ = _footer(monkeypatch, tmp_path)
    *_, state_line = footer_widgets(footer.root)
    assert state_line.children == []

    analysis.dirty.set(True)
    assert state_line.children == [t("analysis_tile.verdict.unsaved")]

    analysis.dirty.set(False)
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]


def test_the_state_line_sits_right_and_saved_wears_the_done_ink(tmp_path, monkeypatch):
    """Under the dock's buttons, on the right whatever it says; *Saved* in the
    screen's done ink (the theme's primary), not Vuetify's green success."""
    footer, analysis, _ = _footer(monkeypatch, tmp_path)
    *_, state_line = footer_widgets(footer.root)
    assert "text-right" in state_line.class_.split()

    analysis.dirty.set(True)
    assert {"text-right", "warning--text"} <= set(state_line.class_.split())

    analysis.dirty.set(False)
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    classes = set(state_line.class_.split())
    assert {"text-right", "dat-done-ink"} <= classes and "success--text" not in classes


def test_an_alert_change_forgets_the_previous_alerts_save_time(tmp_path, monkeypatch):
    """Spec §4.4: *Saved HH:MM* means a save landed on **this** alert in this
    session, so an untouched alert must show the empty line and not inherit
    the previous one's stamp.

    ``reset_alert`` sets ``dirty`` to the ``False`` it already holds, which
    notifies nobody, so only the ``last_save_time`` subscription can carry the
    re-render — asserted on the rendered line, not on the reactive alone.
    """
    footer, analysis, _ = _footer(monkeypatch, tmp_path)
    *_, state_line = footer_widgets(footer.root)

    analysis.verdict.set("Confirmed")
    analysis.dirty.set(False)  # as a save that wrote leaves it
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]

    analysis.reset_alert()  # the reviewer moves to another alert
    assert analysis.last_save_time.value == 0.0
    assert state_line.children == []


def test_the_state_line_asks_for_the_apply_before_it_asks_for_a_save(tmp_path, monkeypatch):
    """The reviewer has to see why the save will not take: the save-state line
    reads the unapplied drawing ahead of its own "Unsaved changes", since
    applying is what clears both."""
    footer, analysis, _ = _footer(monkeypatch, tmp_path)
    *_, state_line = footer_widgets(footer.root)

    analysis.drawing_unapplied.set(True)
    analysis.dirty.set(True)
    assert state_line.children == [t("analysis_tile.edition_labels.unapplied_drawing")]
    assert "warning--text" in state_line.class_

    analysis.drawing_unapplied.set(False)
    assert state_line.children == [t("analysis_tile.verdict.unsaved")]


def test_save_saves_and_stays_and_next_moves_without_saving(tmp_path, monkeypatch):
    """Two buttons (user, 2026-10-26): *Save* runs the save and never moves;
    *Next* moves and never saves."""
    calls = []
    footer, _, app_state = _footer(monkeypatch, tmp_path,
                                   saves=lambda b, then: calls.append((b, then)))
    save, nxt, _ = footer_widgets(footer.root)

    save.fire_event("click", None)
    assert calls == [(save, None)]
    assert app_state.actual_alert_id.value == 0

    nxt.fire_event("click", None)
    assert calls == [(save, None)]
    assert app_state.actual_alert_id.value == 1


def test_a_refused_save_stays_on_the_alert_and_gives_the_button_back(tmp_path, monkeypatch):
    """Review Focus: pressing **Save** on an alert with no verdict must
    refuse, leave the reviewer where they are and give the button back; a
    save that writes stays too, and Next then moves without asking.

    The real ``make_save_runner`` runs, inline, against the real alert store.
    """
    from tests.test_feedback_notifications import _save_harness

    # The harness's own recorder, not a second one: ``_record_feedback`` also
    # re-points ``spawn_in_context`` at a real thread, and the save must have
    # finished by the time the assertions below run.
    app_state, analysis, widgets, model, feedback = _save_harness(tmp_path, monkeypatch)
    # Two alerts, so "stayed on this one" is a real assertion and not a clamp.
    one = app_state.alerts_gdf.value
    app_state.alerts_gdf.value = pd.concat([one, one], ignore_index=True)
    app_state.actual_alert_id.value = 0

    run_save = export_mod.make_save_runner(widgets, app_state, analysis, model)
    footer = build_footer(run_save, v.Html(tag="div"), app_state, analysis)
    save, nxt, _ = footer_widgets(footer.root)

    save.fire_event("click", None)  # no verdict chosen
    assert app_state.actual_alert_id.value == 0
    assert app_state.alerts_gdf.value["status"].tolist() == ["Not reviewed"] * 2
    assert save.loading is False and save.disabled is False
    assert feedback.toasts() == [
        ("warning", t("analysis_tile.questionarie.confirmation_missing"))
    ]

    analysis.verdict.set("Confirmed")
    analysis.dirty.set(True)
    save.fire_event("click", None)
    assert app_state.actual_alert_id.value == 0  # Save never moves
    assert app_state.alerts_gdf.value["status"].tolist() == ["Confirmed", "Not reviewed"]
    assert save.loading is False and save.disabled is False
    assert analysis.dirty.value is False

    nxt.fire_event("click", None)  # nothing unsaved: no question
    assert not _prompt_shown(footer.root)
    assert app_state.actual_alert_id.value == 1


# ---------------------------------------------------------------------------
# Next over unsaved work: Discard / Stay
# ---------------------------------------------------------------------------
def test_next_over_unsaved_work_asks_and_discard_moves_on(tmp_path, monkeypatch):
    footer, analysis, app_state = _footer(monkeypatch, tmp_path)
    _save, nxt, _ = footer_widgets(footer.root)
    _prompt, discard, _stay = prompt_widgets(footer.root)
    assert not _prompt_shown(footer.root)

    analysis.dirty.set(True)
    nxt.fire_event("click", None)

    assert _prompt_shown(footer.root)
    assert app_state.actual_alert_id.value == 0  # asked, not moved

    discard.fire_event("click", None)

    assert app_state.actual_alert_id.value == 1
    assert not _prompt_shown(footer.root)


def test_stay_keeps_the_alert_and_its_work(tmp_path, monkeypatch):
    footer, analysis, app_state = _footer(monkeypatch, tmp_path)
    _save, nxt, _ = footer_widgets(footer.root)
    _prompt, discard, stay = prompt_widgets(footer.root)
    analysis.dirty.set(True)
    nxt.fire_event("click", None)

    stay.fire_event("click", None)

    assert not _prompt_shown(footer.root)
    assert app_state.actual_alert_id.value == 0
    assert analysis.dirty.value is True
    # The answer forgot the move it held: a stray Discard moves nothing.
    discard.fire_event("click", None)
    assert app_state.actual_alert_id.value == 0


def test_the_prompt_words_follow_the_catalogue(tmp_path, monkeypatch):
    footer, _, _ = _footer(monkeypatch, tmp_path)
    prompt, discard, stay = prompt_widgets(footer.root)
    texts = _texts(prompt)
    assert "This alert has unsaved changes" in texts
    assert "Discard drops them and moves on. Stay keeps you on this alert." in texts
    assert (label_of(discard), label_of(stay)) == ("Discard", "Stay")


def test_an_alert_change_or_a_save_answers_the_prompt(tmp_path, monkeypatch):
    """The question is about one alert's work: another alert (however it
    came) or a save that leaves nothing unsaved hides it, and drops the move
    it held."""
    footer, analysis, app_state = _footer(monkeypatch, tmp_path)
    _save, nxt, _ = footer_widgets(footer.root)
    _prompt, discard, _stay = prompt_widgets(footer.root)

    analysis.dirty.set(True)
    nxt.fire_event("click", None)
    assert _prompt_shown(footer.root)
    analysis.reset_alert()  # e.g. a pick on the Overview map
    assert not _prompt_shown(footer.root)

    analysis.dirty.set(True)
    nxt.fire_event("click", None)
    assert _prompt_shown(footer.root)
    analysis.dirty.set(False)  # the save landed
    assert not _prompt_shown(footer.root)
    discard.fire_event("click", None)
    assert app_state.actual_alert_id.value == 0


def test_the_filled_button_follows_the_saved_review(tmp_path, monkeypatch):
    """Save is filled while there is something to save -- unsaved work, or no
    saved review of the alert -- and Next once the review is saved, this
    session or before (its row has a status)."""
    footer, analysis, app_state = _footer(monkeypatch, tmp_path)
    save, nxt, _ = footer_widgets(footer.root)
    skip = next_unreviewed_widget(footer.root)

    def filled():
        assert save.outlined != nxt.outlined, "exactly one of the two is filled"
        assert skip.outlined == nxt.outlined, "the next-unreviewed icon wears Next's emphasis"
        return "save" if not save.outlined else "next"

    assert filled() == "save"  # nothing saved yet
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert filled() == "next"
    analysis.dirty.set(True)
    assert filled() == "save"
    analysis.dirty.set(False)
    assert filled() == "next"
    analysis.reset_alert()  # another alert, never saved
    assert filled() == "save"

    gdf = app_state.alerts_gdf.value.copy()
    gdf["status"] = ["Not reviewed", "Confirmed", "Not reviewed"]
    app_state.alerts_gdf.value = gdf
    assert filled() == "save"  # this row is "Not reviewed"
    app_state.actual_alert_id.value = 1  # a row saved in an earlier session
    assert filled() == "next"


def test_the_skip_icon_after_next_goes_to_the_next_unreviewed_alert(tmp_path, monkeypatch):
    footer, analysis, app_state = _footer(monkeypatch, tmp_path)
    _save, nxt, _ = footer_widgets(footer.root)
    skip = next_unreviewed_widget(footer.root)
    assert footer.root.children[1].children[-2:] == [nxt, skip]
    assert skip.attributes["title"] == skip.attributes["aria-label"] == t("analysis_tile.nav.next_unreviewed")
    gdf = app_state.alerts_gdf.value.copy()
    gdf["status"] = ["Not reviewed", "Confirmed", "Not reviewed"]
    app_state.alerts_gdf.value = gdf
    assert not skip.disabled

    analysis.dirty.set(True)
    skip.fire_event("click", None)
    assert _prompt_shown(footer.root) and app_state.actual_alert_id.value == 0  # asks first
    analysis.dirty.set(False)
    skip.fire_event("click", None)
    assert app_state.actual_alert_id.value == 2  # past the reviewed alert 2

    gdf = gdf.copy()
    gdf["status"] = ["Confirmed", "Confirmed", "Not reviewed"]
    app_state.alerts_gdf.value = gdf
    assert skip.disabled, "nothing else left to review"


# ---------------------------------------------------------------------------
# The top bar's alert changes ask the same question
# ---------------------------------------------------------------------------
def test_the_top_bar_navigation_asks_first_over_unsaved_work(panel):
    from gui.tile.analysis import context

    _three_alerts(panel.app_state)
    footer = analysis_tile.panel_sections(panel.controls).footer.root
    _prompt, discard, stay = prompt_widgets(footer)
    _banner, card, *_ = panel.controls.children
    prev, nxt, _jump, _menu, go = context.nav_widgets(card)
    skip = context.next_unreviewed_widget(card)
    panel.analysis.dirty.set(True)

    def asks(click):
        click()
        shown = _prompt_shown(footer)
        moved = panel.app_state.actual_alert_id.value
        stay.fire_event("click", None)
        return shown, moved

    panel.app_state.actual_alert_id.value = 1
    panel.analysis.dirty.set(True)
    assert asks(lambda: prev.fire_event("click", None)) == (True, 1)
    assert asks(lambda: nxt.fire_event("click", None)) == (True, 1)
    panel.id_field.v_model = "3"
    assert asks(lambda: go.fire_event("click", None)) == (True, 1)
    assert asks(lambda: skip.fire_event("click", None)) == (True, 1)

    # Discard runs the move the prompt held: here, Go to alert 3.
    panel.id_field.v_model = "3"
    go.fire_event("click", None)
    discard.fire_event("click", None)
    assert panel.app_state.actual_alert_id.value == 2

    # Nothing unsaved: the top bar moves at once.
    panel.analysis.dirty.set(False)
    prev.fire_event("click", None)
    assert not _prompt_shown(footer)
    assert panel.app_state.actual_alert_id.value == 1
