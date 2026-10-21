"""The GUI's growth index (spec 2026-10-20 §5.3, §6.2–6.3): earlier-alert
states, history summary, period colours, the cache and links into a set."""

from datetime import date, datetime

import geopandas as gpd
import pytest
from shapely.geometry import box

from deforestation_alerts.project.growth import run_id_of
from deforestation_alerts.project.models import AlertSet, AlertSetParams, GridSpec, Period, Project
from deforestation_alerts.scripts.alert_store import save_gdf_to_parquet
from gui.scripts import growth_index, set_store

AUG = Period(id="2026-08-01_2026-08-31", start=date(2026, 8, 1), end=date(2026, 8, 31))
SEP = Period(id="2026-09-01_2026-09-30", start=date(2026, 9, 1), end=date(2026, 9, 30))
OCT = Period(id="2026-10-01_2026-10-31", start=date(2026, 10, 1), end=date(2026, 10, 31))

# ~50 m boxes near (-55, -25), UTM 21S; 0.0004 deg of longitude is ~40 m there
CUR = box(-55.0000, -25.0000, -54.9995, -24.9995)    # Oct #1
SEP12 = box(-55.0005, -25.0000, -55.0000, -24.9995)  # touches CUR on its west side
SEP15 = box(-54.9991, -25.0000, -54.9986, -24.9995)  # ~40 m east of CUR
AUG4 = box(-55.0010, -25.0000, -55.0005, -24.9995)   # touches SEP12, ~50 m from CUR
AUG7 = box(-54.9000, -25.0000, -54.8995, -24.9995)   # ~10 km east

STALE = "19990101000000000000"


def _set(p, sid, period, created_at):
    p.alert_sets[sid] = AlertSet(
        id=sid, period_id=period.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path=f"periods/{period.id}/{sid}/alert_db.parquet", combined_rel_path="c.tif",
        footprint_rel_path="f.tif", fingerprint="x", created_at=created_at)
    return p.alert_sets[sid]


def _gdf(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _row(i, status, shape, links=(), dmin=2026.6, dmax=2026.62):
    return {"alert_id": i, "status": status, "geometry": shape, "area_ha": 0.25,
            "alert_date_min": dmin, "alert_date_max": dmax, "grows_from": list(links)}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    now = datetime(2026, 10, 20, 12, 0, 0)
    p = Project(name="g", created_at=now, updated_at=now)
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=1, height=1)
    for per in (AUG, SEP, OCT):
        p.periods[per.id] = per
    _set(p, "aug", AUG, datetime(2026, 9, 2, 8, 0, 0))
    _set(p, "sep", SEP, datetime(2026, 10, 2, 8, 0, 0))
    _set(p, "oct", OCT, datetime(2026, 10, 19, 8, 0, 0))
    growth_index.clear_cache()
    yield p
    growth_index.clear_cache()


def _link(p, sid, alert_id, run_id=None):
    return f"{sid}#{alert_id}@{run_id or run_id_of(p.alert_sets[sid])}"


def _stores(p, *, sep12_status="Confirmed"):
    aug = _gdf([_row(4, "Confirmed", AUG4, dmin=2026.58, dmax=2026.60),
                _row(7, "Confirmed", AUG7, dmin=2026.58, dmax=2026.60)])
    sep = _gdf([_row(12, sep12_status, SEP12, links=[_link(p, "aug", 4)], dmin=2026.61, dmax=2026.65),
                _row(15, "Need revision", SEP15, dmin=2026.66, dmax=2026.70)])
    return {"aug": aug, "sep": sep}


def _loader(stores, calls=None):
    def load(p_, s):
        if calls is not None:
            calls.append(s.id)
        return stores.get(s.id)
    return load


def _current(links=()):
    return _gdf([_row(1, "Not reviewed", CUR, links=links)])


