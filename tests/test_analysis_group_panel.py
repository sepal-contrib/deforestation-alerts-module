"""The Analysis panel under group review (spec 2026-10-26 §5.3, §6.3, §6.4):
an opened grouped alert is seeded from its main, the dock's Save counts the
group and asks first over the rows it changes, *Remove from group*, what
happens when a save lands, and the dock's prompt over a tick.

The harness is ``test_analysis_group_save``'s (a real active set, its store
in ``tmp_path``, the FAO/GAUL lookups stood in for) under the panel
``analysis_tile.build_controls`` builds; the workers run inline."""

import threading
from types import SimpleNamespace

import ipyvuetify as v
import numpy as np
import pandas as pd
import pytest
import solara
from pysepal.i18n import set_locale
from shapely.geometry import MultiPolygon, box

from deforestation_alerts.project import review_groups
from gui.i18n import t
from gui.tile.analysis import analysis_tile, export, nearby, original_alert
from gui.tile.analysis.context import status_label_key
from gui.tile.analysis.footer import affected_line, build_footer, footer_widgets, prompt_widgets
from gui.tile.analysis.state import AnalysisState, capture_alert, toggle_selection
from gui.widget.buttons import label_of
from tests._pane_helpers import make_panes
from tests.test_analysis_group_save import _alerts as _store_alerts
from tests.test_analysis_group_save import _groups, _setup, _stored

L1, L2, L3 = "old#1@20250101000000000000", "old#2@20250101000000000000", "old#3@20250101000000000000"
NR = "Not reviewed"
CONFIRMED = "Confirmed"
HIDDEN = "display: none;"


@pytest.fixture(autouse=True)
def _english():
    set_locale("en")
    yield
    set_locale("en")


def _alerts(**columns):
    """``test_analysis_group_save``'s four alerts, with the columns the
    panel's facts read."""
    facts = {"alert_sources": ["GLAD-L"] * 4, "alert_date_min": [2026.010] * 4, "alert_date_max": [2026.020] * 4}
    return _store_alerts(**{**facts, **columns})


def _links(*values):
    return [np.array(list(v), dtype=object) for v in values]


def _grouped(**columns):
    """Four alerts, ids 10-13: #1 (main) and #2, #3 a confirmed group of
    three, #4 unreviewed. The main stores the review the members copy, but
    the members here hold other values, so a seeding read off the wrong row
    shows."""
    polygon = MultiPolygon([box(0.2, 0.2, 0.8, 0.8)])
    base = dict(status=[CONFIRMED, CONFIRMED, CONFIRMED, NR],
                description=["Agriculture", "Mining", "Mining", None],
                alert_polygon=[polygon, None, None, None],
                grows_from=_links([L1], [L2], [], []),
                grows_from_auto=_links([L1], [L2], [L3, L1], []),
                review_group=pd.array([10, 10, 10, None], dtype="Int64"))
    base.update(columns)
    return _alerts(**base)


class _Toasts(list):
    def info(self, message):
        self.append(message)


def _panel(tmp_path, monkeypatch, *, alerts=None, grouping=True):
    h = _setup(tmp_path, monkeypatch, grouping=grouping, alerts=alerts)
    from tests.test_feedback_notifications import _record_feedback

    _record_feedback(monkeypatch, export)
    monkeypatch.setattr(export, "spawn_in_context", lambda target, args=(), **k: target(*args))
    h.nearby_runs, h.earlier_runs = [], []
    monkeypatch.setattr(nearby, "refresh_in_background", lambda *a: h.nearby_runs.append(a))
    monkeypatch.setattr(nearby.earlier, "refresh_in_background", lambda *a: h.earlier_runs.append(a))
    monkeypatch.setattr(analysis_tile, "refresh_in_background", lambda *a: h.earlier_runs.append(a))
    monkeypatch.setattr(analysis_tile, "alert_extent", lambda *a: (h.widgets.actual_bb, None))
    h.toasts = _Toasts()
    monkeypatch.setattr(analysis_tile, "current_notifier", lambda: h.toasts)
    h.analysis = AnalysisState()
    h.widgets = SimpleNamespace(panes=make_panes(), imagery_ctx=solara.reactive(None),
                                actual_bb=SimpleNamespace(geometry=lambda: None), actual_alert_grid=None)
    h.id_field = v.TextField(v_model=None)
    h.controls = analysis_tile.build_controls(
        widgets=h.widgets, app_state=h.s, analysis=h.analysis,
        aux_model=h.ctx, app_tile_model=h.ctx, id_field=h.id_field)
    h.sections = analysis_tile.panel_sections(h.controls)
    h.footer, h.near = h.sections.footer, h.sections.nearby
    h.reloads = []
    h.analysis.reload_alert.subscribe(lambda value: h.reloads.append(value))
    return h


