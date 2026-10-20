"""Navigation over the Analysis filter (spec §8.1, §8.3, §8.4): the filter
lives on ``AnalysisState`` and is bound into ``AppState``; Previous / Next,
Save & next, the counter, the jump menu, Next unreviewed, the start position
and Overview's Go all follow it. A real ``AppState`` and ``AnalysisState``,
stand-in widgets, as tests/test_analysis_context.py drives block A."""

from types import SimpleNamespace

import ipyvuetify as v
import pytest
from pysepal.i18n import set_locale

from gui.i18n import format_number, t
from gui.scripts import set_store
from gui.scripts.alert_filter import AlertFilter
from gui.scripts.set_context import SetContext
from gui.tile.analysis import analysis_tile, context
from gui.tile.analysis.footer import build_footer, footer_widgets
from gui.tile.analysis.state import AnalysisState, apply_start_filter
from tests.test_analysis_context import _alerts, _state
from tests.test_set_store import _project_with_set, _session

ANA = AlertFilter("evaluator", frozenset({"Ana"}))


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _six(set_id="x"):
    """Ana has rows 0, 2 and 4 (0 reviewed), Bruno 1 and 3, row 5 nobody."""
    return _alerts(["Confirmed", "Not reviewed", "Not reviewed", "Not reviewed", "", "Confirmed"],
                   set_id=set_id).assign(evaluator=["Ana", "Bruno", "Ana", "Bruno", "Ana", ""])


