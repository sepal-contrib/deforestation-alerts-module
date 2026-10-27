"""The Analysis save writes a group plan (spec 2026-10-26 group review §4.3,
§5): one store write for every row of the plan, the area rules every save
follows, the refusals, *Remove from group*, and what the save runner hands
``on_saved``. The harness is ``test_set_context``'s: a real active set, its
store in ``tmp_path``, the FAO/GAUL lookups stood in for."""

import threading
from datetime import datetime
from types import SimpleNamespace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project import review_groups
from deforestation_alerts.project.models import ReviewCopy
from gui.scripts.set_context import SetContext
from gui.tile.analysis import export, review_save
from gui.tile.analysis.review_save import SaveRefused
from gui.tile.analysis.state import AnalysisState, capture_alert, toggle_selection, touch
from tests.test_set_context import _loaded, _review_alerts, _state

IDS = [10, 11, 12, 13]


class _Lookups:
    """Stands in for ``review_save.ee``: every attribute and call is itself,
    the calls recorded by name (``Rectangle`` gets the lookup's bounds)."""

    def __init__(self):
        self.seen = []
        self.calls = 0
        self._name = None

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self._name = name
        return self

    def __call__(self, *args, **kwargs):
        self.seen.append((self._name, args))
        return self

    def getInfo(self):  # noqa: N802 - Earth Engine's name
        self.calls += 1
        return {"admin1": "Brasil", "admin2": "Acre", "admin3": "Rio Branco"}


def _alerts(n=4, ids=True, **columns):
    gdf = _review_alerts("x", n=n)
    if ids:
        gdf["alert_id"] = IDS[:n]
    gdf["area_ha"] = [review_groups.polygon_area_ha(g) for g in gdf.geometry]
    for name, values in columns.items():
        gdf[name] = values
    return gdf


def _setup(tmp_path, monkeypatch, *, grouping=False, alerts=None, copy_of=None):
    s = _state(tmp_path, monkeypatch)
    s.project.value.group_review.enabled = grouping
    if copy_of is not None:
        s.project.value.review_copy = ReviewCopy(
            evaluator=copy_of, evaluator_index=1, set_id="x", split_id="s", set_fingerprint="f",
            origin_project="o", imported_at=datetime.now())
    ctx = SetContext(s)
    gdf, store = _loaded(s, alerts if alerts is not None else _alerts())
    lookups = _Lookups()
    monkeypatch.setattr("gui.tile.analysis.review_save.ee", lookups)
    return SimpleNamespace(s=s, ctx=ctx, store=store, lookups=lookups)


def _drawing(*polys):
    return gpd.GeoDataFrame(geometry=list(polys), crs="EPSG:4326")


def _analysis(verdict="Confirmed", selection=(), defo=None):
    analysis = AnalysisState()
    analysis.verdict.set(verdict)
    analysis.answers.set({"loss_driver": ["Agriculture"]})
    analysis.before_info.set(["Planet NICFI", 1, None, "before", "_b"])
    analysis.after_info.set(["Planet NICFI", 2, None, "after", "_a"])
    analysis.selection.set(frozenset(selection))
    analysis.defo_layer.set(defo)
    return analysis


WIDGETS = SimpleNamespace(actual_bb=SimpleNamespace(geometry=lambda: None))


def _save(h, alert_id=0, selection=(), *, remove=False, analysis=None, **kw):
    """Press Save (or Remove from group) on ``alert_id``, for real."""
    analysis = analysis or _analysis(selection=selection, **kw)
    h.s.actual_alert_id.set(alert_id)
    alert = capture_alert(analysis, WIDGETS, h.s, remove=remove)
    return export._save_attributes(WIDGETS, h.s, analysis, h.ctx, alert=alert), alert


def _stored(h):
    return gpd.read_parquet(h.store)


def _groups(gdf):
    return [None if pd.isna(v) else int(v) for v in gdf[review_groups.COLUMN]]


