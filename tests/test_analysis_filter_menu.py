"""The Analysis filter's control in the top bar (spec §8.3): the funnel
button's menu (attribute, values with counts, Apply / Clear) and the
closable chip. Real ``AppState`` and ``AnalysisState``, raw widgets driven
with ``fire_event`` and ``v_model`` writes, as block A's tests drive it."""

from types import SimpleNamespace

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from deforestation_alerts.project.models import Question, QuestionOption
from gui.i18n import t
from gui.scripts.alert_filter import AlertFilter
from gui.scripts.set_context import SetContext
from gui.tile.analysis import analysis_tile, context
from gui.tile.analysis.filter_menu import build_filter_menu
from gui.tile.analysis.state import AnalysisState
from tests.test_analysis_context import _alerts, _state


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _six():
    return _alerts(["Confirmed", "Not reviewed", "Not reviewed", "Not reviewed", "", "Confirmed"]).assign(
        evaluator=["Ana", "Bruno", "Ana", "Bruno", "Ana", ""],
        q_road=["yes", "no", "", "yes", "yes", "no"])


@pytest.fixture()
def bar(tmp_path, monkeypatch):
    """The control on a bound state with six alerts on screen, wired as the
    layout wires the navigation (a filter moves to its start)."""
    s = _state(tmp_path, monkeypatch)
    p = s.project.value
    p.questions = [*p.questions, Question(key="road", label="Road nearby?", options=[
        QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")])]
    analysis = AnalysisState()
    analysis_tile.wire_alert_navigation(SimpleNamespace(), s, analysis, SetContext(s), SimpleNamespace(v_model=None))
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(1)
    return SimpleNamespace(state=s, analysis=analysis, ui=build_filter_menu(s))


def test_the_menu_offers_the_attributes_of_the_alerts_on_hand(bar):
    ui = bar.ui
    assert ui.menu.v_slots[0]["children"] is ui.button and ui.button.v_on == "menu.on"
    assert ui.button.attributes["aria-label"] == t("analysis_tile.filter.button")
    ui.menu.v_model = True
    assert [i["value"] for i in ui.attribute.items] == ["evaluator", "status", "q_road"]
    assert ui.attribute.items[0]["text"] == t("analysis_tile.filter.col_evaluator")
    assert ui.attribute.items[2]["text"] == "Road nearby?"
    assert ui.attribute.v_model is None and ui.values.items == []
    assert "display: none" in ui.empty.style_ and not ui.apply.disabled


def test_picking_an_attribute_lists_its_values_with_counts(bar):
    ui = bar.ui
    ui.menu.v_model = True
    ui.attribute.v_model = "evaluator"
    assert ui.values.items == [
        {"text": "Ana (3)", "value": "Ana"}, {"text": "Bruno (2)", "value": "Bruno"},
        {"text": t("analysis_tile.filter.unassigned") + " (1)", "value": ""}]
    assert ui.values.v_model == []


def test_apply_sets_the_filter_moves_to_its_start_and_shows_the_chip(bar):
    ui, s = bar.ui, bar.state
    ui.menu.v_model = True
    ui.attribute.v_model = "evaluator"
    ui.values.v_model = ["Ana"]
    ui.apply.fire_event("click", None)

    assert bar.analysis.alert_filter.value == AlertFilter("evaluator", frozenset({"Ana"}))
    assert ui.menu.v_model is False
    assert s.actual_alert_id.value == 2              # Bruno's 1 -> Ana's first unreviewed
    assert ui.chip.children == ["Evaluator: Ana"] and "display: none" not in ui.chip.style_
    assert ui.button.color == "primary" and ui.button.children[0].children == ["mdi-filter"]


def test_the_menu_reopens_on_the_filter_in_force(bar):
    ui = bar.ui
    bar.state.set_alert_filter(AlertFilter("q_road", frozenset({"yes", "no"})))
    ui.menu.v_model = True
    assert ui.attribute.v_model == "q_road"
    assert ui.values.v_model == ["yes", "no"]
    assert ui.values.items[0] == {"text": "Yes (3)", "value": "yes"}
    assert ui.chip.children == ["Road nearby?: Yes, No"]


def test_a_value_no_alert_has_any_more_stays_listed(bar):
    ui = bar.ui
    bar.state.set_alert_filter(AlertFilter("evaluator", frozenset({"Carla"})))
    ui.menu.v_model = True
    assert {"text": "Carla", "value": "Carla"} in ui.values.items and ui.values.v_model == ["Carla"]


def test_the_chip_names_two_values_and_counts_the_rest(bar):
    bar.state.set_alert_filter(AlertFilter("status", frozenset({"Not reviewed", "Confirmed", "Need revision"})))
    assert bar.ui.chip.children == [
        "Status: " + t("legend.status.not_reviewed") + ", " + t("legend.status.confirmed") + " +1"]


def test_closing_the_chip_or_clear_removes_the_filter(bar):
    ui, s = bar.ui, bar.state
    s.set_alert_filter(AlertFilter("evaluator", frozenset({"Ana"})))
    ui.chip.fire_event("click:close", None)
    assert s.alert_filter is None and "display: none" in ui.chip.style_
    assert ui.button.children[0].children == ["mdi-filter-outline"] and not ui.button.color

    s.set_alert_filter(AlertFilter("evaluator", frozenset({"Ana"})))
    ui.menu.v_model = True
    ui.clear.fire_event("click", None)
    assert s.alert_filter is None and ui.menu.v_model is False


def test_apply_with_no_value_picked_clears(bar):
    ui, s = bar.ui, bar.state
    s.set_alert_filter(AlertFilter("evaluator", frozenset({"Ana"})))
    ui.menu.v_model = True
    ui.values.v_model = []
    ui.apply.fire_event("click", None)
    assert s.alert_filter is None


def test_without_alerts_the_menu_says_there_is_nothing_to_filter(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.bind_alert_filter(AnalysisState().alert_filter)
    ui = build_filter_menu(s)
    ui.menu.v_model = True
    assert ui.attribute.items == [] and ui.apply.disabled
    assert ui.empty.children == [t("analysis_tile.filter.no_columns")]
    assert "display: none" not in ui.empty.style_


def test_the_top_bar_puts_the_filter_left_of_the_set_selector(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.bind_alert_filter(AnalysisState().alert_filter)
    card = context.build_context(s, v.TextField(v_model=None))
    selector = v.Select(items=[])
    topnav = context.build_topnav(card, selector)
    classes = [getattr(c, "class_", None) for c in topnav.children]
    assert classes.index("defo-topnav-filter") == classes.index("defo-topnav-set") - 1
    assert context.filter_control(card).root is topnav.children[classes.index("defo-topnav-filter")]


def test_an_unbound_top_bar_has_no_filter(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    card = context.build_context(s, v.TextField(v_model=None))
    topnav = context.build_topnav(card, v.Select(items=[]))
    assert "defo-topnav-filter" not in [getattr(c, "class_", None) for c in topnav.children]
    assert context.filter_control(card) is None