def _open(h, position):
    """Open the alert at ``position`` as a navigation does."""
    h.s.actual_alert_id.set(position)
    analysis_tile.view_actual_alert(h.widgets, h.s, h.analysis, h.ctx, h.id_field)


def _press_save(h):
    h.footer.save.fire_event("click", None)


def _dialog_lines(confirm):
    return [line.children[0] for line in confirm.body.children]


# ---------------------------------------------------------------------------
# Seeding (§6.3)
# ---------------------------------------------------------------------------
def test_opening_a_member_seeds_from_the_main(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 1)
    a = h.analysis
    assert a.verdict.value == CONFIRMED
    assert a.answers.value == {"loss_driver": ["Agriculture"]}
    assert a.links.value == (L1,)
    drawn = a.defo_layer.value
    assert drawn is not None and drawn.geometry.iloc[0].equals(box(0.2, 0.2, 0.8, 0.8))
    assert a.selection.value == frozenset({1, 2})
    assert a.dirty.value is False and a.edit_rev.value == 0
    # the main opens on the same review and the same ticks
    _open(h, 0)
    assert (a.answers.value, a.links.value, a.selection.value) == (
        {"loss_driver": ["Agriculture"]}, (L1,), frozenset({1, 2}))


def test_a_single_row_seeds_from_itself(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 3)
    assert h.analysis.verdict.value is None and h.analysis.links.value == ()
    assert h.analysis.selection.value == frozenset()


def test_grouping_off_seeds_the_row_itself(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped(), grouping=False)
    _open(h, 1)
    assert h.analysis.answers.value == {"loss_driver": ["mining"]}  # its own code
    assert h.analysis.links.value == (L2,) and h.analysis.auto_links.value == (L2,)
    assert h.analysis.selection.value == frozenset() and h.analysis.defo_layer.value is None


def test_auto_links_are_the_groups_union(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 2)
    assert h.analysis.auto_links.value == (L1, L2, L3)


# ---------------------------------------------------------------------------
# The banner, the facts and the Save label
# ---------------------------------------------------------------------------
def test_the_banner_names_the_group_and_remove_shows_on_members(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 0)
    assert h.near.banner.style_ == nearby.BANNER_SHOWN
    assert h.near.banner_text.children == ["Reviewed as a group of 3 (main alert #1)"]
    assert h.near.remove_btn.style_ == HIDDEN  # the main is not removed from its own group
    _open(h, 2)
    assert h.near.remove_btn.style_ != HIDDEN and label_of(h.near.remove_btn) == "Remove from group"
    _open(h, 3)
    assert h.near.banner.style_ == HIDDEN and h.near.remove_btn.style_ == HIDDEN


