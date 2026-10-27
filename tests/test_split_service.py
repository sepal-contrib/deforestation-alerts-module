"""The evaluator split's store writes (``gui/scripts/split_service.py``, spec
§4.1, §4.4): through the store's lock and a generation guard, as a review
Save writes, refused while the set is re-run, and published to the live
alerts and the set record."""

from datetime import datetime

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project, ReviewCopy
from deforestation_alerts.project.split import SplitError
from deforestation_alerts.scripts.alert_store import load_gdf_from_parquet, save_gdf_to_parquet
from gui.scripts import set_store, split_service
from gui.scripts.integrate_jobs import integrating
from gui.scripts.set_context import SetContext
from gui.store.state_manager import AppState

REL = "periods/per/sets/glad-l"
NOW = datetime(2026, 10, 10)


def _set(params=None):
    return AlertSet(id="x", period_id="per", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                    params=params or AlertSetParams(), store_rel_path=f"{REL}/alert_db.parquet",
                    combined_rel_path=f"{REL}/c.tif", footprint_rel_path=f"{REL}/footprint.tif",
                    fingerprint="f", created_at=NOW)


def _alerts(statuses, set_id="x"):
    n = len(statuses)
    return gpd.GeoDataFrame({"alert_id": list(range(n)), "status": list(statuses), "set_id": [set_id] * n},
                            geometry=[box(i, 0, i + 1, 1) for i in range(n)], crs="EPSG:4326")


def _open(tmp_path, monkeypatch, statuses=("Not reviewed",) * 5, *, params=None, loaded=True):
    """An open project whose active set ``x`` has a store with ``statuses``;
    with ``loaded``, its alerts are on hand as the shell's loader publishes them."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    state = AppState()
    p = Project(name="d", created_at=NOW, updated_at=NOW)
    p.alert_sets["x"] = _set(params)
    p.active_set = "x"
    state.new_project_state(p)
    store = set_store.active_store_path(state.project.value)
    save_gdf_to_parquet(_alerts(statuses), str(store))
    if loaded:
        state.alerts_gdf.set(set_store.load_active_alerts(state.project.value))
    return state, SetContext(state), store


def _column(store):
    return load_gdf_from_parquet(store)["evaluator"].tolist()


def test_a_split_deals_the_unreviewed_alerts_and_records_the_split(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed", "Confirmed", "Not reviewed", "", "Not reviewed"])

    split = split_service.apply_split(state, ctx, [" Ana ", "Bo"], include_reviewed=False)

    assert _column(store) == ["Ana", "", "Bo", "Ana", "Bo"]
    assert split.evaluators == ["Ana", "Bo"] and split.include_reviewed is False and len(split.split_id) == 32
    assert split.packaged is False and split.returns == {}
    assert state.project.value.alert_sets["x"].split == split


def test_include_reviewed_deals_every_alert(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Confirmed", "Not reviewed", "False Positive"])
    split = split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=True)
    assert _column(store) == ["A", "B", "A"] and split.include_reviewed is True


def test_the_live_alerts_carry_the_split_and_the_next_save_keeps_it(tmp_path, monkeypatch):
    """The alerts on hand are republished with the column and the new
    generation: a review Save of them lands and writes the split back, not
    over it."""
    from gui.tile.analysis.review_save import write_store as _save_alerts_to_gdf

    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 3)
    before = state.alerts_gdf.value
    split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)

    live = state.alerts_gdf.value
    assert live is not before and live["evaluator"].tolist() == ["A", "B", "A"]
    assert not set_store.generation_changed(live, store)
    live = live.copy()
    live.loc[1, "status"] = "Confirmed"
    _save_alerts_to_gdf(live, ctx)
    assert _column(store) == ["A", "B", "A"]
    assert load_gdf_from_parquet(store)["status"].tolist() == ["Not reviewed", "Confirmed", "Not reviewed"]


def test_a_save_of_the_alerts_read_before_the_split_is_refused(tmp_path, monkeypatch):
    """Those alerts have no ``evaluator`` column: written back, they would
    erase the split. Their generation is the one before the split's."""
    from gui.tile.analysis.review_save import SaveRefused, write_store as _save_alerts_to_gdf

    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2)
    stale = state.alerts_gdf.value
    split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    state.alerts_gdf.set(stale)  # as if the Save had captured them before the split
    with pytest.raises(SaveRefused) as refused:
        _save_alerts_to_gdf(stale, ctx)
    assert refused.value.key == "analysis_tile.export_labels.save_store_changed"
    assert _column(store) == ["A", "A"]


def test_the_alerts_of_another_set_are_not_republished(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2, loaded=False)
    split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert state.alerts_gdf.value is None  # nothing on hand: the loader reads the new store
    assert _column(store) == ["A", "A"]