def _bound(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    analysis = AnalysisState()
    s.bind_alert_filter(analysis.alert_filter)
    return s, analysis


# ---- AppState: the binding, the resets, Previous / Next -------------------------

def test_previous_and_next_walk_the_matches_and_stay_at_the_ends(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(0)
    analysis.alert_filter.set(ANA)
    assert s.alert_filter == ANA and s.alert_mask().tolist() == [True, False, True, False, True, False]

    s.go_next()
    assert s.actual_alert_id.value == 2
    s.go_next()
    s.go_next()  # the last match: stays
    assert s.actual_alert_id.value == 4
    s.go_prev()
    assert s.actual_alert_id.value == 2

    s.actual_alert_id.set(3)  # an alert outside the filter (a Go made before it)
    s.go_prev()
    assert s.actual_alert_id.value == 2
    s.actual_alert_id.set(3)
    s.go_next()
    assert s.actual_alert_id.value == 4


def test_a_filter_nobody_matches_moves_nothing(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(1)
    analysis.alert_filter.set(AlertFilter("evaluator", frozenset({"Carla"})))
    s.go_next()
    s.go_prev()
    assert s.actual_alert_id.value == 1


def test_an_unbound_state_has_no_filter_and_walks_every_alert(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(0)
    s.set_alert_filter(ANA)  # nothing bound: a no-op
    assert s.alert_filter is None and s.alert_mask().all()
    s.go_next()
    assert s.actual_alert_id.value == 1


def test_binding_the_same_filter_twice_subscribes_once(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    s.bind_alert_filter(analysis.alert_filter)
    assert s.alert_filter_reactive is analysis.alert_filter
    s.alerts_gdf.set(_six())
    analysis.alert_filter.set(ANA)
    s.alerts_gdf.set(_six(set_id="y"))
    assert analysis.alert_filter.value is None


def test_a_project_install_clears_the_filter(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    analysis.alert_filter.set(ANA)
    s.load_project_state(s.project.value)
    assert analysis.alert_filter.value is None


def test_another_sets_alerts_clear_the_filter_and_the_same_sets_keep_it(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    analysis.alert_filter.set(ANA)
    s.alerts_gdf.set(_six())            # a Save republishes the same set
    assert analysis.alert_filter.value == ANA
    s.alerts_gdf.set(None)              # the set was deleted: no alerts in between
    assert analysis.alert_filter.value == ANA
    s.alerts_gdf.set(_six(set_id="y"))  # another set
    assert analysis.alert_filter.value is None


# ---- the start position and Overview's Go (set_store) ------------------------------

def test_a_start_filter_set_after_the_install_survives_the_first_publish(tmp_path, monkeypatch):
    """Spec §6.2: a review copy opens on its evaluator's alerts. The opener
    installs the project (which clears any filter), then sets the filter,
    then the loader publishes the set: the filter stays and the start is the
    evaluator's first unreviewed alert, not the saved position."""
    p, s_, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 1  # Bruno's
    state = _session(p)
    analysis = AnalysisState()
    state.bind_alert_filter(analysis.alert_filter)
    state.load_project_state(p)
    apply_start_filter(analysis, ANA)

    assert set_store.publish_set_alerts(state, "d", s_, _six(set_id="s"))
    assert analysis.alert_filter.value == ANA
    assert state.actual_alert_id.value == 2  # Ana's first unreviewed (her 0 is reviewed)


def test_a_start_filter_after_the_install_moves_alerts_already_on_screen(tmp_path, monkeypatch):
    """T13's order (project_dialog ``_land``): ``load_project_state`` then
    ``apply_start_filter``. When the set's alerts are already published (the
    layout wired), the filter moves to its start at once; the install's
    ``project_loaded_signal`` bump does not clear it."""
    s = _state(tmp_path, monkeypatch)
    p = s.project.value
    p.alert_sets["x"].last_alert_index = 1  # Bruno's
    analysis = AnalysisState()
    analysis_tile.wire_alert_navigation(SimpleNamespace(), s, analysis, SetContext(s),
                                        SimpleNamespace(v_model=None))
    signals = []
    s.project_loaded_signal.subscribe(lambda n: signals.append(analysis.alert_filter.value))
    s.load_project_state(p)
    apply_start_filter(analysis, ANA)
    assert signals == [None] and analysis.alert_filter.value == ANA
    s.alerts_gdf.set(_six())
    assert s.actual_alert_id.value == 2      # the resolver, not the saved 1
    s.project_loaded_signal.set(s.project_loaded_signal.value + 1)
    assert analysis.alert_filter.value == ANA


def test_a_start_filter_set_before_the_layout_is_wired_is_resolved_when_it_is(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(1)                 # published, unfiltered, Bruno's
    analysis = AnalysisState()
    apply_start_filter(analysis, ANA)
    analysis_tile.wire_alert_navigation(SimpleNamespace(), s, analysis, SetContext(s),
                                        SimpleNamespace(v_model=None))
    assert s.alert_filter == ANA and s.actual_alert_id.value == 2


def test_another_sets_alerts_drop_the_filter_whatever_the_listener_order(tmp_path, monkeypatch):
    """Solara fires a reactive's listeners in no fixed order (they are kept
    in a set). The worst order is forced here: the tile's start-alert
    callback runs before ``AppState``'s own reset. The start alert still
    ignores a filter made on another set, so it never saves over the new
    set's position (``remember_position``, as ``view_actual_alert`` does)."""
    s = _state(tmp_path, monkeypatch)
    p = s.project.value
    p.alert_sets["y"] = p.alert_sets["x"].model_copy(update={"id": "y", "last_alert_index": 3})  # Bruno's
    ctx = SetContext(s)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(0)
    s.actual_alert_id.subscribe(lambda i: ctx.remember_position(i) if i >= 0 else None)

    subscribe = s.alerts_gdf.subscribe
    held = []
    monkeypatch.setattr(s.alerts_gdf, "subscribe", held.append, raising=False)
    analysis = AnalysisState()
    s.bind_alert_filter(analysis.alert_filter)
    assert len(held) == 1                    # AppState's own reset, held back
    subscribe(lambda g: (analysis_tile.select_start_alert(s, ctx), held[0](g)))
    analysis.alert_filter.set(ANA)

    s.update_project("d", lambda live: setattr(live, "active_set", "y"))
    assert set_store.publish_set_alerts(s, "d", s.project.value.alert_sets["y"], _six(set_id="y"))
    assert analysis.alert_filter.value is None
    assert s.project.value.alert_sets["y"].last_alert_index == 3
    assert s.actual_alert_id.value == 3


def test_a_publish_keeps_a_saved_position_the_filter_shows(tmp_path, monkeypatch):
    p, s_, store = _project_with_set(tmp_path, monkeypatch)
    p.alert_sets["s"].last_alert_index = 4
    state = _session(p)
    analysis = AnalysisState()
    state.bind_alert_filter(analysis.alert_filter)
    apply_start_filter(analysis, ANA)
    assert set_store.publish_set_alerts(state, "d", s_, _six(set_id="s"))
    assert state.actual_alert_id.value == 4


def test_overview_go_clears_a_filter_that_hides_the_alert(tmp_path, monkeypatch):
    p, s_, store = _project_with_set(tmp_path, monkeypatch)
    state = _session(p)
    analysis = AnalysisState()
    state.bind_alert_filter(analysis.alert_filter)
    state.alerts_gdf.set(_six(set_id="s"))
    analysis.alert_filter.set(ANA)

    assert set_store.go_to_alert(state, "d", "s", 2, 2)  # Ana's: the filter stays
    assert analysis.alert_filter.value == ANA and state.actual_alert_id.value == 2
    assert set_store.go_to_alert(state, "d", "s", 3, 3)  # Bruno's: the filter goes
    assert analysis.alert_filter.value is None and state.actual_alert_id.value == 3


# ---- the tile: the start alert and a filter applied on screen ----------------------

def test_the_tiles_start_alert_and_a_new_filter_follow_the_resolver(tmp_path, monkeypatch):
    s = _state(tmp_path, monkeypatch)
    s.project.value.alert_sets["x"].last_alert_index = 3  # Bruno's
    analysis = AnalysisState()
    analysis_tile.wire_alert_navigation(SimpleNamespace(), s, analysis, SetContext(s),
                                        SimpleNamespace(v_model=None))
    assert s.alert_filter_reactive is analysis.alert_filter

    s.alerts_gdf.set(_six())
    assert s.actual_alert_id.value == 3      # no filter: the saved position
    analysis.alert_filter.set(ANA)
    assert s.actual_alert_id.value == 2      # on screen: Ana's first unreviewed
    analysis.alert_filter.set(None)
    assert s.actual_alert_id.value == 2      # clearing moves nothing
    analysis.alert_filter.set(AlertFilter("evaluator", frozenset({"Carla"})))
    assert s.actual_alert_id.value == 2      # nothing matches: stays


# ---- block A: the counter, the jump menu, Next unreviewed ---------------------------

def _block_a(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    field = v.TextField(v_model=None)
    card = context.build_context(s, field)
    s.alerts_gdf.set(_six())
    return s, analysis, card, field


def test_the_counter_counts_the_matches_with_the_total_as_a_hint(tmp_path, monkeypatch):
    s, analysis, card, _ = _block_a(tmp_path, monkeypatch)
    counter, tally, bar = context.queue_widgets(card)
    s.actual_alert_id.set(2)
    assert counter.children == ["3 / 6"]

    analysis.alert_filter.set(ANA)
    assert counter.children == ["2 / 3"]
    assert tally.children == ["1 reviewed · 2 left · filtered · 6 total"]
    assert bar.value == pytest.approx(100.0 / 3)
    s.actual_alert_id.set(3)
    assert counter.children == ["– / 3"]

    analysis.alert_filter.set(None)
    assert counter.children == ["4 / 6"] and tally.children == ["2 reviewed · 4 left"]


def test_no_match_says_so_and_disables_the_navigation(tmp_path, monkeypatch):
    s, analysis, card, _ = _block_a(tmp_path, monkeypatch)
    prev, nxt, *_, unreviewed, _growth = context.nav_widgets(card)
    counter, tally, _ = context.queue_widgets(card)
    s.actual_alert_id.set(1)
    analysis.alert_filter.set(AlertFilter("evaluator", frozenset({"Carla"})))
    assert counter.children == ["No alerts match this filter"]
    assert tally.children == ["filtered · 6 total"]
    assert prev.disabled and nxt.disabled and unreviewed.disabled
    assert s.actual_alert_id.value == 1
    analysis.alert_filter.set(None)
    assert not prev.disabled and not nxt.disabled


def test_the_nav_buttons_follow_the_filter(tmp_path, monkeypatch):
    s, analysis, card, _ = _block_a(tmp_path, monkeypatch)
    prev, nxt, *_ = context.nav_widgets(card)
    s.actual_alert_id.set(0)
    analysis.alert_filter.set(ANA)
    nxt.fire_event("click", None)
    assert s.actual_alert_id.value == 2
    prev.fire_event("click", None)
    assert s.actual_alert_id.value == 0


def test_the_jump_refuses_a_number_outside_the_filter(tmp_path, monkeypatch):
    s, analysis, card, field = _block_a(tmp_path, monkeypatch)
    _, _, _, menu, go, _, _ = context.nav_widgets(card)
    refusal = context.jump_refusal(card)
    s.actual_alert_id.set(0)
    analysis.alert_filter.set(ANA)

    menu.v_model = True
    field.v_model = 4                      # alert 4 is row 3: Bruno's
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 0 and menu.v_model is True
    assert refusal.children == ["Alert 4 is not in the current filter"]
    assert "display: none" not in refusal.style_

    field.v_model = 5                      # row 4: Ana's
    go.fire_event("click", None)
    assert s.actual_alert_id.value == 4 and menu.v_model is False

    menu.v_model = True                    # reopening forgets the refusal
    assert refusal.children == [] and "display: none" in refusal.style_
    field.v_model = 99                     # clamps to row 5: nobody's
    field.fire_event("keydown.enter", None)
    assert s.actual_alert_id.value == 4 and refusal.children == ["Alert 6 is not in the current filter"]


def test_a_refusal_on_screen_follows_the_language(tmp_path, monkeypatch):
    from gui.tile.analysis.labels import Labels

    s, analysis = _bound(tmp_path, monkeypatch)
    field, labels = v.TextField(v_model=None), Labels()
    card = context.build_context(s, field, labels=labels)
    s.alerts_gdf.set(_six())
    _, _, _, menu, go, _, _ = context.nav_widgets(card)
    refusal = context.jump_refusal(card)
    s.actual_alert_id.set(0)
    analysis.alert_filter.set(ANA)
    menu.v_model = True
    field.v_model = 4
    go.fire_event("click", None)
    set_locale("fr")
    labels.run()
    assert menu.v_model is True and field.v_model == 4
    assert refusal.children == [t("analysis_tile.filter.not_in_filter", number=format_number(4))]
    assert "display: none" not in refusal.style_
    menu.v_model = False
    menu.v_model = True                     # reopening still forgets it
    labels.run()
    assert refusal.children == [] and "display: none" in refusal.style_


def test_next_unreviewed_keeps_to_the_matches(tmp_path, monkeypatch):
    s, analysis, card, _ = _block_a(tmp_path, monkeypatch)
    *_, menu, _, unreviewed, _growth = context.nav_widgets(card)
    s.actual_alert_id.set(2)
    analysis.alert_filter.set(ANA)
    menu.v_model = True
    unreviewed.fire_event("click", None)
    assert s.actual_alert_id.value == 4      # not Bruno's 3
    unreviewed.fire_event("click", None)
    assert s.actual_alert_id.value == 2      # wraps round, past the reviewed 0
    s.alerts_gdf.set(_six().assign(status=["Confirmed", "", "Not reviewed", "", "Confirmed", ""]))
    assert unreviewed.disabled, "Ana has nothing else left to review"


# ---- the footer: Save & next -----------------------------------------------------------

def test_save_and_next_advances_to_the_next_match(tmp_path, monkeypatch):
    s, analysis = _bound(tmp_path, monkeypatch)
    footer = build_footer(lambda button=None, then=None: then() if then else None,
                          v.Html(tag="div"), s, analysis)
    save_next, _, _ = footer_widgets(footer.root)
    s.alerts_gdf.set(_six())
    s.actual_alert_id.set(0)
    analysis.alert_filter.set(ANA)
    save_next.fire_event("click", None)
    assert s.actual_alert_id.value == 2


def test_next_growth_button_disabled_without_growth(tmp_path, monkeypatch):
    s, analysis, card, _ = _block_a(tmp_path, monkeypatch)
    *_, menu, _, _, growth = context.nav_widgets(card)
    s.actual_alert_id.set(0)
    assert growth.disabled, "an old set has no growth alert"
    gdf = _six()
    gdf["grows_from"] = [[], ["old#3@20250101000000000000"], [], [], ["old#3@20250101000000000000"], []]
    gdf["below_min"] = False
    s.alerts_gdf.set(gdf)
    s.actual_alert_id.set(0)
    assert not growth.disabled
    menu.v_model = True
    growth.fire_event("click", None)
    assert s.actual_alert_id.value == 1 and menu.v_model is False
    analysis.alert_filter.set(ANA)            # Ana's rows: 0, 2, 4 -> only 4 grows
    s.actual_alert_id.set(0)
    growth.fire_event("click", None)
    assert s.actual_alert_id.value == 4
    analysis.alert_filter.set(AlertFilter("evaluator", frozenset({"Bruno"})))
    s.actual_alert_id.set(1)
    assert growth.disabled, "no growth alert among Bruno's"