def test_earlier_view_states(project):
    p = project
    links = [_link(p, "sep", 12), _link(p, "aug", 7, STALE)]
    view = growth_index.earlier_view(p, "oct", _current(links), 1, links, [_link(p, "sep", 12)], 1000,
                                     load=_loader(_stores(p)))
    states = [(it.cand.key, it.state, it.via) for it in view.items]
    assert states == [("sep#12", "auto", None), ("sep#15", "none", None),
                      ("aug#4", "via", "sep#12"), ("aug#7", "broken", None)]
    assert view.n_within == 3 and view.n_linked == 1
    assert view.periods == ((SEP.id, SEP.start), (AUG.id, AUG.start))
    by_key = {it.cand.key: it for it in view.items}
    assert by_key["sep#12"].distance_m < 1 and 30 < by_key["sep#15"].distance_m < 50
    assert by_key["aug#7"].cand.link == _link(p, "aug", 7, STALE)  # the link as stored
    colors = growth_index.period_colors([SEP.id, AUG.id])
    assert by_key["sep#12"].color == colors[SEP.id] and by_key["aug#4"].color == colors[AUG.id]
    assert growth_index.parent_windows(view) == ((2026.61, 2026.65),)


def test_linked_parent_now_false_positive_is_broken(project):
    p = project
    links = [_link(p, "sep", 12)]
    view = growth_index.earlier_view(p, "oct", _current(links), 1, links, [], 1000,
                                     load=_loader(_stores(p, sep12_status="False Positive")))
    states = {it.cand.key: it.state for it in view.items}
    assert states["sep#12"] == "broken" and states["aug#4"] == "none"  # FP bridges nothing
    assert view.n_linked == 0
    assert growth_index.history_summary(p, "oct", _current(links), 1, links,
                                        load=_loader(_stores(p, sep12_status="False Positive"))) is None


def test_set_rerun_breaks_links(project):
    p = project
    links = [_link(p, "sep", 12)]
    stores = _stores(p)
    assert growth_index.history_summary(p, "oct", _current(links), 1, links, load=_loader(stores)).n == 3
    p.alert_sets["sep"] = p.alert_sets["sep"].model_copy(update={"created_at": datetime(2026, 10, 18)})
    view = growth_index.earlier_view(p, "oct", _current(links), 1, links, links, 1000, load=_loader(stores))
    items = [(it.cand.link, it.state) for it in view.items]
    assert (links[0], "broken") in items
    assert (_link(p, "sep", 12), "none") in items  # the re-run's #12 can be linked anew
    assert "via" not in {state for _, state in items}
    assert growth_index.history_summary(p, "oct", _current(links), 1, links, load=_loader(stores)) is None


def test_history_summary_uses_draft_links(project):
    p = project
    load = _loader(_stores(p))
    assert growth_index.history_summary(p, "oct", _current(), 1, [], load=load) is None
    summary = growth_index.history_summary(p, "oct", _current(), 1, [_link(p, "sep", 12)], load=load)
    assert summary.n == 3 and summary.since == AUG.start
    assert summary.confirmed_ha > 0 and summary.unreviewed_ha > 0


def test_period_colors_grey_after_eight():
    ids = [f"p{i}" for i in range(10)]
    colors = growth_index.period_colors(ids)
    assert [colors[i] for i in ids[:8]] == list(growth_index.PERIOD_PALETTE)
    assert len(set(growth_index.PERIOD_PALETTE)) == 8
    assert colors["p8"] == colors["p9"] == growth_index.OLDER_GREY == "#b0bec5"


def test_cache_reloads_after_store_write(project):
    p = project
    stores = _stores(p)
    calls = []
    load = _loader(stores, calls)
    first = growth_index.candidates_for(p, "oct", load=load)
    growth_index.candidates_for(p, "oct", load=load)
    assert sorted(calls) == ["aug", "sep"]
    assert sorted(c.key for c in first) == ["aug#4", "aug#7", "sep#12", "sep#15"]
    store = set_store.store_path(p, p.alert_sets["sep"])
    store.parent.mkdir(parents=True, exist_ok=True)
    save_gdf_to_parquet(stores["sep"], store)
    growth_index.candidates_for(p, "oct", load=load)
    assert sorted(calls) == ["aug", "sep", "sep"]


def test_candidates_in_a_review_copy_come_from_the_snapshot(project, monkeypatch):
    p = project
    monkeypatch.setattr(growth_index.review_copy, "read_only", lambda p_: True)
    calls = []
    assert growth_index.candidates_for(p, "oct", load=_loader(_stores(p), calls)) == []
    assert calls == []


