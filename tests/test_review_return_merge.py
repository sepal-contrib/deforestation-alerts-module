"""Merging a reviewed copy into the creator's set (spec §7): the checks and
the classification of ``share.read_return``, the rows ``share.apply_return``
writes, and ``merge_service.merge_return`` on a real creator folder (the
store write, the republish, the record, the reload of the alert open in
Analysis). The reviewed copies are real zips made by ``build_return``."""

import json
import math
from dataclasses import replace
from io import BytesIO

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from deforestation_alerts.project import paths, review_groups, share
from deforestation_alerts.project.growth import links_of, run_id_of
from deforestation_alerts.project.share import ShareError, apply_return, read_return
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet
from gui.i18n import t
from gui.scripts import growth_index, integrate_jobs, merge_service, set_store
from gui.scripts.error_text import error_text
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.state import AnalysisState
from tests._share_helpers import AOI_FC, APP_VERSION, CREATOR, SET1, SET2, make_copy, make_creator, rezip, zip_members

#: the creator's store: alert 0 reviewed alike, 1 Bo's, 2 reviewed otherwise, 3 not reviewed
CREATOR_STATUSES = ("Confirmed", "Not reviewed", "Need revision", "Not reviewed")
#: Ana's copy: she reviewed 0, 2 and 3 (and 1, which her copy wrongly marks as hers)
COPY_STATUSES = ("Confirmed", "Confirmed", "False Positive", "Confirmed")
CREATOR_ASSIGNED = ("Ana", "Bo", "Ana", "Ana")
COPY_ASSIGNED = ("Ana", "Ana", "Ana", "Ana")


def _creator(tmp_path, monkeypatch, *, statuses=CREATOR_STATUSES, columns=None):
    """The creator's project under the data dir; ``columns`` overwrite store columns."""
    root = tmp_path / "data"
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(root))
    p, pdir = make_creator(root, statuses=statuses, assigned=CREATOR_ASSIGNED)
    if columns:
        _rewrite(pdir / "legacy" / "alert_db.parquet", columns)
    return p, pdir


def _rewrite(store, columns) -> None:
    gdf = load_gdf_from_parquet(store)
    for name, values in columns.items():
        gdf[name] = values
    save_gdf_to_parquet(gdf, store)


def _return(tmp_path, *, statuses=COPY_STATUSES, extra=None, columns=None):
    """Ana's reviewed copy, as ``build_return`` makes it."""
    p, pdir = make_copy(tmp_path / "copies", statuses=statuses, assigned=COPY_ASSIGNED, extra=extra)
    if columns:
        _rewrite(_copy_store(p, pdir), columns)
    return share.build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)


def _copy_store(p, pdir):
    return paths.resolve(pdir, p.alert_sets[SET1].store_rel_path)


def _store(p):
    return set_store.load_set_alerts(p, p.alert_sets[SET1])


def _header(zip_path, dst, **changes):
    members = zip_members(zip_path)
    header = json.loads(members[share.HEADER_MEMBER])
    header.update(changes)
    return rezip(zip_path, dst, replace={share.HEADER_MEMBER: json.dumps(header)})


def _reviews(zip_path, dst, change):
    """``zip_path`` with its reviews table passed through ``change``."""
    rows = share.read_table(BytesIO(zip_members(zip_path)[share.REVIEWS_MEMBER]))
    buf = BytesIO()
    change(rows).to_parquet(buf, index=False)
    return rezip(zip_path, dst, replace={share.REVIEWS_MEMBER: buf.getvalue()})


def _refused(fn) -> ShareError:
    with pytest.raises(ShareError) as info:
        fn()
    return info.value


# ---- read_return: checks and classification -------------------------------------