def test_a_re_split_replaces_the_split(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 4)
    first = split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)
    second = split_service.apply_split(state, ctx, ["C", "D", "E"], include_reviewed=False)
    assert second.split_id != first.split_id
    assert _column(store) == ["C", "D", "E", "C"]
    assert state.project.value.alert_sets["x"].split == second


@pytest.mark.parametrize("names, key", [([], "evaluators.err.no_names"), (["a", "A"], "evaluators.err.duplicate_name")])
def test_bad_names_are_refused_before_the_store_is_read(tmp_path, monkeypatch, names, key):
    state, ctx, store = _open(tmp_path, monkeypatch)
    with pytest.raises(SplitError) as err:
        split_service.apply_split(state, ctx, names, include_reviewed=False)
    assert err.value.key == key and "evaluator" not in load_gdf_from_parquet(store).columns


def test_no_eligible_alert_writes_nothing(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Confirmed", "Need revision"])
    generation = set_store.store_generation(store)
    with pytest.raises(SplitError) as err:
        split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert err.value.key == "evaluators.err.none_eligible"
    assert set_store.store_generation(store) == generation and state.project.value.alert_sets["x"].split is None


def test_without_an_active_set_or_a_store_the_split_is_refused(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch)
    store.unlink()
    with pytest.raises(SplitError) as err:
        split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert err.value.key == "evaluators.err.no_set"
    state.update_project("d", lambda p: setattr(p, "active_set", None))
    with pytest.raises(SplitError) as err:
        split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert err.value.key == "evaluators.err.no_set"


def test_a_review_copy_cannot_split(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch)
    copy = ReviewCopy(evaluator="Ana", evaluator_index=1, set_id="x", split_id="s", set_fingerprint="f",
                      origin_project="creator", imported_at=NOW)
    state.update_project("d", lambda p: setattr(p, "review_copy", copy))
    for call in (lambda: split_service.apply_split(state, ctx, ["A"], include_reviewed=False),
                 lambda: split_service.remove_split(state, ctx)):
        with pytest.raises(SplitError) as err:
            call()
        assert err.value.key == "review_copy.read_only"
        assert err.value.params == {"evaluator": "Ana", "origin_project": "creator"}


def test_an_evaluator_attribute_layer_refuses_split_and_remove(tmp_path, monkeypatch):
    """Spec §3: a layer made before ``evaluator`` was reserved; the split
    would overwrite its values, Remove would blank them."""
    layer = {"source": "/r.tif", "kind": "local", "bands": [{"band": "b1", "stat": "mean", "column": "evaluator"}]}
    state, ctx, store = _open(tmp_path, monkeypatch, params=AlertSetParams(attribute_layers=[layer]))
    for call in (lambda: split_service.apply_split(state, ctx, ["A"], include_reviewed=False),
                 lambda: split_service.remove_split(state, ctx)):
        with pytest.raises(SplitError) as err:
            call()
        assert err.value.key == "evaluators.err.attribute_clash"


def test_a_store_rewritten_during_the_serialization_is_not_overwritten(tmp_path, monkeypatch):
    """The guard runs right before the replace: a Save that lands while the
    split serializes (another thread, here inline) moves the generation."""
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 3)
    real = gpd.GeoDataFrame.to_parquet

    def _save_lands_meanwhile(self, path, *args, **kwargs):
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", real)  # once
        save_gdf_to_parquet(_alerts(["Confirmed"] * 3), str(store))
        return real(self, path, *args, **kwargs)

    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", _save_lands_meanwhile)
    with pytest.raises(SplitError) as err:
        split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert err.value.key == "evaluators.err.store_changed"
    assert load_gdf_from_parquet(store)["status"].tolist() == ["Confirmed"] * 3
    assert "evaluator" not in load_gdf_from_parquet(store).columns
    assert state.project.value.alert_sets["x"].split is None


def test_a_running_integrate_of_the_set_refuses_the_split(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch)
    with integrating("d", "x"):
        with pytest.raises(SplitError) as err:
            split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert err.value.key == "evaluators.err.integrating"
    with integrating("d", "another-set"), integrating("other-project", "x"):
        split_service.apply_split(state, ctx, ["A"], include_reviewed=False)  # neither is this set's run
    assert _column(store) == ["A"] * 5