# ---------------------------------------------------------------------------
# Grouping off: today's save, but for the two area fixes (§4.3)
# ---------------------------------------------------------------------------
def test_grouping_off_saves_exactly_as_before(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)
    wrote, alert = _save(h)
    assert wrote is True and alert.plan is None and alert.grouping is None
    stored = _stored(h)
    assert review_groups.COLUMN not in stored.columns
    assert review_groups.COLUMN not in h.s.alerts_gdf.value.columns
    row = stored.iloc[0]
    assert row["status"] == "Confirmed" and row["description"] == "Agriculture"
    assert (row["admin1"], row["admin2"], row["admin3"]) == ("Brasil", "Acre", "Rio Branco")
    assert (row["before_img"], row["after_img"]) == ("before", "after")
    assert row["alert_polygon"] is None
    assert list(row["grows_from"]) == []
    assert row["area_ha"] == pytest.approx(review_groups.polygon_area_ha(box(0, 0, 1, 1)))
    assert stored["status"].tolist()[1:] == ["Not reviewed"] * 3
    # the lookup is the alert's own box, as before
    assert not any(name == "Rectangle" for name, _ in h.lookups.seen)


def test_a_drawing_with_overlapping_features_measures_the_union(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)
    a, b = box(0.1, 0.1, 0.3, 0.3), box(0.2, 0.1, 0.4, 0.3)
    _save(h, defo=_drawing(a, b))
    area = _stored(h).iloc[0]["area_ha"]
    assert area == pytest.approx(review_groups.polygon_area_ha(a.union(b)))
    assert area < review_groups.polygon_area_ha(a) + review_groups.polygon_area_ha(b) - 1


def test_a_save_without_a_drawing_restores_the_integrate_area(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch)
    integrate = h.s.alerts_gdf.value["area_ha"].iloc[0]
    _save(h, defo=_drawing(box(0.1, 0.1, 0.2, 0.2)))
    assert _stored(h).iloc[0]["area_ha"] < integrate / 10
    _save(h)
    assert _stored(h).iloc[0]["area_ha"] == pytest.approx(integrate)
    assert h.s.alerts_gdf.value["area_ha"].iloc[0] == pytest.approx(integrate)