def test_rows_are_new_conflicts_unchanged_or_ignored(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _return(tmp_path)
    preview = read_return(zp, p, _store(p))
    assert (preview.new, preview.conflicts, preview.unchanged, preview.ignored) == ([3], [2], [0], [1])
    assert preview.header.evaluator == "Ana" and not preview.questions_changed
    assert preview.columns[0] == "status" and "evaluator" not in preview.columns
    assert preview.extra_columns == []
    # an alert the store does not hold is ignored too
    without_3 = _store(p).iloc[:3]
    assert read_return(zp, p, without_3).ignored == [1, 3]


def test_missing_values_and_equal_geometries_are_unchanged(tmp_path, monkeypatch):
    """Spec §7.3: None, NaN and "" are one missing value; geometries compare by WKB."""
    shape_ = box(0.0, 0.0, 0.01, 0.01)
    p, _ = _creator(tmp_path, monkeypatch, columns={
        "after_img": [math.nan] * 4,
        "alert_polygon": gpd.GeoSeries([shape_, None, None, None], crs="EPSG:4326")})
    zp = _return(tmp_path, columns={
        "description": [None] * 4,
        "after_img": [None] * 4,
        "alert_polygon": gpd.GeoSeries([box(0.0, 0.0, 0.01, 0.01), None, box(0, 0, 1, 1), None], crs="EPSG:4326")})
    preview = read_return(zp, p, _store(p))
    assert 0 in preview.unchanged
    assert 2 in preview.conflicts


def test_a_drawn_polygon_alone_makes_a_reviewed_row_a_conflict(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _return(tmp_path, columns={
        "alert_polygon": gpd.GeoSeries([box(0, 0, 1, 1), None, None, None], crs="EPSG:4326")})
    assert read_return(zp, p, _store(p)).conflicts == [0, 2]


def test_a_changed_questionnaire_warns_and_extra_columns_are_named(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _header(_return(tmp_path), tmp_path / "q.zip", questions_digest="older")
    zp = _reviews(zp, tmp_path / "q2.zip", lambda r: r.assign(q_gone="x"))
    preview = read_return(zp, p, _store(p))
    assert preview.questions_changed
    assert preview.extra_columns == ["q_gone"] and "q_gone" not in preview.columns
    lines = merge_service.preview_lines(preview, selected=4)
    assert lines == [
        t("share.merge_summary", evaluator="Ana", new=1, conflicts=1, unchanged=1, ignored=1),
        t("share.merge_questions_changed"),
        t("share.merge_extra_columns", columns="q_gone"),
        t("share.merge_selected_reload", n=4),
    ]


@pytest.mark.parametrize("case, key", [
    ("review_copy", "share.merge_err_review_copy"),
    ("kind", "share.merge_err_kind"),
    ("wrong_set", "share.merge_err_wrong_set"),
    ("rerun", "share.merge_err_rerun"),
    ("old_split", "share.merge_err_old_split"),
    ("no_split", "share.merge_err_old_split"),
    ("evaluator", "share.merge_err_evaluator"),
    ("junk", "share.merge_err_payload"),
    ("dup_ids", "share.merge_err_payload"),
    ("other_evaluator", "share.merge_err_payload"),
    ("not_a_verdict", "share.merge_err_payload"),
    ("no_alerts", "share.merge_err_no_alerts"),
    ("missing_member", "share.err.missing_member"),
])
def test_read_return_refusals(tmp_path, monkeypatch, case, key):
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _return(tmp_path)
    gdf = _store(p)
    s = p.alert_sets[SET1]
    dst = tmp_path / "bad.zip"
    if case == "review_copy":
        p = make_copy(tmp_path / "other", statuses=COPY_STATUSES, assigned=COPY_ASSIGNED)[0]
    elif case == "kind":
        zp = _header(zp, dst, kind=share.KIND_PACKAGE)
    elif case == "wrong_set":
        p = p.model_copy(update={"active_set": None})
    elif case == "rerun":
        p.alert_sets[SET1] = s.model_copy(update={"fingerprint": "fp-rerun"})
    elif case == "old_split":
        p.alert_sets[SET1] = s.model_copy(update={"split": s.split.model_copy(update={"split_id": "b" * 32})})
    elif case == "no_split":
        p.alert_sets[SET1] = s.model_copy(update={"split": None})
    elif case == "evaluator":
        zp = _header(zp, dst, evaluator="Cy")
    elif case == "junk":
        zp = rezip(zp, dst, replace={share.REVIEWS_MEMBER: b"not parquet"})
    elif case == "dup_ids":
        zp = _reviews(zp, dst, lambda r: r.assign(alert_id=0))
    elif case == "other_evaluator":
        zp = _reviews(zp, dst, lambda r: r.assign(evaluator="Bo"))
    elif case == "not_a_verdict":
        zp = _reviews(zp, dst, lambda r: r.assign(status="Not reviewed"))
    elif case == "no_alerts":
        gdf = None
    elif case == "missing_member":
        zp = rezip(zp, dst, drop=[share.REVIEWS_MEMBER])
    exc = _refused(lambda: read_return(zp, p, gdf))
    assert exc.key == key
    assert error_text(exc) == t(key, **exc.params)
    if case == "wrong_set":
        assert exc.params == {"set": SET1}
    if case == "evaluator":
        assert exc.params == {"evaluator": "Cy"}


def test_a_real_package_picked_to_merge_reads_as_the_wrong_kind(tmp_path, monkeypatch):
    """A package (its alerts, AOI and imagery members) is checked for its kind
    before the reviewed copy's member allow-list, as Import does: it reads as
    "a review package, not a reviewed copy", never as an unexpected file."""
    p, pdir = _creator(tmp_path, monkeypatch)
    package = share.build_package(p, pdir, SET1, "Ana", AOI_FC, tmp_path / "out", app_version=APP_VERSION)
    exc = _refused(lambda: read_return(package, p, _store(p)))
    assert exc.key == "share.merge_err_kind"
    assert error_text(exc) == t("share.merge_err_kind")
    # a kind neither side makes is named as such
    odd = _header(_return(tmp_path), tmp_path / "odd.zip", kind="something-else")
    exc = _refused(lambda: read_return(odd, p, _store(p)))
    assert exc.key == "share.err.unknown_kind" and exc.params == {"kind": "something-else"}


# ---- apply_return ---------------------------------------------------------------------


def test_theirs_takes_the_conflicts_and_mine_keeps_them(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch, columns={"after_img": [math.nan] * 4})
    zp = _return(tmp_path, extra={"q_cause": ["", "", "fire", "fire"],
                                  "after_img": [None, None, None, "after.tif"]})
    gdf = _store(p)
    assert "q_cause" not in gdf.columns
    preview = read_return(zp, p, gdf)

    theirs = apply_return(gdf, preview, "theirs")
    assert theirs["status"].tolist() == ["Confirmed", "Not reviewed", "False Positive", "Confirmed"]
    assert theirs["q_cause"].tolist() == ["", "", "fire", "fire"]  # created like a Save does, then written
    assert theirs["after_img"].iat[3] == "after.tif"  # a float column of unset names takes a name
    assert theirs["evaluator"].tolist() == list(CREATOR_ASSIGNED)  # never the return's

    mine = apply_return(gdf, preview, "mine")
    assert mine["status"].tolist() == ["Confirmed", "Not reviewed", "Need revision", "Confirmed"]
    assert mine["q_cause"].tolist() == ["", "", "", "fire"]

    assert gdf["status"].tolist() == list(CREATOR_STATUSES) and "q_cause" not in gdf.columns  # a copy
    with pytest.raises(ValueError, match="unknown merge choice"):
        apply_return(gdf, preview, "both")


def test_nothing_to_write_returns_the_store_as_is(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch, statuses=COPY_STATUSES)
    preview = read_return(_return(tmp_path), p, _store(p))
    assert preview.new == [] and preview.conflicts == []
    out = apply_return(_store(p), preview, "theirs")
    assert "q_cause" not in out.columns and out["status"].tolist() == list(COPY_STATUSES)


# ---- merge_return -------------------------------------------------------------------


def _session(tmp_path, monkeypatch, **kw):
    """The creator's project open, its set's alerts on hand; returns
    ``(app_state, set_context, analysis, return zip)``."""
    p, _ = _creator(tmp_path, monkeypatch, **kw)
    app_state = AppState()
    app_state.new_project_state(p)
    app_state.alerts_gdf.set(_store(p))
    return app_state, SetContext(app_state), AnalysisState(), _return(tmp_path)


def _preview(app_state, zp):
    return read_return(zp, app_state.project.value, app_state.alerts_gdf.value)


def _disk(app_state):
    p = app_state.project.value
    return load_gdf_from_parquet(set_store.store_path(p, p.alert_sets[SET1]))


def test_a_merge_writes_the_store_republishes_and_records_the_return(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    record = merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "theirs")

    assert (record.taken, record.unchanged, record.conflicts, record.choice, record.ignored) == (2, 1, 1, "theirs", 1)
    assert _disk(app_state)["status"].tolist() == ["Confirmed", "Not reviewed", "False Positive", "Confirmed"]
    live = app_state.alerts_gdf.value
    assert live["status"].tolist() == _disk(app_state)["status"].tolist()
    p = app_state.project.value
    assert not set_store.generation_changed(live, set_store.store_path(p, p.alert_sets[SET1]))  # a Save may follow
    s = p.alert_sets[SET1]
    assert s.n_reviewed == 3
    assert s.split.returns == {"Ana": record}


def test_mine_keeps_the_creators_conflicting_reviews(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    record = merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "mine")
    assert (record.taken, record.conflicts, record.choice) == (1, 1, "mine")
    assert _disk(app_state)["status"].tolist() == ["Confirmed", "Not reviewed", "Need revision", "Confirmed"]


def test_merging_the_same_copy_again_changes_nothing(tmp_path, monkeypatch):
    """Spec §7.4: a repeated merge finds every row unchanged, writes no store
    (its generation stays), and only refreshes the record; a stale preview is
    classified again against the store first."""
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    stale = _preview(app_state, zp)
    merge_service.merge_return(app_state, ctx, analysis, stale, "theirs")
    p = app_state.project.value
    store = set_store.store_path(p, p.alert_sets[SET1])
    generation = set_store.store_generation(store)

    again = _preview(app_state, zp)
    assert (again.new, again.conflicts, again.unchanged, again.ignored) == ([], [], [0, 2, 3], [1])
    for preview in (again, stale):
        record = merge_service.merge_return(app_state, ctx, analysis, preview, "theirs")
        assert (record.taken, record.unchanged, record.conflicts, record.choice, record.ignored) == (0, 3, 0, None, 1)
    assert set_store.store_generation(store) == generation
    assert app_state.project.value.alert_sets[SET1].split.returns["Ana"] == record


def test_a_store_saved_during_the_merge_refuses_it(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    preview = _preview(app_state, zp)
    real = set_store.load_set_alerts

    def _load_then_saved(p, s):
        gdf = real(p, s)
        save_gdf_to_parquet(gdf, set_store.store_path(p, s))  # a review Save lands after the read
        return gdf

    monkeypatch.setattr(set_store, "load_set_alerts", _load_then_saved)
    assert _refused(lambda: merge_service.merge_return(
        app_state, ctx, analysis, preview, "theirs")).key == "share.merge_err_store_changed"
    assert _disk(app_state)["status"].tolist() == list(CREATOR_STATUSES)
    assert app_state.project.value.alert_sets[SET1].split.returns == {}


def test_a_running_integrate_refuses_the_merge(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    preview = _preview(app_state, zp)
    with integrate_jobs.integrating(CREATOR, SET1):
        assert _refused(lambda: merge_service.merge_return(
            app_state, ctx, analysis, preview, "theirs")).key == "evaluators.err.integrating"

    calls = []
    monkeypatch.setattr(merge_service, "is_integrating", lambda *a: calls.append(a) or len(calls) > 1)
    assert _refused(lambda: merge_service.merge_return(
        app_state, ctx, analysis, preview, "theirs")).key == "evaluators.err.integrating"  # the save's guard
    assert _disk(app_state)["status"].tolist() == list(CREATOR_STATUSES)


def test_a_set_changed_since_the_preview_refuses_the_merge(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    preview = _preview(app_state, zp)

    def _resplit(live):
        s = live.alert_sets[SET1]
        s.split = s.split.model_copy(update={"split_id": "b" * 32})

    app_state.update_project(CREATOR, _resplit)
    assert _refused(lambda: merge_service.merge_return(
        app_state, ctx, analysis, preview, "theirs")).key == "share.merge_err_changed"
    assert _disk(app_state)["status"].tolist() == list(CREATOR_STATUSES)


def test_a_review_copy_never_merges(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    preview = _preview(app_state, zp)
    copy = make_copy(tmp_path / "other", statuses=COPY_STATUSES, assigned=COPY_ASSIGNED)[0]
    app_state.new_project_state(copy)
    assert _refused(lambda: merge_service.merge_return(
        app_state, ctx, analysis, preview, "theirs")).key == "share.merge_err_review_copy"


@pytest.mark.parametrize("selected, choice, reloaded", [
    (3, "theirs", True),    # a new review of the alert open in Analysis
    (2, "theirs", True),    # its conflict, taken
    (2, "mine", False),     # its conflict, kept
    (0, "theirs", False),   # unchanged
    (1, "theirs", False),   # Bo's: ignored
])
def test_the_alert_open_in_analysis_is_reloaded_when_its_row_is_written(tmp_path, monkeypatch, selected, choice,
                                                                         reloaded):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    app_state.actual_alert_id.set(selected)
    preview = _preview(app_state, zp)
    assert merge_service.selected_alert_written(app_state, preview, choice) == (selected + 1 if reloaded else None)
    before = analysis.reload_alert.value
    merge_service.merge_return(app_state, ctx, analysis, preview, choice)
    assert analysis.reload_alert.value == before + (1 if reloaded else 0)
    analysis.reset_alert()
    assert analysis.reload_alert.value == before + (1 if reloaded else 0)  # not per-alert state


def test_a_reload_request_views_the_current_alert_again(monkeypatch):
    app_state, analysis = AppState(), AnalysisState()
    views = []
    monkeypatch.setattr(analysis_tile, "view_actual_alert", lambda *a: views.append(a))
    monkeypatch.setattr(analysis_tile, "select_start_alert", lambda *a: None)
    analysis_tile.wire_alert_navigation(None, app_state, analysis, None, None)
    assert len(views) == 1
    analysis.reload_alert.set(analysis.reload_alert.value + 1)
    assert len(views) == 2


# ---- growth links travel with the reviews (spec 2026-10-20 §6.6) ----------------------


def test_same_value_compares_link_lists():
    import numpy as np

    assert share.same_value(np.array(["a"]), ["a"])
    assert share.same_value([], None)
    assert share.same_value(np.array([], dtype=object), [])
    assert share.same_value(["a", "b"], "a;b")
    assert not share.same_value(["a"], ["b"])
    assert not share.same_value(["a"], [])


def test_return_carries_grows_from_and_merges(tmp_path, monkeypatch):
    link = f"{SET1}#0@20261001120000000000"
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _return(tmp_path, extra={"grows_from": [[link], [], [], []]})
    preview = read_return(zp, p, _store(p))
    assert "grows_from" in preview.columns and preview.new == [3]
    merged = apply_return(_store(p), preview, "theirs")
    assert links_of(merged.loc[merged["alert_id"] == 0, "grows_from"].iloc[0]) == [link]
    assert [links_of(v) for v in merged["grows_from"]] == [[link], [], [], []]


def test_a_store_with_an_all_empty_grows_from_column_takes_the_links(tmp_path, monkeypatch):
    """Integrate stores ``grows_from`` as ``list<null>`` when it linked nothing."""
    link = f"{SET1}#0@20261001120000000000"
    p, pdir = _creator(tmp_path, monkeypatch, columns={"grows_from": [[], [], [], []]})
    import pyarrow.parquet as pq

    assert str(pq.read_schema(pdir / "legacy" / "alert_db.parquet").field("grows_from").type) == "list<element: null>"
    zp = _return(tmp_path, extra={"grows_from": [[], [], [link], [link]]})
    preview = read_return(zp, p, _store(p))
    assert preview.unchanged == [0]  # [] against list<null>'s empty array
    merged = apply_return(_store(p), preview, "theirs")
    assert [links_of(v) for v in merged["grows_from"]] == [[], [], [link], [link]]
    out = tmp_path / "merged.parquet"
    save_gdf_to_parquet(merged, out)
    assert [links_of(v) for v in load_gdf_from_parquet(out)["grows_from"]] == [[], [], [link], [link]]


# ---- a review copy links against its package's earlier alerts ----------------------------


def _open_copy(state_root, links):
    """A growth creator whose SET2 alert 0 is linked ``links`` into SET1, its
    package for Ana imported into the data directory and open in an AppState."""
    from tests._share_helpers import make_growth_creator

    growth_index.clear_cache()
    creator, cpdir = make_growth_creator(state_root, links={0: links})
    package = share.build_package(creator, cpdir, SET2, "Ana", AOI_FC, state_root.parent / "out",
                                  app_version=APP_VERSION)
    copy = share.import_package(package)
    state = AppState()
    state.load_project_state(copy)
    s = copy.alert_sets[SET2]
    assert set_store.publish_set_alerts(state, copy.name, s, set_store.load_set_alerts(copy, s))
    return creator, state


def _view(p, gdf, links):
    return growth_index.earlier_view(p, SET2, gdf, 0, links, links, 50_000.0)  # the alerts are 0.1 deg apart


def test_a_copy_shows_a_link_to_a_since_false_positive_parent_as_broken(tmp_path, monkeypatch):
    from gui.tile.analysis import earlier

    root = tmp_path / "data"
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(root))
    creator, state = _open_copy(root, [f"{SET1}#1@{{run}}"])
    copy = state.project.value
    link = f"{SET1}#1@{run_id_of(creator.alert_sets[SET1])}"
    view = _view(copy, state.alerts_gdf.value, [link])
    assert [(it.cand.key, it.state, it.cand.node.status) for it in view.items if it.cand.link == link] == [
        (f"{SET1}#1", "broken", "False Positive")]
    assert earlier._kept_links(copy, view, (link,)) == (link,)  # kept, greyed (spec §11.5), as in the creator's project
    assert growth_index.history_summary(copy, SET2, state.alerts_gdf.value, 0, [link]) is None
    growth_index.clear_cache()


def test_a_copy_link_survives_open_save_return_and_merge(tmp_path, monkeypatch):
    """A copy alert linked to a Confirmed parent keeps the link through open
    (Task 6), Save (Task 5) and the merge; a parent the evaluator ticks comes
    back too."""
    import types

    from gui.tile.analysis import earlier, export
    from tests.test_set_context import _AdminLookups

    root = tmp_path / "data"
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(root))
    creator, state = _open_copy(root, [f"{SET1}#0@{{run}}"])
    copy = state.project.value
    run1 = run_id_of(creator.alert_sets[SET1])
    stored, ticked = f"{SET1}#0@{run1}", f"{SET1}#2@{run1}"
    gdf = state.alerts_gdf.value

    view = _view(copy, gdf, [stored])
    assert {it.cand.link: it.state for it in view.items}[stored] == "auto"
    assert ticked in {it.cand.link for it in view.items}  # offered to tick
    kept = earlier._kept_links(copy, view, (stored,))
    assert kept == (stored,)
    summary = growth_index.history_summary(copy, SET2, gdf, 0, [stored, ticked])
    assert summary is not None and summary.n == 3

    monkeypatch.setattr("gui.tile.analysis.review_save.ee", _AdminLookups())
    analysis = AnalysisState()
    analysis.verdict.set("Confirmed")
    analysis.links.set(kept + (ticked,))
    state.actual_alert_id.set(0)
    widgets = types.SimpleNamespace(actual_bb=types.SimpleNamespace(geometry=lambda: None))
    assert export._save_attributes(widgets, state, analysis, SetContext(state)) is True
    saved = set_store.load_set_alerts(copy, copy.alert_sets[SET2])
    assert links_of(saved.loc[saved["alert_id"] == 0, "grows_from"].iloc[0]) == [stored, ticked]

    zp = share.build_return(state.project.value, paths.project_dir(copy.name), tmp_path / "back",
                            app_version=APP_VERSION)
    cstore = set_store.load_set_alerts(creator, creator.alert_sets[SET2])
    preview = read_return(zp, creator, cstore)
    assert preview.new == [0]
    merged = apply_return(cstore, preview, "theirs")
    row = merged.loc[merged["alert_id"] == 0].iloc[0]
    assert row["status"] == "Confirmed" and links_of(row["grows_from"]) == [stored, ticked]
    growth_index.clear_cache()


# ---- group review: returns carry detachments, merges are per group (spec 2026-10-26 §8.3, §8.4) ----

NA = pd.NA
ANA = ("Ana",) * 4


def _groups(values):
    return pd.array(list(values), dtype="Int64")


def _plain(series) -> list:
    """``review_group`` as ints and None (``<NA>`` does not compare)."""
    return [None if pd.isna(v) else int(v) for v in series]


def _grouped_creator(tmp_path, monkeypatch, statuses, groups=None):
    """A creator whose store is all Ana's, ``groups`` its ``review_group`` (no column when None)."""
    root = tmp_path / "data"
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(root))
    p, pdir = make_creator(root, statuses=statuses, assigned=ANA)
    if groups is not None:
        _rewrite(pdir / "legacy" / "alert_db.parquet", {"review_group": _groups(groups)})
    return p, pdir


def _grouping_copy(tmp_path, statuses, groups, assigned=ANA):
    """Ana's copy of a split made with grouping on (500 m)."""
    p, pdir = make_copy(tmp_path / "copies", statuses=statuses, assigned=assigned,
                        extra={"review_group": _groups(groups)})
    s = p.alert_sets[SET1]
    p.alert_sets[SET1] = s.model_copy(update={"split": s.split.model_copy(update={"group_radius_m": 500.0})})
    return p, pdir


def _grouping_return(tmp_path, statuses, groups, assigned=ANA):
    p, pdir = _grouping_copy(tmp_path, statuses, groups, assigned)
    return share.build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)


def _grouped_session(tmp_path, monkeypatch, statuses, groups=None):
    p, _ = _grouped_creator(tmp_path, monkeypatch, statuses, groups)
    app_state = AppState()
    app_state.new_project_state(p)
    app_state.alerts_gdf.set(_store(p))
    return app_state, SetContext(app_state), AnalysisState()


def _classes(preview) -> tuple:
    return preview.new, preview.conflicts, preview.unchanged, preview.ignored


def test_a_return_without_grouping_is_format_one_as_before(tmp_path):
    p, pdir = make_copy(tmp_path / "copies", statuses=("Confirmed", "Not reviewed", "False Positive", "Not reviewed"),
                        assigned=COPY_ASSIGNED)
    zp = share.build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)
    members = zip_members(zp)
    assert json.loads(members[share.HEADER_MEMBER])["format_version"] == 1
    rows = share.read_table(BytesIO(members[share.REVIEWS_MEMBER]))
    store = load_gdf_from_parquet(_copy_store(p, pdir))
    assert list(rows.columns) == ["alert_id", "evaluator", *[c for c in share.review_columns(p.questions)
                                                             if c in store.columns]]
    assert "review_group" not in rows.columns
    assert rows["alert_id"].tolist() == [0, 2]  # the reviewed rows only, as before


def test_a_grouping_return_is_format_two_and_carries_a_detached_member(tmp_path, monkeypatch):
    zp = _grouping_return(tmp_path, ("Confirmed", "Confirmed", "Not reviewed", "Not reviewed"), (0, 0, NA, NA),
                          assigned=("Ana", "Ana", "Ana", "Bo"))
    members = zip_members(zp)
    assert json.loads(members[share.HEADER_MEMBER])["format_version"] == share.RETURN_GROUPS_VERSION
    rows = share.read_table(BytesIO(members[share.REVIEWS_MEMBER]))
    assert rows["alert_id"].tolist() == [0, 1, 2]  # every row assigned to Ana, reviewed or not
    assert rows["status"].tolist() == ["Confirmed", "Confirmed", "Not reviewed"]
    assert _plain(rows["review_group"]) == [0, 0, None]
    p, _ = _grouped_creator(tmp_path, monkeypatch, ("Confirmed",) * 3 + ("Not reviewed",), (0, 0, 0, NA))
    assert read_return(zp, p, _store(p)).header.format_version == 2  # a Not-reviewed row reads in format 2


def test_a_format_one_return_still_refuses_a_not_reviewed_row(tmp_path, monkeypatch):
    p, _ = _creator(tmp_path, monkeypatch)
    zp = _reviews(_return(tmp_path), tmp_path / "nr.zip", lambda r: r.assign(status="Not reviewed"))
    assert _refused(lambda: read_return(zp, p, _store(p))).key == "share.merge_err_payload"


def test_a_detached_member_resets_the_creators_row(tmp_path, monkeypatch):
    """Alert 2 was merged as a member of alert 0's group, then detached in the copy."""
    p, _ = _grouped_creator(tmp_path, monkeypatch, ("Confirmed",) * 3 + ("Not reviewed",), (0, 0, 0, NA))
    zp = _grouping_return(tmp_path, ("Confirmed", "Confirmed", "Not reviewed", "Not reviewed"), (0, 0, NA, NA))
    gdf = _store(p)
    preview = read_return(zp, p, gdf)
    assert _classes(preview) == ([], [0, 1, 2], [3], [])  # the group is one conflict
    assert preview.n_groups == 1
    merged = apply_return(gdf, preview, "theirs")
    assert merged["status"].tolist() == ["Confirmed", "Confirmed", "Not reviewed", "Not reviewed"]
    assert _plain(merged["review_group"]) == [0, 0, None, None]
    assert str(merged["review_group"].dtype) == "Int64"
    review_groups.check_groups(merged)


def test_a_not_reviewed_row_outside_a_group_is_unchanged(tmp_path, monkeypatch):
    """Alert 3, reviewed by the creator alone, is left as it is by Ana's unreviewed row."""
    p, _ = _grouped_creator(tmp_path, monkeypatch, ("Not reviewed",) * 3 + ("False Positive",))
    zp = _grouping_return(tmp_path, ("Confirmed", "Confirmed", "Not reviewed", "Not reviewed"), (0, 0, NA, NA))
    gdf = _store(p)
    preview = read_return(zp, p, gdf)
    assert _classes(preview) == ([0, 1], [], [2, 3], [])
    merged = apply_return(gdf, preview, "theirs")
    assert merged["status"].tolist() == ["Confirmed", "Confirmed", "Not reviewed", "False Positive"]
    assert str(merged["review_group"].dtype) == "Int64"  # created as Int64, not object
    assert _plain(merged["review_group"]) == [0, 0, None, None]


def _one_conflict(tmp_path, monkeypatch):
    """Ana grouped 0, 1 and 2; the creator reviewed alert 0 otherwise."""
    p, _ = _grouped_creator(tmp_path, monkeypatch, ("False Positive",) + ("Not reviewed",) * 3)
    zp = _grouping_return(tmp_path, ("Confirmed",) * 3 + ("Not reviewed",), (0, 0, 0, NA))
    gdf = _store(p)
    return gdf, read_return(zp, p, gdf)


def test_one_conflicting_row_makes_its_whole_group_a_conflict(tmp_path, monkeypatch):
    _, preview = _one_conflict(tmp_path, monkeypatch)
    assert _classes(preview) == ([], [0, 1, 2], [3], [])
    assert preview.n_groups == 1


def test_keep_mine_keeps_the_whole_group(tmp_path, monkeypatch):
    gdf, preview = _one_conflict(tmp_path, monkeypatch)
    mine = apply_return(gdf, preview, "mine")
    assert mine["status"].tolist() == ["False Positive"] + ["Not reviewed"] * 3  # no member without its main
    assert "review_group" not in mine.columns


def test_take_theirs_takes_the_whole_group(tmp_path, monkeypatch):
    gdf, preview = _one_conflict(tmp_path, monkeypatch)
    theirs = apply_return(gdf, preview, "theirs")
    assert theirs["status"].tolist() == ["Confirmed"] * 3 + ["Not reviewed"]
    assert _plain(theirs["review_group"]) == [0, 0, 0, None]
    review_groups.check_groups(theirs)


def test_a_merge_that_breaks_a_group_is_refused(tmp_path, monkeypatch):
    app_state, ctx, analysis = _grouped_session(tmp_path, monkeypatch, ("Not reviewed",) * 4)
    zp = _grouping_return(tmp_path, ("Confirmed", "Confirmed", "Not reviewed", "Not reviewed"), (0, 0, NA, NA))
    # alert 1 names a main that is not in the return nor in the store
    zp = _reviews(zp, tmp_path / "broken.zip", lambda r: r.assign(review_group=_groups((0, 99, NA, NA))))
    before = _disk(app_state)
    exc = _refused(lambda: merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "theirs"))
    assert exc.key == "share.merge_err_groups"
    assert error_text(exc) == t("share.merge_err_groups")
    after = _disk(app_state)
    assert list(after.columns) == list(before.columns) and after["status"].tolist() == ["Not reviewed"] * 4
    assert app_state.project.value.alert_sets[SET1].split.returns == {}


def test_an_older_format_one_zip_still_merges(tmp_path, monkeypatch):
    """A format-1 return merges as before, also into a store that has the column."""
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch, columns={"review_group": _groups([NA] * 4)})
    preview = _preview(app_state, zp)
    assert preview.header.format_version == 1 and preview.n_groups == 0
    record = merge_service.merge_return(app_state, ctx, analysis, preview, "theirs")
    assert (record.taken, record.unchanged, record.conflicts, record.ignored) == (2, 1, 1, 1)
    disk = _disk(app_state)
    assert disk["status"].tolist() == ["Confirmed", "Not reviewed", "False Positive", "Confirmed"]
    assert _plain(disk["review_group"]) == [None] * 4