def test_an_integrate_starting_during_the_serialization_refuses_the_write(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2)
    real = gpd.GeoDataFrame.to_parquet
    run = integrating("d", "x")

    def _run_starts_meanwhile(self, path, *args, **kwargs):
        monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", real)
        run.__enter__()
        return real(self, path, *args, **kwargs)

    monkeypatch.setattr(gpd.GeoDataFrame, "to_parquet", _run_starts_meanwhile)
    try:
        with pytest.raises(SplitError) as err:
            split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    finally:
        run.__exit__(None, None, None)
    assert err.value.key == "evaluators.err.integrating"
    assert "evaluator" not in load_gdf_from_parquet(store).columns


def test_remove_split_clears_the_column_and_the_record(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 3)
    split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)
    split_service.remove_split(state, ctx)
    assert _column(store) == ["", "", ""]
    assert state.project.value.alert_sets["x"].split is None
    assert state.alerts_gdf.value["evaluator"].tolist() == ["", "", ""]


def test_a_lost_split_record_shows_as_orphan_assignments_and_remove_clears_them(tmp_path, monkeypatch):
    """The store landed but the manifest kept no record (spec §4.4)."""
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2)
    split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    state.update_project("d", lambda p: setattr(p.alert_sets["x"], "split", None))  # the record is lost
    p = state.project.value
    assert split_service.orphan_assignments(p, load_gdf_from_parquet(store)) is True
    split_service.remove_split(state, ctx)
    assert _column(store) == ["", ""]
    assert split_service.orphan_assignments(state.project.value, load_gdf_from_parquet(store)) is False


def test_orphan_assignments_is_false_otherwise(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2)
    p = state.project.value
    assert split_service.orphan_assignments(p, load_gdf_from_parquet(store)) is False  # no column
    assert split_service.orphan_assignments(p, None) is False
    split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    assert split_service.orphan_assignments(state.project.value, load_gdf_from_parquet(store)) is False  # recorded
    layer = {"source": "/r.tif", "kind": "local", "bands": [{"band": "b1", "stat": "mean", "column": "evaluator"}]}
    clash = state.project.value.model_copy(deep=True)
    clash.alert_sets["x"] = _set(AlertSetParams(attribute_layers=[layer]))
    assert split_service.orphan_assignments(clash, load_gdf_from_parquet(store)) is False


def test_remove_split_without_a_column_only_drops_the_record(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch, ["Not reviewed"] * 2)
    split_service.apply_split(state, ctx, ["A"], include_reviewed=False)
    save_gdf_to_parquet(_alerts(["Not reviewed"] * 2), str(store))  # a store without the column
    generation = set_store.store_generation(store)
    split_service.remove_split(state, ctx)
    assert set_store.store_generation(store) == generation
    assert state.project.value.alert_sets["x"].split is None


def _pairs():
    """Four boxes: two pairs ~50 m apart, the pairs ~2 km apart (near -70, -10)."""
    def b(x):
        return box(-70 + x, -10, -70 + x + 0.0009, -10 + 0.0009)

    boxes = [b(0), b(0.0014), b(0.018), b(0.0194)]
    return gpd.GeoDataFrame({"alert_id": [0, 1, 2, 3], "status": ["Not reviewed"] * 4, "set_id": ["x"] * 4},
                            geometry=boxes, crs="EPSG:4326")


def test_a_grouping_split_keeps_neighbours_together(tmp_path, monkeypatch):
    from deforestation_alerts.project.models import GroupReview

    state, ctx, store = _open(tmp_path, monkeypatch)
    save_gdf_to_parquet(_pairs(), str(store))
    state.alerts_gdf.set(set_store.load_active_alerts(state.project.value))
    p = state.project.value
    p.group_review = GroupReview(enabled=True, radius_m=500.0)
    state.project.set(p)

    split = split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)

    col = _column(store)
    assert col[0] == col[1] and col[2] == col[3] and col[0] != col[2]
    assert split.group_radius_m == 500.0 and split.group_include_reviewed is False
    assert split_service.largest_unit(state, 2) == 0


def test_a_split_without_grouping_records_no_radius(tmp_path, monkeypatch):
    state, ctx, store = _open(tmp_path, monkeypatch)
    split = split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)
    assert split.group_radius_m is None and split.group_include_reviewed is False
    assert split_service.largest_unit(state, 2) == 0


def test_largest_unit_reports_a_neighbourhood_above_an_even_share(tmp_path, monkeypatch):
    from deforestation_alerts.project.models import GroupReview

    state, ctx, store = _open(tmp_path, monkeypatch)
    save_gdf_to_parquet(_pairs(), str(store))
    state.alerts_gdf.set(set_store.load_active_alerts(state.project.value))
    p = state.project.value
    p.group_review = GroupReview(enabled=True, radius_m=5000.0)  # all four in one unit
    state.project.set(p)
    split_service.apply_split(state, ctx, ["A", "B"], include_reviewed=False)
    assert split_service.largest_unit(state, 2) == 4
