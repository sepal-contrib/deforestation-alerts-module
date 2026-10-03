"""Block B: the three-button verdict, the driver that disappears for a false
positive, Save & next, and the save-state line.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import Project
from gui.i18n import t
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import review_panel
from gui.tile.analysis.state import AnalysisState


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _panel(monkeypatch, tmp_path, saves=None):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    app_state = AppState()
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    app_state.new_project_state(p)
    analysis = AnalysisState()
    widgets = SimpleNamespace()
    if saves is not None:
        monkeypatch.setattr(review_panel, "make_save_runner",
                            lambda *a, **k: lambda button=None, then=None: saves(button, then))
    card = review_panel.build_review_panel(widgets, app_state, analysis, SetContext(app_state))
    return card, widgets, analysis, app_state


def test_a_button_sets_the_verdict_and_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    assert analysis.verdict.value is None

    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is True


def test_the_chosen_button_is_the_filled_one(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)
    analysis.verdict.set("Need revision")
    assert buttons["Need revision"].outlined is False
    assert buttons["Confirmed"].outlined is True


def test_a_false_positive_hides_the_driver_but_keeps_it_in_the_tree(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, driver_box, note, *_ = review_panel.verdict_widgets(card)
    assert "display: none" not in driver_box.style_

    analysis.verdict.set("False Positive")
    assert "display: none" in driver_box.style_
    assert "display: none" not in note.style_
    assert widgets.driver_combobox in driver_box.children

    analysis.verdict.set("Confirmed")
    assert "display: none" not in driver_box.style_
    assert "display: none" in note.style_


def test_a_driver_change_marks_the_alert_dirty(tmp_path, monkeypatch):
    card, widgets, analysis, _ = _panel(monkeypatch, tmp_path)
    widgets.driver_combobox.fire_event("change", ["mining"])
    assert analysis.dirty.value is True


def test_the_state_line_reads_unsaved_then_saved(tmp_path, monkeypatch):
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)
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
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    *_, state_line = review_panel.verdict_widgets(card)

    analysis.verdict.set("Confirmed")
    analysis.dirty.set(False)  # as a save that wrote leaves it
    analysis.last_save_time.set(datetime(2026, 10, 3, 14, 32).timestamp())
    assert state_line.children == [t("analysis_tile.verdict.saved_at", time="14:32")]

    analysis.reset_alert()  # the reviewer moves to another alert
    assert analysis.last_save_time.value == 0.0
    assert state_line.children == []


def test_re_clicking_the_chosen_verdict_does_not_mark_the_alert_unsaved(tmp_path, monkeypatch):
    """``dirty`` follows a verdict *change* (spec §4.4): pressing the answer
    that is already selected after a save must not claim unsaved changes."""
    card, _, analysis, _ = _panel(monkeypatch, tmp_path)
    buttons, *_ = review_panel.verdict_widgets(card)

    buttons["Confirmed"].fire_event("click", None)
    analysis.dirty.set(False)  # as a save that wrote leaves it
    buttons["Confirmed"].fire_event("click", None)
    assert analysis.verdict.value == "Confirmed"
    assert analysis.dirty.value is False

    buttons["Need revision"].fire_event("click", None)  # a real change still counts
    assert analysis.dirty.value is True


def test_save_and_next_asks_to_advance_and_plain_save_does_not(tmp_path, monkeypatch):
    calls = []
    card, _, _, _ = _panel(monkeypatch, tmp_path, saves=lambda b, then: calls.append(then))
    *_, save_next, save, _ = review_panel.verdict_widgets(card)

    save.fire_event("click", None)
    assert calls == [None]

    save_next.fire_event("click", None)
    assert calls[1] is not None


def test_a_refused_save_and_next_stays_on_the_alert(tmp_path, monkeypatch):
    """Review Focus: pressing **Save & next** on an alert with no verdict must
    refuse, leave the reviewer where they are and give the button back.

    The real ``make_save_runner`` runs, inline, against the real alert store —
    only ``then``-after-a-write keeps the queue from moving, so it is pinned
    here at the panel's own level and not merely trusted of the runner.
    """
    import pandas as pd

    import gui.tile.analysis.export as export_mod
    from tests.test_feedback_notifications import _record_feedback, _save_harness

    app_state, analysis, widgets, model = _save_harness(tmp_path, monkeypatch)
    feedback = _record_feedback(monkeypatch, export_mod)
    # _record_feedback re-points spawn_in_context at a real thread; the save
    # must finish before the assertions, as it does in _save_harness.
    monkeypatch.setattr(export_mod, "spawn_in_context",
                        lambda target, args=(), **k: target(*args))
    # Two alerts, so "stayed on this one" is a real assertion and not a clamp.
    one = app_state.alerts_gdf.value
    app_state.alerts_gdf.value = pd.concat([one, one], ignore_index=True)
    app_state.actual_alert_id.value = 0

    card = review_panel.build_review_panel(widgets, app_state, analysis, model)
    *_, save_next, _, _ = review_panel.verdict_widgets(card)

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