def test_the_footer_label_counts_the_group(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    _open(h, 0)
    assert label_of(h.footer.save) == "Save"
    toggle_selection(h.analysis, 1)
    toggle_selection(h.analysis, 2)
    assert label_of(h.footer.save) == "Save for 3 alerts"
    h.near.clear.fire_event("click", None)
    assert label_of(h.footer.save) == "Save"


def test_the_footer_label_builds_no_plan(tmp_path, monkeypatch):
    """Review M6: the label follows every selection change, which
    ``reset_alert`` and the after-save reseed make inside ``_alert_lock``:
    it counts from the selection, with no capture or plan built there. A
    ticked main of another group still brings that whole group."""
    alerts = _alerts(status=[NR, CONFIRMED, CONFIRMED, NR],
                     review_group=pd.array([None, 11, 11, None], dtype="Int64"))
    h = _panel(tmp_path, monkeypatch, alerts=alerts)
    _open(h, 0)
    plans = []
    real = review_groups.plan_group
    monkeypatch.setattr(review_groups, "plan_group", lambda *a, **k: plans.append(a) or real(*a, **k))
    toggle_selection(h.analysis, 1)  # #2, the main of #2-#3
    assert label_of(h.footer.save) == "Save for 3 alerts"
    toggle_selection(h.analysis, 3)
    assert label_of(h.footer.save) == "Save for 4 alerts"
    assert plans == []


# ---------------------------------------------------------------------------
# The confirmation (§6.4)
# ---------------------------------------------------------------------------
def _footer_alone(tmp_path, monkeypatch, alerts):
    h = _setup(tmp_path, monkeypatch, grouping=True, alerts=alerts)
    h.analysis = AnalysisState()
    h.analysis.verdict.set(CONFIRMED)
    h.calls = []
    widgets = SimpleNamespace(actual_bb=None, actual_alert_grid=None)

    def run_save(button=None, then=None, *, alert=None):
        h.calls.append((button, alert))

    h.footer = build_footer(run_save, v.Html(tag="div"), h.s, h.analysis,
                            capture=lambda: capture_alert(h.analysis, widgets, h.s))
    return h


def test_the_confirmation_lists_the_plan(tmp_path, monkeypatch):
    """#1 and #2 are a group, #3 a reviewed single: saving #1 with #3 ticked
    and #2 unticked overwrites #3 and takes #2 out."""
    alerts = _alerts(status=[CONFIRMED, CONFIRMED, CONFIRMED, NR],
                     review_group=pd.array([10, 10, 12, None], dtype="Int64"))
    h = _footer_alone(tmp_path, monkeypatch, alerts)
    h.s.actual_alert_id.set(0)
    h.analysis.selection.set(frozenset({2}))
    confirm = h.footer.confirm
    status = t(status_label_key(CONFIRMED))

    h.footer.save.fire_event("click", None)
    assert confirm.dialog.v_model is True and h.calls == []
    assert _dialog_lines(confirm) == [
        f"#2 ({status}): leaves the group and goes back to not reviewed",
        f"#3 ({status}): its review is replaced",
    ]
    confirm.cancel.fire_event("click", None)
    assert confirm.dialog.v_model is False and h.calls == []

    h.footer.save.fire_event("click", None)
    confirm.ok.fire_event("click", None)
    assert confirm.dialog.v_model is False
    assert len(h.calls) == 1
    button, alert = h.calls[0]
    assert button is h.footer.save
    assert alert.plan.members == (2,) and alert.plan.detached == (1,) and alert.selection == frozenset({2})


def test_the_lines_name_moves_and_joins():
    gdf = _alerts(status=[CONFIRMED] * 4)
    status = t(status_label_key(CONFIRMED))
    moved = review_groups.Affected(1, CONFIRMED, review_groups.MOVED, 2)
    joined = review_groups.Affected(3, CONFIRMED, review_groups.JOINED, 3)
    assert affected_line(moved, gdf) == f"#2 ({status}): moves from group #3"
    assert affected_line(joined, gdf) == f"#4 ({status}): joins with group #4"


def test_no_confirmation_when_nothing_is_affected(tmp_path, monkeypatch):
    h = _footer_alone(tmp_path, monkeypatch, _alerts())
    h.s.actual_alert_id.set(0)
    h.analysis.selection.set(frozenset({1, 3}))
    h.footer.save.fire_event("click", None)
    assert h.footer.confirm.dialog.v_model is False
    assert len(h.calls) == 1 and h.calls[0][1].plan.members == (1, 3)


def test_without_a_capture_save_runs_as_before(tmp_path, monkeypatch):
    calls = []
    footer = build_footer(lambda button=None, then=None: calls.append((button, then)),
                          v.Html(tag="div"), _setup(tmp_path, monkeypatch).s, AnalysisState())
    footer.save.fire_event("click", None)
    assert calls == [(footer.save, None)]


# ---------------------------------------------------------------------------
# Remove from group (§5.4)
# ---------------------------------------------------------------------------
def test_remove_from_group_asks_then_saves(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 2)
    confirm = h.footer.confirm
    h.near.remove_btn.fire_event("click", None)
    assert confirm.dialog.v_model is True
    assert _dialog_lines(confirm) == [
        f"#3 ({t(status_label_key(CONFIRMED))}): leaves the group and goes back to not reviewed"]
    assert _groups(_stored(h)) == [10, 10, 10, None]  # nothing written before the OK

    reloads = len(h.reloads)
    confirm.ok.fire_event("click", None)
    stored = _stored(h)
    assert _groups(stored) == [10, 10, None, None]
    assert stored["status"].tolist() == [CONFIRMED, CONFIRMED, NR, NR]
    assert len(h.reloads) == reloads + 1  # the alert opens again, from the store
    assert h.toasts == []


def _dialog_labels(confirm):
    return confirm.dialog.children[0].children[0].children, label_of(confirm.ok)


def test_remove_from_group_asks_to_remove_not_to_save(tmp_path, monkeypatch):
    """Review M2: the removal's dialog reads *Remove from group* / *Remove*,
    and the save's own confirmation keeps its wording afterwards."""
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 2)
    confirm = h.footer.confirm
    h.near.remove_btn.fire_event("click", None)
    assert _dialog_labels(confirm) == (["Remove from group"], "Remove")
    confirm.cancel.fire_event("click", None)
    _open(h, 0)
    toggle_selection(h.analysis, 1)  # #2 leaves the group: the save asks first
    _press_save(h)
    assert confirm.dialog.v_model is True
    assert _dialog_labels(confirm) == ([t("analysis_tile.nearby.confirm_title")], t("analysis_tile.nearby.confirm_ok"))