def test_merge_keeps_n_groups(tmp_path, monkeypatch):
    app_state, ctx, analysis = _grouped_session(tmp_path, monkeypatch, ("Not reviewed",) * 4)
    zp = _grouping_return(tmp_path, ("Confirmed",) * 3 + ("Not reviewed",), (0, 0, 0, NA))
    record = merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "theirs")
    assert record.taken == 3
    s = app_state.project.value.alert_sets[SET1]
    assert (s.n_reviewed, s.n_groups) == (3, 1)
    disk = _disk(app_state)
    assert str(disk["review_group"].dtype) == "Int64" and _plain(disk["review_group"]) == [0, 0, 0, None]


def test_a_format_one_merge_adds_no_group_column(tmp_path, monkeypatch):
    app_state, ctx, analysis, zp = _session(tmp_path, monkeypatch)
    before = _disk(app_state)
    merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "theirs")
    after = _disk(app_state)
    assert list(after.columns) == list(before.columns) and "review_group" not in after.columns
    assert after.dtypes.astype(str).tolist() == before.dtypes.astype(str).tolist()
    assert app_state.project.value.alert_sets[SET1].n_groups == 0


def test_the_preview_counts_groups(tmp_path, monkeypatch):
    _, preview = _one_conflict(tmp_path, monkeypatch)
    lines = merge_service.preview_lines(preview)
    assert lines[0] == "Ana: 0 new reviews, 3 conflicts (1 group), 1 unchanged, 0 ignored"
    plain = replace(preview, n_groups=0)
    assert merge_service.preview_lines(plain) == [
        t("share.merge_summary", evaluator="Ana", new=0, conflicts=3, unchanged=1, ignored=0)]