def test_links_into_counts_later_rows(project):
    p = project
    stores = _stores(p)
    stores["oct"] = _gdf([_row(1, "Not reviewed", CUR, links=[_link(p, "sep", 12)]),
                          _row(2, "Not reviewed", CUR, links=[_link(p, "sep", 15, STALE)]),
                          _row(3, "Not reviewed", CUR, links=[_link(p, "aug", 4), "garbage"]),
                          _row(4, "Not reviewed", CUR)])
    load = _loader(stores)
    assert growth_index.links_into(p, "sep", load=load) == 1
    assert growth_index.links_into(p, "aug", load=load) == 2  # sep #12 and oct #3
    assert growth_index.links_into(p, "oct", load=load) == 0


def test_more_sets_than_any_cap_are_all_kept(project):
    """Every call walks every set: none may be evicted while the project holds it."""
    p = project
    for i in range(20):
        per = Period(id=f"2025-{i:02d}", start=date(2025, 1, 1 + i), end=date(2025, 1, 1 + i))
        p.periods[per.id] = per
        _set(p, f"old{i:02d}", per, datetime(2025, 2, 1))
    calls = []
    load = _loader(_stores(p), calls)
    links = [_link(p, "sep", 12)]
    growth_index.earlier_view(p, "oct", _current(links), 1, links, [], 1000, load=load)
    assert len(calls) == len(p.alert_sets) - 1  # every set but the current one, once
    calls.clear()
    growth_index.earlier_view(p, "oct", _current(links), 1, links, [], 1000, load=load)
    growth_index.history_summary(p, "oct", _current(links), 1, links, load=load)
    assert calls == []
    del p.alert_sets["old00"]
    p.alert_sets["sep"] = p.alert_sets["sep"].model_copy(update={"created_at": datetime(2026, 10, 18)})
    growth_index.candidates_for(p, "oct", load=load)
    assert calls == ["sep"]  # a new run reloads; a removed set's entry is dropped
    assert (p.name, "old00") not in growth_index._cache


def test_draft_links_can_remove_a_stored_link(project):
    p = project
    stored = [_link(p, "sep", 12)]
    load = _loader(_stores(p))
    view = growth_index.earlier_view(p, "oct", _current(stored), 1, [], [], 1000, load=load)
    states = {it.cand.key: it.state for it in view.items}
    assert states["sep#12"] == "none" and "linked" not in states.values() and view.n_linked == 0
    assert growth_index.history_summary(p, "oct", _current(stored), 1, [], load=load) is None


def test_period_order_comes_from_candidates_only(project):
    p = project
    stores = _stores(p)
    stores["sep"] = _gdf([_row(12, "False Positive", SEP12)])  # Sep has no candidate left
    links = [_link(p, "sep", 12)]
    view = growth_index.earlier_view(p, "oct", _current(links), 1, links, [], 1000, load=_loader(stores))
    assert view.periods == ((AUG.id, AUG.start),)
    by_key = {it.cand.key: it for it in view.items}
    assert by_key["sep#12"].state == "broken" and by_key["sep#12"].color == growth_index.OLDER_GREY
    assert by_key["aug#4"].color == growth_index.PERIOD_PALETTE[0]


def test_candidates_come_from_earlier_periods_only(project):
    """(from the retired ``earlier_reviews`` tests) a set of the same or a later
    period is no parent; a missing store gives no candidates."""
    p = project
    stores = _stores(p)
    stores["oct"] = _gdf([_row(1, "Confirmed", CUR)])
    load = _loader(stores)
    assert sorted(c.key for c in growth_index.candidates_for(p, "sep", load=load)) == ["aug#4", "aug#7"]
    assert growth_index.candidates_for(p, "aug", load=load) == []
    growth_index.clear_cache()
    assert growth_index.candidates_for(p, "oct", load=lambda p_, s: None) == []


# ---- export columns (Task 9) --------------------------------------------------

def _export_gdf(p):
    return _gdf([_row(1, "Not reviewed", CUR, links=[_link(p, "sep", 12), _link(p, "aug", 7, STALE)]),
                 _row(2, "Not reviewed", AUG7)])