def test_the_runner_holds_remove_from_group_during_a_save(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 2)
    seen = []
    real = export._save_attributes

    def _watch(*a, **k):
        seen.append(h.near.remove_btn.disabled)
        return real(*a, **k)

    monkeypatch.setattr(export, "_save_attributes", _watch)
    _press_save(h)
    h.footer.confirm.ok.fire_event("click", None)
    assert seen == [True] and not h.near.remove_btn.disabled


def _no_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(export, "_save_attributes", lambda *a, **k: calls.append(a) or True)
    return calls


def test_remove_does_nothing_while_grouping_is_off(tmp_path, monkeypatch):
    """A plan-less capture would be a full save of the verdict on screen."""
    h = _panel(tmp_path, monkeypatch, alerts=_grouped(), grouping=False)
    _open(h, 2)
    assert h.near.remove_btn.style_ == HIDDEN
    calls = _no_runner(monkeypatch)
    h.near.run_remove()
    assert calls == [] and h.footer.confirm.dialog.v_model is False


def test_remove_does_nothing_on_a_row_that_is_not_a_member(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    calls = _no_runner(monkeypatch)
    for position in (0, 3):  # the main, a single
        _open(h, position)
        h.near.run_remove()
    assert calls == [] and h.footer.confirm.dialog.v_model is False


# ---------------------------------------------------------------------------
# After a save (§5.3)
# ---------------------------------------------------------------------------
def _captured(h, position, selection):
    h.s.actual_alert_id.set(position)
    h.analysis.verdict.set(CONFIRMED)
    h.analysis.selection.set(frozenset(selection))
    return capture_alert(h.analysis, h.widgets, h.s)


def test_a_member_on_screen_reloads_when_its_group_save_lands(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    on_saved = analysis_tile.make_after_save(h.widgets, h.s, h.analysis)
    alert = _captured(h, 0, {1})
    # the reviewer moved on to #2 while the save ran
    h.s.actual_alert_id.set(1)
    h.analysis.reset_alert()
    reloads = len(h.reloads)
    on_saved(alert, False)
    assert len(h.reloads) == reloads + 1
    assert h.toasts == ["Alert #2 was saved as part of a group"]


def test_a_save_landing_on_an_untouched_alert_does_nothing(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    on_saved = analysis_tile.make_after_save(h.widgets, h.s, h.analysis)
    alert = _captured(h, 0, {1})
    # Discard, then another alert, outside the plan
    h.s.actual_alert_id.set(3)
    h.analysis.reset_alert()
    h.analysis.selection.set(frozenset({2}))
    reloads = len(h.reloads)
    on_saved(alert, False)
    assert len(h.reloads) == reloads and h.toasts == []
    assert h.analysis.selection.value == frozenset({2})


def test_a_save_landing_in_another_set_does_nothing(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    on_saved = analysis_tile.make_after_save(h.widgets, h.s, h.analysis)
    alert = _captured(h, 0, {1})
    monkeypatch.setattr(analysis_tile, "active_set", lambda p: SimpleNamespace(id="another"))
    h.analysis.selection.set(frozenset({3}))  # whatever the screen holds now
    reloads, runs = len(h.reloads), len(h.nearby_runs)
    on_saved(alert, False)  # same visit
    assert h.analysis.selection.value == frozenset({3}) and len(h.nearby_runs) == runs
    h.s.actual_alert_id.set(1)
    h.analysis.reset_alert()
    on_saved(alert, False)  # another visit, of a row of the plan
    assert len(h.reloads) == reloads and h.toasts == []


def test_a_save_without_a_plan_does_nothing(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts(), grouping=False)
    on_saved = analysis_tile.make_after_save(h.widgets, h.s, h.analysis)
    alert = _captured(h, 0, ())
    assert alert.plan is None
    h.s.actual_alert_id.set(1)
    h.analysis.reset_alert()
    reloads = len(h.reloads)
    on_saved(alert, False)
    assert len(h.reloads) == reloads and h.toasts == []


def test_a_group_save_reseeds_the_selection_and_looks_again(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    _open(h, 0)
    h.analysis.verdict.set(CONFIRMED)
    toggle_selection(h.analysis, 2)
    runs = (len(h.nearby_runs), len(h.earlier_runs))
    _press_save(h)
    assert _groups(_stored(h)) == [10, None, 10, None]
    assert h.analysis.selection.value == frozenset({2}) and h.analysis.dirty.value is False
    assert len(h.nearby_runs) > runs[0] and len(h.earlier_runs) > runs[1]
    assert h.near.banner_text.children == ["Reviewed as a group of 2 (main alert #1)"]
    # the Original alert outlines follow the saved group
    spec = h.widgets.panes.spec(original_alert.ORIGINAL_ID)
    assert [f["properties"]["position"] for f in spec["data"]["features"]] == [0, 2]


def test_a_tick_during_the_save_survives_it(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    _open(h, 0)
    h.analysis.verdict.set(CONFIRMED)
    toggle_selection(h.analysis, 1)
    real = export._save_attributes

    def _tick_meanwhile(*a, **k):
        wrote = real(*a, **k)
        toggle_selection(h.analysis, 2)  # a tick after the click
        return wrote

    monkeypatch.setattr(export, "_save_attributes", _tick_meanwhile)
    _press_save(h)
    assert _groups(_stored(h)) == [10, 10, None, None]
    assert h.analysis.selection.value == frozenset({1, 2})
    assert h.analysis.dirty.value is True
    assert label_of(h.footer.save) == "Save for 3 alerts"


def test_auto_links_after_a_group_save_are_the_union(tmp_path, monkeypatch):
    """After a group save, ``auto_links`` is the union of what Integrate
    found for the group's rows."""
    h = _panel(tmp_path, monkeypatch, alerts=_alerts(grows_from=_links([], [], [], []),
                                                     grows_from_auto=_links([L2], [L1], [L3], [])))
    _open(h, 0)
    assert h.analysis.auto_links.value == (L2,)
    h.analysis.verdict.set(CONFIRMED)
    toggle_selection(h.analysis, 1)
    _press_save(h)
    assert h.analysis.auto_links.value == (L1, L2)


def test_a_tick_makes_next_ask_first(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    _open(h, 0)
    _save, nxt, _ = footer_widgets(h.footer.root)
    toggle_selection(h.analysis, 1)
    nxt.fire_event("click", None)
    prompt, _discard, _stay = prompt_widgets(h.footer.root)
    assert "display: none" not in prompt.style_
    assert h.s.actual_alert_id.value == 0


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------
def _lock_free_on_reload(h):
    """Record, at every reload, whether another thread could take the alert
    lock: the reload runs the whole per-alert orchestration, which must not
    hold it."""
    free = []

    def _probe(*_):
        got = []

        def _try():
            if h.analysis._alert_lock.acquire(timeout=1):
                got.append(True)
                h.analysis._alert_lock.release()

        thread = threading.Thread(target=_try)
        thread.start()
        thread.join(5)
        free.append(bool(got))

    h.analysis.reload_alert.subscribe(_probe)
    return free


def test_the_reload_of_another_visit_runs_outside_the_alert_lock(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    free = _lock_free_on_reload(h)
    on_saved = analysis_tile.make_after_save(h.widgets, h.s, h.analysis)
    alert = _captured(h, 0, {1})
    h.s.actual_alert_id.set(1)
    h.analysis.reset_alert()
    on_saved(alert, False)
    assert free == [True]


def test_the_reload_after_a_removal_runs_outside_the_alert_lock(tmp_path, monkeypatch):
    h = _panel(tmp_path, monkeypatch, alerts=_grouped())
    _open(h, 2)
    free = _lock_free_on_reload(h)
    h.near.remove_btn.fire_event("click", None)
    h.footer.confirm.ok.fire_event("click", None)
    assert free == [True]


def test_the_save_label_follows_a_new_table(tmp_path, monkeypatch):
    """Ticking another group's main brings its whole group: when that group
    changes with the selection unchanged, the label follows the table."""
    h = _panel(tmp_path, monkeypatch, alerts=_alerts())
    _open(h, 0)
    toggle_selection(h.analysis, 2)
    assert label_of(h.footer.save) == "Save for 2 alerts"
    gdf = h.s.alerts_gdf.value.copy()
    gdf["status"] = [NR, NR, CONFIRMED, CONFIRMED]
    gdf["review_group"] = pd.array([None, None, 12, 12], dtype="Int64")
    h.s.alerts_gdf.set(gdf)  # #3 is now the main of a group of two
    assert label_of(h.footer.save) == "Save for 3 alerts"


def test_a_capture_that_fails_toasts_and_does_not_save(tmp_path, monkeypatch):
    from gui.tile.analysis import footer as footer_mod

    errors = []
    monkeypatch.setattr(footer_mod, "current_notifier", lambda: SimpleNamespace(error=errors.append))
    calls = []

    def _broken():
        raise RuntimeError("no table")

    footer = build_footer(lambda button=None, then=None, *, alert=None: calls.append(alert),
                          v.Html(tag="div"), _setup(tmp_path, monkeypatch).s, AnalysisState(), capture=_broken)
    footer.save.fire_event("click", None)
    assert calls == [] and footer.confirm.dialog.v_model is False
    assert errors == [t("analysis_tile.export_labels.save_capture_failed", error="no table")]
