"""Merging a reviewed copy into the creator's set (spec §7): the checks and
the classification of ``share.read_return``, the rows ``share.apply_return``
writes, and ``merge_service.merge_return`` on a real creator folder (the
store write, the republish, the record, the reload of the alert open in
Analysis). The reviewed copies are real zips made by ``build_return``."""

import json
import math
from io import BytesIO

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.project import paths, share
from deforestation_alerts.project.share import ShareError, apply_return, read_return
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet
from gui.i18n import t
from gui.scripts import integrate_jobs, merge_service, set_store
from gui.scripts.error_text import error_text
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.tile.analysis import analysis_tile
from gui.tile.analysis.state import AnalysisState
from tests._share_helpers import AOI_FC, APP_VERSION, CREATOR, SET1, make_copy, make_creator, rezip, zip_members

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