def test_with_growth_columns_kind_links_and_history_area(project):
    p = project
    out = growth_index.with_growth_columns(p, "oct", _export_gdf(p), load=_loader(_stores(p)))
    first, second = out.iloc[0], out.iloc[1]
    assert first["kind"] == "growth"
    assert first["grows_from"] == f"{_link(p, 'sep', 12)};{_link(p, 'aug', 7, STALE)}"  # broken kept
    # CUR + sep#12 + aug#4 (through sep#12), all confirmed or the current one unreviewed
    assert first["history_area_ha"] > 0
    assert second["kind"] == "new" and second["grows_from"] == "" and second["history_area_ha"] == 0.0
    assert "grows_from_auto" not in out.columns and "below_min" not in out.columns


def test_with_growth_columns_small_and_dropped_columns(project):
    p = project
    gdf = _export_gdf(p)
    gdf["grows_from_auto"] = [[], []]
    gdf["below_min"] = [True, False]
    out = growth_index.with_growth_columns(p, "oct", gdf, load=_loader(_stores(p)))
    assert list(out["kind"]) == ["growth_small", "new"]
    assert "grows_from_auto" not in out.columns and "below_min" not in out.columns
    assert "grows_from_auto" in gdf.columns  # the argument is not changed


def test_export_old_set_growth_columns(project):
    # Review Focus 1: a store from before growth links has no growth columns
    p = project
    gdf = _gdf([_row(1, "Not reviewed", CUR), _row(2, "Confirmed", AUG7)]).drop(columns=["grows_from"])
    out = growth_index.with_growth_columns(p, "oct", gdf, load=_loader({}))
    assert list(out["kind"]) == ["new", "new"]
    assert list(out["grows_from"]) == ["", ""]
    assert list(out["history_area_ha"]) == [0.0, 0.0]


def test_with_growth_columns_empty_arrow_list_column(project):
    import pyarrow as pa

    p = project
    gdf = _gdf([_row(1, "Not reviewed", CUR)])
    gdf["grows_from"] = pa.array([[]], type=pa.list_(pa.null())).to_pandas()
    out = growth_index.with_growth_columns(p, "oct", gdf, load=_loader({}))
    assert out["grows_from"].iloc[0] == "" and out["kind"].iloc[0] == "new"


def test_with_growth_columns_loads_each_set_once(project):
    p = project
    calls = []
    gdf = _gdf([_row(1, "Not reviewed", CUR, links=[_link(p, "sep", 12)]),
                _row(2, "Not reviewed", AUG7), _row(3, "Not reviewed", SEP15)])
    growth_index.with_growth_columns(p, "oct", gdf, load=_loader(_stores(p), calls))
    assert sorted(calls) == ["aug", "sep"]


def test_set_histories_largest_first(project):
    p = project
    near_sep15 = box(-54.9991, -24.9995, -54.9986, -24.9990)  # touches SEP15 on its north side
    current = _gdf([
        {**_row(1, "Confirmed", CUR, links=[_link(p, "sep", 12)]), "below_min": False},
        {**_row(2, "Not reviewed", near_sep15, links=[_link(p, "sep", 15)]), "below_min": True},
        {**_row(3, "Not reviewed", box(-54.8000, -25.0000, -54.7995, -24.9995)), "below_min": False},
    ])
    summary = growth_index.set_histories(p, "oct", current, load=_loader(_stores(p)))
    assert (summary.n_growth, summary.n_growth_small, summary.n_reviewed) == (1, 1, 1)
    assert summary.area_ha == pytest.approx(0.5)
    [big, small] = summary.rows
    assert (big.alert_id, big.n) == (1, 3)  # aug 4 + sep 12 + this set's alert
    assert (small.alert_id, small.n) == (2, 2)
    assert big.confirmed_ha > small.confirmed_ha > 0
    assert big.added_ha == pytest.approx(0.27, rel=0.15)  # this set's one ~50 m box
    assert big.first_period.startswith("2026-08-01") and big.last_period.startswith("2026-10-01")
    assert small.first_period.startswith("2026-09-01")


def test_set_histories_old_set_has_no_rows(project):
    summary = growth_index.set_histories(project, "oct", _current(), load=_loader(_stores(project)))
    assert summary == growth_index.GrowthSummary(0, 0, 0.0, 0, ())