# ---------------------------------------------------------------------------
# Grouping on: the plan, in one store write
# ---------------------------------------------------------------------------
def test_the_group_column_stays_int64_across_two_saves(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    for selection in ({1}, {1, 2}):
        _save(h, 0, selection)
        assert str(h.s.alerts_gdf.value[review_groups.COLUMN].dtype) == "Int64"
        assert str(_stored(h)[review_groups.COLUMN].dtype) == "Int64"
    assert _groups(_stored(h)) == [10, 10, 10, None]


def test_a_group_save_writes_every_row_in_one_store_write(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    writes = []
    real = review_save.write_store
    monkeypatch.setattr(review_save, "write_store", lambda *a, **k: (writes.append(1), real(*a, **k))[1])
    wrote, alert = _save(h, 0, {1, 2})
    assert wrote is True and alert.plan.members == (1, 2)
    assert len(writes) == 1
    stored = _stored(h)
    assert stored["status"].tolist() == ["Confirmed"] * 3 + ["Not reviewed"]
    assert _groups(stored) == [10, 10, 10, None]
    assert stored["description"].tolist()[:3] == ["Agriculture"] * 3
    assert stored["admin1"].tolist()[:3] == ["Brasil"] * 3
    review_groups.check_groups(stored)
    review_groups.check_groups(h.s.alerts_gdf.value)
    assert _groups(h.s.alerts_gdf.value) == [10, 10, 10, None]


def test_a_group_of_one_is_written_when_grouping_is_on(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    wrote, alert = _save(h, 1)
    assert wrote is True and alert.plan.main == 1 and alert.plan.members == ()
    assert _groups(_stored(h)) == [None, 11, None, None]


def _required(h):
    q = h.s.project.value.questions[0]
    h.s.project.value.questions = [q.model_copy(update={"required": True})]


REFUSALS = {
    "missing-verdict": ("analysis_tile.questionarie.confirmation_missing", {"verdict": None}),
    "required-answer": (review_save.REQUIRED_MISSING, {}),
    "unapplied-drawing": ("analysis_tile.edition_labels.unapplied_drawing", {}),
    "false-positive-drawn": ("analysis_tile.questionarie.false_positive_drawn",
                             {"verdict": "False Positive", "defo": _drawing(box(0.1, 0.1, 0.2, 0.2))}),
    "mixed-evaluators": ("analysis_tile.nearby.mixed_evaluators", {}),
    "foreign-rows": ("analysis_tile.nearby.foreign_rows", {}),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_each_refusal_writes_no_row(tmp_path, monkeypatch, case):
    key, kw = REFUSALS[case]
    alerts, copy_of = _alerts(), None
    if case == "mixed-evaluators":
        alerts = _alerts(evaluator=["ana", "bob", "ana", "ana"])
    if case == "foreign-rows":
        alerts, copy_of = _alerts(evaluator=["bob"] * 4), "ana"
    h = _setup(tmp_path, monkeypatch, grouping=True, alerts=alerts, copy_of=copy_of)
    if case == "required-answer":
        _required(h)
    analysis = _analysis(selection={1}, **kw)
    if case == "required-answer":
        analysis.answers.set({})
    if case == "unapplied-drawing":
        analysis.drawing_unapplied.set(True)
    before = h.store.read_bytes()
    live = h.s.alerts_gdf.value.copy()
    with pytest.raises(SaveRefused) as refused:
        _save(h, 0, analysis=analysis)
    assert refused.value.key == key
    assert h.store.read_bytes() == before
    assert h.s.alerts_gdf.value["status"].tolist() == live["status"].tolist()
    assert review_groups.COLUMN not in h.s.alerts_gdf.value.columns


def test_the_store_changed_guard_refuses_a_group_save(tmp_path, monkeypatch):
    from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet

    h = _setup(tmp_path, monkeypatch, grouping=True)
    save_gdf_to_parquet(_alerts(n=3), str(h.store))  # another writer's store
    before = h.store.read_bytes()
    with pytest.raises(SaveRefused) as refused:
        _save(h, 0, {1})
    assert refused.value.key == "analysis_tile.export_labels.save_store_changed"
    assert h.store.read_bytes() == before


def test_the_admin_lookup_spans_the_whole_group(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    _save(h, 0, {2})
    assert [args for name, args in h.lookups.seen if name == "Rectangle"] == [([0.0, 0.0, 3.0, 1.0],)]
    assert h.lookups.calls == 1  # one ee.Dictionary round trip


def test_a_drawn_group_measures_the_union_once(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    a, b = box(0.1, 0.1, 0.3, 0.3), box(0.2, 0.1, 0.4, 0.3)
    _save(h, 0, {1}, defo=_drawing(a, b))
    stored = _stored(h)
    assert stored["area_ha"].iloc[0] == pytest.approx(review_groups.polygon_area_ha(a.union(b)))
    assert stored["area_ha"].iloc[1] == 0.0
    assert stored["alert_polygon"].iloc[0].equals(a.union(b)) and stored["alert_polygon"].iloc[1] is None


def test_remove_from_group_resets_the_member(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    _save(h, 0, {1, 2})
    calls = h.lookups.calls
    wrote, alert = _save(h, 1, remove=True)
    assert wrote is True and alert.plan.kind == review_groups.REMOVE
    assert h.lookups.calls == calls  # no Earth Engine call
    for gdf in (_stored(h), h.s.alerts_gdf.value):
        assert gdf["status"].tolist() == ["Confirmed", "Not reviewed", "Confirmed", "Not reviewed"]
        assert _groups(gdf) == [10, None, 10, None]
        assert gdf["description"].iloc[1] == "" and pd.isna(gdf["admin1"].iloc[1])
        assert gdf["admin1"].iloc[2] == "Brasil"
        assert gdf["area_ha"].iloc[1] == pytest.approx(review_groups.polygon_area_ha(box(1, 0, 2, 1)))
        review_groups.check_groups(gdf)


def test_an_old_store_without_alert_id_groups_by_position(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True, alerts=_alerts(ids=False))
    _save(h, 1, {2})
    assert _groups(_stored(h)) == [None, 1, 1, None]
    review_groups.check_groups(_stored(h))


# ---------------------------------------------------------------------------
# The runner: a late tick, and on_saved
# ---------------------------------------------------------------------------
def _runner(h, monkeypatch, *, inline=True, on_saved=None):
    from tests.test_feedback_notifications import _record_feedback

    feedback = _record_feedback(monkeypatch, export)
    if inline:
        monkeypatch.setattr(export, "spawn_in_context", lambda target, args=(), **k: target(*args))
    return export.make_save_runner(WIDGETS, h.s, h.analysis, h.ctx, on_saved=on_saved), feedback


def test_a_tick_after_the_click_is_not_in_the_save(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    h.analysis = _analysis(selection={1})
    touch(h.analysis)
    h.s.actual_alert_id.set(0)
    seen = []
    run_save, _ = _runner(h, monkeypatch, on_saved=lambda alert, outpaced: seen.append((alert, outpaced)))
    real = export._save_attributes

    def _tick_meanwhile(*a, **k):
        wrote = real(*a, **k)
        toggle_selection(h.analysis, 2)  # the reviewer ticks #3 while the save runs
        return wrote

    monkeypatch.setattr(export, "_save_attributes", _tick_meanwhile)
    run_save(None)

    assert h.analysis.dirty.value is True
    assert [o for _, o in seen] == [True]
    assert seen[0][0].plan.members == (1,)
    assert h.analysis.selection.value == frozenset({1, 2})
    assert _groups(_stored(h)) == [10, 10, None, None]


def test_on_saved_runs_on_the_worker_with_the_capture(tmp_path, monkeypatch):
    h = _setup(tmp_path, monkeypatch, grouping=True)
    h.analysis = _analysis(selection={1, 2})
    h.s.actual_alert_id.set(0)
    seen, order = [], []

    def _on_saved(alert, outpaced):
        seen.append((alert, outpaced, threading.current_thread()))
        order.append("on_saved")

    run_save, feedback = _runner(h, monkeypatch, inline=False, on_saved=_on_saved)
    alert = capture_alert(h.analysis, WIDGETS, h.s)
    real = export._save_attributes

    def _move_on_meanwhile(*a, **k):
        wrote = real(*a, **k)
        h.analysis.reset_alert()  # the reviewer opened another alert
        return wrote

    monkeypatch.setattr(export, "_save_attributes", _move_on_meanwhile)
    run_save(None, then=lambda: order.append("then"), alert=alert)
    for thread in feedback.threads:
        thread.join(10)

    assert len(seen) == 1
    got, outpaced, thread = seen[0]
    assert got is alert and got.plan.members == (1, 2)
    assert outpaced is False
    assert thread is not threading.main_thread()
    assert order == ["on_saved"]  # ``then`` waits for the alert saved, which is gone
    # the runner wrote nothing to the alert now on screen
    assert h.analysis.last_save_time.value == 0.0 and h.analysis.dirty.value is False
    assert _groups(_stored(h)) == [10, 10, 10, None]


def test_capture_records_the_set_and_the_store_generation(tmp_path, monkeypatch):
    from gui.scripts.set_store import STORE_GENERATION

    h = _setup(tmp_path, monkeypatch, grouping=True, copy_of="ana")
    h.s.actual_alert_id.set(0)
    alert = capture_alert(_analysis(), WIDGETS, h.s)
    assert alert.set_id == "x"
    assert alert.generation == h.s.alerts_gdf.value.attrs[STORE_GENERATION] is not None
    assert alert.evaluator == "ana" and alert.grouping.radius_m == 300.0
    assert alert.selection == frozenset()


def test_toggle_selection_flips_and_touches():
    analysis = AnalysisState()
    toggle_selection(analysis, 3)
    assert analysis.selection.value == frozenset({3}) and analysis.edit_rev.value == 1
    toggle_selection(analysis, 3)
    assert analysis.selection.value == frozenset() and analysis.dirty.value is True
    analysis.selection.set(frozenset({1}))
    analysis.reset_alert()
    assert analysis.selection.value == frozenset()
