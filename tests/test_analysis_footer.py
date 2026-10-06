"""The sticky footer (spec D6): Export section, then Save & next / Save, then
the save-state line; a required refusal opens the Questions section.

The Save buttons and the save-state line moved here from the verdict card
(block B), so their tests moved with them from tests/test_analysis_verdict.py.
"""

from datetime import datetime
from types import SimpleNamespace

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis import export as export_mod
from gui.tile.analysis.footer import build_footer, footer_widgets
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


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
    draw = SimpleNamespace(clear=lambda: None)
    shown_map = SimpleNamespace(add_layer=lambda *_: None)
    controls = analysis_tile.build_controls(
        widgets=SimpleNamespace(draw_before=draw, draw_after=draw,
                                map_before=shown_map, map_after=shown_map),
        app_state=app_state, analysis=analysis,
        aux_model=model, app_tile_model=model, id_field=v.TextField(v_model=None))
    return SimpleNamespace(controls=controls, app_state=app_state, analysis=analysis)


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


# ---------------------------------------------------------------------------
# The footer in the assembled panel
# ---------------------------------------------------------------------------
def test_the_footer_is_the_panels_last_child_and_sticks(panel):
    controls = panel.controls
    footer = controls.children[-1]
    assert "position: sticky" in footer.style_ and "bottom: 0" in footer.style_
    # The Workflow panel pads its content (16px) and spaces its sections (16px):
    # a sticky element stays inside its parent, so without this negative
    # margin the footer stops 32px above the panel's bottom edge once the
    # content is scrolled to its end (seen in the browser walk).
    assert "margin-bottom: -32px" in footer.style_
    sections = analysis_tile.panel_sections(controls)
    assert footer is sections.footer.root
    assert footer.children[0] is sections.export.root
    save_next, save, state_line = footer_widgets(footer)
    assert save_next.color == "primary"
    # The literal English (the fixture pins the locale), not ``t()`` of the
    # same key, which passes on the ``⟦key⟧`` placeholder too.
    assert save.children == ["Save"] and save_next.children[0] == "Save & next"
    # The order inside the footer: Export, the two buttons, the state line.
    assert save_next in footer.children[1].children and save in footer.children[1].children
    assert footer.children[2].children == [state_line]
    # The background is an opaque surface: the panel content scrolls under it.
    assert "background-color" in footer.style_


def test_the_panel_order_is_head_verdict_questions_refine_footer(panel):
    from gui.tile.analysis import context, review_panel

    sections = analysis_tile.panel_sections(panel.controls)
    *head, block_a, block_b, questions, refine, footer = panel.controls.children
    assert len(head) == 3                       # help + selector + banner
    context.facts_widgets(block_a)              # block A owns the facts...
    assert set(review_panel.verdict_widgets(block_b)) == {
        "Confirmed", "False Positive", "Need revision"}  # ...block B the verdict
    assert [questions, refine, footer] == [sections.questions.root, sections.refine.root,
                                           sections.footer.root]
    kids = panel.controls.children
    assert kids.index(sections.questions.root) == kids.index(sections.refine.root) - 1


def test_the_verdict_card_no_longer_holds_the_save(panel):
    """The Save buttons and the state line left block B for the footer."""
    *_, block_b, _questions, _refine, _footer = panel.controls.children
    texts = _texts(block_b)
    assert "Save" not in texts and "Save & next" not in texts


def test_the_export_body_has_no_template_field_and_three_tiles(panel):
    sections = analysis_tile.panel_sections(panel.controls)
    parts = sections.export_parts
    assert [tile.kind for tile in parts.tiles] == ["zip", "docx", "gif"]
    assert parts.tiles[2].disabled  # TODO(timelapse) placeholder
    # The report template now lives in the Questionnaire step (Task 4).
    texts = _texts(sections.export.body_box)
    for key in ("report_template_label", "report_template_hint"):
        assert t(f"analysis_tile.export_labels.{key}") not in texts
    # Before a save: the dashed build tile (disabled, "save first") and the
    # GIF tile, shown in every state; the zip and report tiles wait for a build.
    from tests.conftest import _build_button, _shown

    zip_tile, docx_tile, gif_tile = parts.tiles
    build = _build_button(parts.body)
    assert _shown(parts.body, build) and build.disabled
    assert "Build alert files" in texts and "(save first)" in texts
    assert _shown(parts.body, gif_tile) and gif_tile.disabled
    assert not _shown(parts.body, zip_tile) and not _shown(parts.body, docx_tile)