def test_the_preview_counts_a_single_alert_and_several_groups(tmp_path, monkeypatch):
    _, preview = _one_conflict(tmp_path, monkeypatch)
    one = replace(preview, new=[5], conflicts=[0, 1], class_groups={"new": 1, "conflicts": 2, "unchanged": 0})
    assert merge_service.preview_lines(one)[0] == "Ana: 1 new review (1 group), 2 conflicts (2 groups), 1 unchanged, 0 ignored"


@pytest.mark.parametrize("bad", [pd.Series([0.0, 1.5, None, None], dtype=float),
                                 pd.Series(["0", "x", None, None], dtype=object)])
def test_a_return_with_an_unreadable_group_id_is_refused(tmp_path, monkeypatch, bad):
    app_state, ctx, analysis = _grouped_session(tmp_path, monkeypatch, ("Not reviewed",) * 4)
    zp = _grouping_return(tmp_path, ("Confirmed", "Confirmed", "Not reviewed", "Not reviewed"), (0, 0, NA, NA))
    zp = _reviews(zp, tmp_path / "bad.zip", lambda r: r.assign(review_group=bad))
    before = _disk(app_state)
    assert _refused(lambda: _preview(app_state, zp)).key == "share.merge_err_payload"
    after = _disk(app_state)
    assert list(after.columns) == list(before.columns) and after["status"].tolist() == ["Not reviewed"] * 4


