"""End to end, without Earth Engine (spec 2026-10-10 §10 "Integration"):
the creator splits a set between two evaluators and builds their packages;
each evaluator imports theirs, reviews their own alerts through the review
Save's write path and builds a reviewed copy; the creator merges both, one
of them with a conflict taken from the evaluator. Every step is the real
code: split_service, share, merge_service, review_save.write_store."""

from deforestation_alerts.project import io, paths, share
from deforestation_alerts.project.questions import column
from deforestation_alerts.project.review_status import is_reviewed
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet
from gui.scripts import merge_service, set_store, split_service
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState
from gui.store.workflow_steps import active_set
from gui.tile.analysis.review_save import write_store as _save_alerts_to_gdf
from gui.tile.analysis.state import AnalysisState
from tests._share_helpers import AOI_FC, APP_VERSION, CAUSE, SET1, make_creator

N = 8


def _open(p):
    """An AppState with ``p`` open and its active set's alerts published, as
    the shell's set loader leaves it; and the SetContext Analysis saves through."""
    state = AppState()
    state.load_project_state(p)
    s = active_set(p)
    assert set_store.publish_set_alerts(state, p.name, s, set_store.load_set_alerts(p, s))
    return state, SetContext(state)


def _review(state, ctx, evaluator, verdict, answer, n=2) -> list[int]:
    """Review ``evaluator``'s first ``n`` alerts as a Save does: edit the
    loaded alerts and write them through the Save's own write path. Returns
    their alert ids."""
    staged = state.alerts_gdf.value.copy()  # keeps the store generation it was read with
    rows = staged.index[staged["evaluator"] == evaluator][:n]
    if column(CAUSE) not in staged.columns:
        staged[column(CAUSE)] = ""
    staged.loc[rows, "status"] = verdict
    staged.loc[rows, column(CAUSE)] = answer
    _save_alerts_to_gdf(staged, ctx)
    state.alerts_gdf.set(staged)
    return [int(i) for i in staged.loc[rows, "alert_id"]]


def test_two_evaluators_review_their_halves_and_the_creator_merges_both(data_root, tmp_path):
    creator, pdir = make_creator(data_root, statuses=("Not reviewed",) * N, assigned=None, imagery=False)
    # The helper's creator comes split already; this one starts unsplit.
    creator.alert_sets[SET1] = creator.alert_sets[SET1].model_copy(update={"split": None})
    io.save_project(creator, data_root)
    state, ctx = _open(creator)

    made = split_service.apply_split(state, ctx, ["Ana", "Bo"], False)
    assert made.evaluators == ["Ana", "Bo"]
    assert list(state.alerts_gdf.value["evaluator"]) == ["Ana", "Bo"] * (N // 2)

    p = state.project.value
    out = tmp_path / "sent"
    packages = {name: share.build_package(p, pdir, SET1, name, AOI_FC, out, app_version=APP_VERSION)
                for name in made.evaluators}

    # The creator reviews Bo's first alert before Bo's copy comes back: a conflict.
    (conflict_id,) = _review(state, ctx, "Bo", "False Positive", "", n=1)

    returned = {}
    for name in made.evaluators:
        copy = share.import_package(packages[name])
        assert copy.review_copy.evaluator == name and copy.name != creator.name
        cstate, cctx = _open(copy)
        reviewed = _review(cstate, cctx, name, "Confirmed", "fire")
        zip_path = share.build_return(cstate.project.value, paths.project_dir(copy.name), out,
                                      app_version=APP_VERSION)
        returned[name] = (zip_path, reviewed)

    analysis = AnalysisState()
    ana = share.read_return(returned["Ana"][0], state.project.value, state.alerts_gdf.value)
    assert (len(ana.new), ana.conflicts, ana.ignored) == (2, [], [])
    ret_ana = merge_service.merge_return(state, ctx, analysis, ana, "mine")
    bo = share.read_return(returned["Bo"][0], state.project.value, state.alerts_gdf.value)
    assert bo.conflicts == [conflict_id] and len(bo.new) == 1
    ret_bo = merge_service.merge_return(state, ctx, analysis, bo, "theirs")
    assert (ret_ana.taken, ret_ana.conflicts) == (2, 0)
    assert (ret_bo.taken, ret_bo.conflicts, ret_bo.choice) == (2, 1, "theirs")

    p = state.project.value
    store = load_gdf_from_parquet(set_store.store_path(p, active_set(p))).set_index("alert_id")
    ids = returned["Ana"][1] + returned["Bo"][1]
    assert conflict_id in ids
    assert sorted(i for i in store.index if is_reviewed(store.loc[i, "status"])) == sorted(ids)
    assert all(store.loc[i, "status"] == "Confirmed" for i in ids)  # Bo's review won the conflict
    assert all(store.loc[i, column(CAUSE)] == "fire" for i in ids)  # the creator's store gained the column
    assert list(store["evaluator"]) == ["Ana", "Bo"] * (N // 2)
    s = p.alert_sets[SET1]
    assert s.n_reviewed == 4 and set(s.split.returns) == {"Ana", "Bo"}