def test_the_footer_registers_both_save_buttons_with_the_runner():
    """One writer at a time: the runner holds both buttons while it saves."""
    guarded = []

    def run_save(button=None, then=None):
        pass

    run_save.guard = lambda *buttons: guarded.extend(buttons)
    app_state, analysis = AppState(), AnalysisState()
    footer = build_footer(run_save, v.Html(tag="div"), app_state, analysis)
    assert set(map(id, guarded)) == {id(footer.save_next), id(footer.save)}


# ---------------------------------------------------------------------------
# A required refusal opens the Questions section
# ---------------------------------------------------------------------------
def _refuse_with(monkeypatch, key, **values):
    def _refuse(*_a, **_k):
        raise export_mod.SaveRefused(key=key, **values)

    monkeypatch.setattr(export_mod, "_save_attributes", _refuse)
    monkeypatch.setattr(export_mod, "spawn_in_context", lambda target, *a, **k: target())


def test_a_required_refusal_opens_the_questions_section(panel, monkeypatch):
    sections = analysis_tile.panel_sections(panel.controls)
    assert not sections.questions.is_open()
    _refuse_with(monkeypatch, export_mod.REQUIRED_MISSING,
                 question="Road access", question_key="road_access")
    save_next, _save, _ = footer_widgets(sections.footer.root)

    save_next.fire_event("click", None)

    assert sections.questions.is_open()
    assert "display: none" not in sections.questions.body_box.style_
    assert save_next.loading is False and save_next.disabled is False


def test_any_other_refusal_leaves_the_questions_section_alone(panel, monkeypatch):
    """Only the missing answer is in the Questions section; a refusal for the
    verdict or the drawing has nothing to show there."""
    sections = analysis_tile.panel_sections(panel.controls)
    _refuse_with(monkeypatch, "analysis_tile.questionarie.confirmation_missing")
    _save_next, save, _ = footer_widgets(sections.footer.root)

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
# The Save buttons and the save-state line (moved from the verdict card)
# ---------------------------------------------------------------------------
def _footer(monkeypatch, tmp_path, saves=None):
    """The footer alone, on a fake runner (``saves(button, then)``)."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    app_state.new_project_state(
        Project(name="d", created_at=datetime.now(), updated_at=datetime.now()))
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


def test_save_and_next_asks_to_advance_and_plain_save_does_not(tmp_path, monkeypatch):
    calls = []
    footer, _, app_state = _footer(monkeypatch, tmp_path,
                                   saves=lambda b, then: calls.append((b, then)))
    save_next, save, _ = footer_widgets(footer.root)

    save.fire_event("click", None)
    assert calls == [(save, None)]

    save_next.fire_event("click", None)
    assert calls[1] == (save_next, app_state.go_next)


def test_a_refused_save_and_next_stays_on_the_alert(tmp_path, monkeypatch):
    """Review Focus: pressing **Save & next** on an alert with no verdict must
    refuse, leave the reviewer where they are and give the button back.

    The real ``make_save_runner`` runs, inline, against the real alert store —
    only ``then``-after-a-write keeps the queue from moving, so it is pinned
    here at the footer's own level and not merely trusted of the runner.
    """
    import pandas as pd

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
    save_next, _, _ = footer_widgets(footer.root)

    save_next.fire_event("click", None)  # no verdict chosen
    assert app_state.actual_alert_id.value == 0
    assert app_state.alerts_gdf.value["status"].tolist() == ["Not reviewed"] * 2
    assert save_next.loading is False and save_next.disabled is False
    assert feedback.toasts() == [
        ("warning", t("analysis_tile.questionarie.confirmation_missing"))
    ]

    analysis.verdict.set("Confirmed")
    save_next.fire_event("click", None)
    assert app_state.actual_alert_id.value == 1
    assert app_state.alerts_gdf.value["status"].tolist() == ["Confirmed", "Not reviewed"]
    assert save_next.loading is False and save_next.disabled is False