def test_an_older_apps_return_that_changes_a_group_member_merges_only_as_mine(tmp_path, monkeypatch):
    """A format-1 return (no ``review_group``) whose evaluator re-reviewed one member of a stored group."""
    statuses = ("Confirmed",) * 3 + ("Not reviewed",)
    p, pdir = make_copy(tmp_path / "copies", statuses=("Confirmed", "False Positive", "Confirmed", "Not reviewed"),
                        assigned=ANA)
    zp = share.build_return(p, pdir, tmp_path / "out", app_version=APP_VERSION)
    app_state, ctx, analysis = _grouped_session(tmp_path, monkeypatch, statuses, (0, 0, 0, NA))
    preview = _preview(app_state, zp)
    assert preview.header.format_version == 1 and _classes(preview) == ([], [0, 1, 2], [], [])
    exc = _refused(lambda: merge_service.merge_return(app_state, ctx, analysis, preview, "theirs"))
    assert exc.key == "share.merge_err_groups"
    assert _disk(app_state)["status"].tolist() == list(statuses)
    record = merge_service.merge_return(app_state, ctx, analysis, _preview(app_state, zp), "mine")
    assert (record.taken, record.conflicts, record.choice) == (0, 3, "mine")
    disk = _disk(app_state)
    assert disk["status"].tolist() == list(statuses) and _plain(disk["review_group"]) == [0, 0, 0, None]
