# tests/test_review_groups.py
"""Review-time groups (spec 2026-10-26 group review): identity, plans, what a
plan writes, the outputs' record and the nearby search. Pure: no Solara, no
Earth Engine."""

import geopandas as gpd
import pandas as pd
import pytest
import shapely
from shapely.geometry import box

from deforestation_alerts.project import review_groups as rg

NR = "Not reviewed"
CRS = "EPSG:32721"  # UTM 21S: the alerts below sit at -55°, -24°
#: what ``_save_attributes`` hands ``plan_updates`` for the main
REVIEW = {"status": "Confirmed", "q_loss_driver": "mining", "before_img": "b", "before_img_info": 1,
          "after_img": "a", "after_img_info": 2, "admin1": "Brasil", "admin2": "", "admin3": ""}
#: a drawing over the first two alerts
DRAWING = box(-55.0, -24.0, -54.9985, -23.9995)


def _alerts(n=4, *, statuses=None, groups=None, ids=True, evaluators=None, gap=0.001):
    """``n`` boxes 0.0005° wide in a row along -24°, ``gap``° apart edge to
    edge (0.001° ≈ 102 m there), ids 100, 101..., ``area_ha`` 1, 2...;
    ``groups`` are ``review_group`` ids (None for none)."""
    step = 0.0005 + gap
    polys = [box(-55.0 + i * step, -24.0, -55.0 + i * step + 0.0005, -23.9995) for i in range(n)]
    gdf = gpd.GeoDataFrame({"status": list(statuses or [NR] * n)}, geometry=polys, crs="EPSG:4326")
    if ids:
        gdf["alert_id"] = [100 + i for i in range(n)]
    gdf["bounding_box"] = gpd.GeoSeries(polys, crs="EPSG:4326")
    gdf["area_ha"] = [float(i + 1) for i in range(n)]
    gdf["alert_date_min"] = [2026.010 + i * 0.001 for i in range(n)]
    gdf["alert_date_max"] = [2026.020 + i * 0.001 for i in range(n)]
    gdf["alert_sources"] = ["GLAD-L"] * n
    for col in ("alert_polygon", "before_img", "before_img_info", "after_img", "after_img_info",
                "admin1", "admin2", "admin3"):
        gdf[col] = pd.Series([None] * n, dtype="object")
    gdf["q_loss_driver"] = [""] * n
    if groups is not None:
        gdf[rg.COLUMN] = pd.array(groups, dtype="Int64")
    if evaluators is not None:
        gdf["evaluator"] = evaluators
    return gdf


def _apply(gdf, updates):
    """``updates`` written into a copy, as the save writes them."""
    out = gdf.copy()
    if any(rg.COLUMN in u for u in updates.values()):
        rg.ensure_column(out)
    for position, values in updates.items():
        for column, value in values.items():
            out.at[position, column] = value
    return out


# ---- identity ---------------------------------------------------------------
def test_ids_are_alert_ids_or_positions():
    assert rg.ids(_alerts(3)).tolist() == [100, 101, 102]
    assert rg.ids(_alerts(3, ids=False)).tolist() == [0, 1, 2]


def test_a_store_without_the_column_has_no_groups():
    gdf = _alerts(3)
    assert rg.group_values(gdf).isna().all()
    assert rg.group_of(gdf, 1) == (1, ())
    assert not rg.is_grouped(gdf, 1)


def test_group_of_maps_ids_to_positions():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[101, 101, 101, None])
    assert rg.group_of(gdf, 0) == (1, (0, 2))
    assert rg.group_of(gdf, 1) == (1, (0, 2))
    assert rg.group_of(gdf, 3) == (3, ())
    assert rg.is_grouped(gdf, 2)


def test_group_of_treats_a_group_of_one_as_single():
    gdf = _alerts(2, statuses=["Confirmed", NR], groups=[100, None])
    assert rg.group_of(gdf, 0) == (0, ())
    assert not rg.is_grouped(gdf, 0)


# ---- plans ------------------------------------------------------------------
def test_a_new_group_of_unreviewed_rows_affects_nobody():
    plan = rg.plan_group(_alerts(4), 0, {1, 2})
    assert (plan.kind, plan.main, plan.members, plan.detached, plan.affected) == (
        rg.SAVE, 0, (1, 2), (), ())
    assert plan.size == 3 and plan.positions() == (0, 1, 2)


def test_ticking_a_reviewed_single_overwrites_it():
    plan = rg.plan_group(_alerts(3, statuses=[NR, "Confirmed", NR]), 0, {1})
    assert plan.affected == (rg.Affected(1, "Confirmed", rg.OVERWRITTEN),)


def test_ticking_another_groups_main_absorbs_the_whole_group():
    gdf = _alerts(6, statuses=[NR] * 3 + ["Need revision"] * 3, groups=[None] * 3 + [103] * 3)
    for selected in ({3}, {3, 4}):  # its member ticked too: still one absorption, no move
        plan = rg.plan_group(gdf, 0, selected)
        assert plan.members == (3, 4, 5)
        assert [(a.position, a.change, a.group) for a in plan.affected] == [
            (3, rg.JOINED, 3), (4, rg.JOINED, 3), (5, rg.JOINED, 3)]
        assert plan.detached == ()


def test_ticking_one_member_of_another_group_moves_it_alone():
    gdf = _alerts(6, statuses=[NR] * 3 + ["Confirmed"] * 3, groups=[None] * 3 + [103] * 3)
    plan = rg.plan_group(gdf, 0, {4})
    assert plan.members == (4,)
    assert plan.affected == (rg.Affected(4, "Confirmed", rg.MOVED, 3),)
    assert 3 not in plan.positions()  # 3 keeps 5


def test_unticking_a_member_on_re_save_detaches_it():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100] * 3 + [None])
    plan = rg.plan_group(gdf, 0, {1})
    assert plan.members == (1,) and plan.detached == (2,)
    assert plan.affected == (rg.Affected(2, "Confirmed", rg.REMOVED, 0),)


def test_unticking_every_member_detaches_them_all():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100] * 3 + [None])
    plan = rg.plan_group(gdf, 0, set())
    assert plan.members == () and plan.detached == (1, 2) and plan.size == 1


def test_a_member_opens_its_groups_main():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100] * 3 + [None])
    plan = rg.plan_group(gdf, 2, {1, 2})
    assert plan.main == 0 and plan.members == (1, 2) and plan.affected == ()


@pytest.mark.parametrize("opened, selected", [
    (0, set()), (0, {1, 2}), (0, {3}), (0, {3, 4}), (0, {4}), (0, {3, 1}), (4, {3, 5}), (4, {5}), (1, {0}),
])
def test_plan_size_is_the_plans_size(opened, selected):
    """Review M6: the footer's count, without building the plan, follows
    ``plan_group``'s §4.5 expansion (another group's main brings it whole)."""
    gdf = _alerts(6, statuses=["Confirmed"] * 2 + [NR] + ["Need revision"] * 3,
                  groups=[100, 100, None, 103, 103, 103])
    assert rg.plan_size(gdf, opened, selected) == rg.plan_group(gdf, opened, selected).size


def test_remove_takes_a_member_out():
    gdf = _alerts(3, statuses=["Confirmed"] * 2 + [NR], groups=[100, 100, None])
    plan = rg.plan_remove(gdf, 1)
    assert (plan.kind, plan.main, plan.members, plan.detached) == (rg.REMOVE, 0, (), (1,))
    assert plan.affected == (rg.Affected(1, "Confirmed", rg.REMOVED, 0),)
    with pytest.raises(ValueError):
        rg.plan_remove(gdf, 0)  # the main
    with pytest.raises(ValueError):
        rg.plan_remove(gdf, 2)  # in no group


def test_check_groups_rejects_a_broken_store():
    """Review M5: an explicit exception, not ``assert`` (gone under ``-O``)."""
    assert issubclass(rg.GroupInvariantError, ValueError)
    rg.check_groups(_alerts(3))
    with pytest.raises(rg.GroupInvariantError):  # a Not reviewed row in a group
        rg.check_groups(_alerts(2, statuses=["Confirmed", NR], groups=[100, 100]))
    with pytest.raises(rg.GroupInvariantError):  # two statuses in a group
        rg.check_groups(_alerts(2, statuses=["Confirmed", "Need revision"], groups=[100, 100]))
    with pytest.raises(rg.GroupInvariantError):  # its main is missing
        rg.check_groups(_alerts(2, statuses=["Confirmed"] * 2, groups=[105, 105]))
    with pytest.raises(rg.GroupInvariantError):  # two evaluators in a group
        rg.check_groups(_alerts(2, statuses=["Confirmed"] * 2, groups=[100, 100], evaluators=["ana", "bo"]))


def test_check_groups_accepts_a_group_of_one():
    rg.check_groups(_alerts(2, statuses=["Confirmed"] * 2, groups=[100, None]))
    rg.check_groups(_alerts(2, statuses=["Confirmed"] * 2, groups=[100, 100], evaluators=["ana", "ana"]))


def test_a_plan_over_two_evaluators_is_mixed():
    gdf = _alerts(3, evaluators=["ana", "ana", "bo"])
    assert not rg.mixed_evaluators(gdf, rg.plan_group(gdf, 0, {1}))
    assert rg.mixed_evaluators(gdf, rg.plan_group(gdf, 0, {2}))
    assert not rg.mixed_evaluators(_alerts(3), rg.plan_group(_alerts(3), 0, {2}))


def test_unassigned_counts_as_its_own_evaluator():
    gdf = _alerts(2, evaluators=["ana", ""])
    assert rg.mixed_evaluators(gdf, rg.plan_group(gdf, 0, {1}))


def test_foreign_rows_are_the_other_evaluators():
    gdf = _alerts(3, evaluators=["ana", "bo", ""])
    plan = rg.plan_group(gdf, 0, {1, 2})
    assert rg.foreign_rows(gdf, plan, "ana") == (1, 2)


def test_without_alert_id_a_group_holds_positions():
    gdf = _alerts(3, ids=False)
    out = _apply(gdf, rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1}), REVIEW))
    assert out[rg.COLUMN].tolist()[:2] == [0, 0] and pd.isna(out[rg.COLUMN].iloc[2])
    rg.check_groups(out)


# ---- what a plan writes ------------------------------------------------------
def test_a_group_save_copies_the_review_and_points_members_at_the_main():
    gdf = _alerts(4)
    out = _apply(gdf, rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1, 2}), REVIEW))
    rg.check_groups(out)
    assert out[rg.COLUMN].tolist()[:3] == [100, 100, 100] and pd.isna(out[rg.COLUMN].iloc[3])
    assert out["status"].tolist() == ["Confirmed"] * 3 + [NR]
    assert out["q_loss_driver"].tolist() == ["mining"] * 3 + [""]
    assert out["admin1"].tolist()[:3] == ["Brasil"] * 3
    assert out["area_ha"].tolist() == [1.0, 2.0, 3.0, 4.0]  # no drawing: each keeps its own


def test_members_take_the_mains_answers_to_deleted_questions():
    gdf = _alerts(3)
    gdf["q_gone"] = ["old", "x", "y"]
    out = _apply(gdf, rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1}), REVIEW))
    assert out["q_gone"].tolist() == ["old", "old", "y"]


def test_links_go_to_every_row_of_the_group():
    gdf = _alerts(3)
    gdf["grows_from"] = pd.Series([[], [], []], dtype="object")
    updates = rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1}), REVIEW, links=["s#1@r"])
    assert updates[0]["grows_from"] == ["s#1@r"] and updates[1]["grows_from"] == ["s#1@r"]
    assert updates[0]["grows_from"] is not updates[1]["grows_from"]
    assert 2 not in updates
    assert "grows_from" not in rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1}), REVIEW)[0]


def test_a_detached_row_is_reset_to_unreviewed_with_its_integrate_area():
    gdf = _alerts(3, statuses=["Confirmed"] * 3, groups=[100] * 3)
    gdf["q_loss_driver"] = ["mining"] * 3
    gdf["description"] = ["note"] * 3
    gdf["before_img"] = pd.Series(["b"] * 3, dtype="object")
    gdf["admin1"] = pd.Series(["Brasil"] * 3, dtype="object")
    gdf.at[0, "alert_polygon"] = DRAWING
    gdf["area_ha"] = [5.0, 0.0, 0.0]  # a drawn group: the members' areas are 0
    out = _apply(gdf, rg.plan_updates(gdf, rg.plan_group(gdf, 0, {1}), REVIEW, crs=CRS))
    row = out.iloc[2]
    assert row["status"] == NR and row["q_loss_driver"] == "" and row["description"] == ""
    assert pd.isna(row["before_img"]) and pd.isna(row["admin1"]) and row["alert_polygon"] is None
    assert pd.isna(row[rg.COLUMN])
    assert row["area_ha"] == pytest.approx(rg.polygon_area_ha(gdf.geometry.iloc[2], CRS))
    rg.check_groups(out)


def test_a_detached_row_goes_back_to_its_automatic_links():
    gdf = _alerts(3, statuses=["Confirmed"] * 3, groups=[100] * 3)
    gdf["grows_from"] = pd.Series([["s#1@r"]] * 3, dtype="object")
    gdf["grows_from_auto"] = pd.Series([[], [], ["s#9@r"]], dtype="object")
    updates = rg.plan_updates(gdf, rg.plan_group(gdf, 0, set()), REVIEW)
    assert updates[2]["grows_from"] == ["s#9@r"]
    assert updates[1]["grows_from"] == []
    gdf["grows_from_auto"] = pd.Series([None, None, "s#8@r;s#9@r"], dtype="object")
    assert rg.reset_values(gdf, 2)["grows_from"] == ["s#8@r", "s#9@r"]
    assert rg.reset_values(gdf, 1)["grows_from"] == []
    assert "grows_from" not in rg.reset_values(_alerts(2), 1)  # a store without growth columns


def test_a_removed_member_leaves_its_main_a_single_review():
    gdf = _alerts(2, statuses=["Confirmed"] * 2, groups=[100, 100])
    updates = rg.plan_updates(gdf, rg.plan_remove(gdf, 1))
    assert list(updates) == [1]  # the main keeps its values
    out = _apply(gdf, updates)
    assert out.loc[0, rg.COLUMN] == 100 and out.loc[0, "status"] == "Confirmed"
    assert out.loc[1, "status"] == NR and pd.isna(out.loc[1, rg.COLUMN])
    rg.check_groups(out)


def test_absorbing_a_group_drops_its_polygon():
    gdf = _alerts(5, statuses=[NR] * 3 + ["Confirmed"] * 2, groups=[None] * 3 + [103] * 2)
    gdf.at[3, "alert_polygon"] = DRAWING
    out = _apply(gdf, rg.plan_updates(gdf, rg.plan_group(gdf, 0, {3}), REVIEW))
    assert out.loc[3, "alert_polygon"] is None and out[rg.COLUMN].tolist()[3:] == [100, 100]
    rg.check_groups(out)
    updates = rg.plan_updates(gdf, rg.plan_group(gdf, 0, {4}), REVIEW)
    assert 3 not in updates  # K's main, left alone, is not touched
    out = _apply(gdf, updates)
    assert out.loc[3, rg.COLUMN] == 103 and out.loc[3, "status"] == "Confirmed"
    assert out.loc[3, "alert_polygon"].equals(DRAWING)
    rg.check_groups(out)


def test_own_area_is_the_stored_integrate_area_unless_a_drawing_replaced_it():
    gdf = _alerts(2)
    assert rg.own_area(gdf, 0) == 1.0
    gdf.at[0, "alert_polygon"] = DRAWING
    gdf.at[0, "area_ha"] = 9.9
    assert rg.own_area(gdf, 0, CRS) == pytest.approx(rg.polygon_area_ha(gdf.geometry.iloc[0], CRS))


def test_polygon_area_matches_a_known_utm_area():
    square = gpd.GeoSeries([box(500000, 7300000, 501000, 7301000)], crs=CRS).to_crs(4326).iloc[0]
    assert rg.polygon_area_ha(square, CRS) == pytest.approx(100.0, rel=1e-6)
    assert rg.polygon_area_ha(square) == pytest.approx(100.0, rel=1e-6)  # its own UTM zone is 21S


def test_bounds_span_the_rows_boxes():
    gdf = _alerts(3)
    minx, miny, maxx, maxy = rg.bounds(gdf, [0, 2])
    assert (minx, miny) == pytest.approx(gdf.geometry.iloc[0].bounds[:2])
    assert (maxx, maxy) == pytest.approx(gdf.geometry.iloc[2].bounds[2:])


def test_ensure_column_adds_a_nullable_int_column():
    gdf = _alerts(3)
    rg.ensure_column(gdf)
    assert str(gdf[rg.COLUMN].dtype) == "Int64" and gdf[rg.COLUMN].isna().all()
    for values in (pd.Series([100, None, 100], dtype="object"), pd.Series([100.0, float("nan"), 100.0])):
        gdf = _alerts(3)
        gdf[rg.COLUMN] = values
        rg.ensure_column(gdf)
        assert str(gdf[rg.COLUMN].dtype) == "Int64"
        assert gdf[rg.COLUMN].tolist()[::2] == [100, 100] and pd.isna(gdf[rg.COLUMN].iloc[1])


def test_n_groups_counts_groups_of_two_or_more():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100, 100, 102, None])
    assert rg.n_groups(gdf) == 1
    assert rg.n_groups(_alerts(2)) == 0


def test_grouped_mask_marks_the_rows_of_groups_of_two_or_more():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[100, 100, 102, None])
    assert rg.grouped_mask(gdf).tolist() == [True, True, False, False]
    assert rg.grouped_mask(_alerts(2)).tolist() == [False, False]  # no column


# ---- what the outputs read ---------------------------------------------------
def test_group_shape_is_the_drawing_else_the_union():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[101] * 3 + [None])
    union = shapely.union_all(gdf.geometry.iloc[[0, 1, 2]].to_numpy())
    assert rg.group_shape(gdf, 0).equals(union)
    assert rg.group_shape(gdf, 3).equals(gdf.geometry.iloc[3])
    gdf.at[1, "alert_polygon"] = DRAWING
    assert rg.group_shape(gdf, 2).equals(DRAWING)


def test_group_record_sums_the_group_and_numbers_its_alerts_from_one():
    gdf = _alerts(4, statuses=["Confirmed"] * 3 + [NR], groups=[101] * 3 + [None])
    gdf["alert_sources"] = ["GLAD-L", "RADD; GLAD-L", "RADD", "GLAD-S2"]
    gdf["grows_from"] = pd.Series([[], ["s#1@r", "s#2@r"], [], []], dtype="object")
    row, outline = rg.group_record(gdf, 2)
    assert row["alert_id"] == 101 and row["area_ha"] == 6.0
    assert row["alert_date_min"] == pytest.approx(2026.010)
    assert row["alert_date_max"] == pytest.approx(2026.022)
    assert row["alert_sources"] == "RADD; GLAD-L"  # the main's first
    assert row["grows_from"] == "s#1@r;s#2@r"
    assert row["group_size"] == 3 and row["group_alerts"] == "1, 2, 3"
    assert outline.equals(shapely.union_all(gdf.geometry.iloc[[0, 1, 2]].to_numpy()))
    row, outline = rg.group_record(gdf, 3)
    assert row["group_size"] == 1 and row["group_alerts"] == "4" and row["grows_from"] == ""
    assert outline.equals(gdf.geometry.iloc[3])


def test_group_record_outline_is_the_mains_drawing():
    gdf = _alerts(3, statuses=["Confirmed"] * 2 + [NR], groups=[101, 101, None])
    gdf.at[1, "alert_polygon"] = DRAWING
    assert rg.group_record(gdf, 0)[1].equals(DRAWING)


# ---- the nearby search ------------------------------------------------------
def _spaced(gaps, **kw):
    """Alerts 50 m wide whose edge-to-edge gaps in metres are ``gaps`` (built in UTM, stored in 4326)."""
    gdf = _alerts(len(gaps) + 1, **kw)
    x, boxes = 500_000.0, []
    for i in range(len(gaps) + 1):
        boxes.append(box(x, 7_345_000.0, x + 50.0, 7_345_050.0))
        x += 50.0 + (gaps[i] if i < len(gaps) else 0.0)
    gdf.geometry = gpd.GeoSeries(boxes, crs=CRS).to_crs("EPSG:4326").values
    return gdf


def test_nearby_measures_edge_to_edge_and_lists_the_nearest_first():
    gdf = _alerts(5)
    res = rg.nearby(gdf, 0, 250, CRS)
    assert [r.position for r in res.rows] == [1] and res.more == 0
    assert res.rows[0].distance_m == pytest.approx(101.7, abs=1.0)
    assert res.rows[0].area_ha == 2.0 and res.rows[0].status == NR and res.rows[0].group is None
    assert res.rows[0].date_min == pytest.approx(2026.011)
    assert [r.position for r in rg.nearby(gdf, 0, 300, CRS).rows] == [1, 2]


def test_nearby_reads_statuses_for_the_listed_rows_only(monkeypatch):
    """Review M8: the unreviewed mask is one vector read, not a
    ``status_of`` call per row of the store."""
    gdf = _alerts(40, statuses=["Confirmed", NR] * 20)
    calls = []
    real = rg.status_of
    monkeypatch.setattr(rg, "status_of", lambda g, p: calls.append(p) or real(g, p))
    res = rg.nearby(gdf, 0, 250, CRS)
    assert [r.position for r in res.rows] == [1]  # #3 (reviewed, single) is not offered
    assert len(calls) <= 2 * len(res.rows)


def test_the_radius_is_edge_to_edge_at_ten_metres():
    assert [r.position for r in rg.nearby(_spaced([8.0]), 0, 10, CRS).rows] == [1]
    assert rg.nearby(_spaced([12.0]), 0, 10, CRS).rows == ()


def test_nearby_from_a_group_lists_its_rows_and_measures_from_every_one():
    gdf = _alerts(5, statuses=["Confirmed"] * 2 + [NR] * 3, groups=[100, 100, None, None, None])
    res = rg.nearby(gdf, 0, 150, CRS)
    assert [(r.position, r.distance_m and round(r.distance_m)) for r in res.rows] == [(1, None), (2, 102)]
    assert res.rows[0].group == 0 and res.rows[1].group is None


def test_selected_rows_are_always_listed_first_whatever_their_distance():
    res = rg.nearby(_alerts(5), 0, 150, CRS, include={4})
    assert [r.position for r in res.rows] == [4, 1]


def test_the_limit_counts_what_it_leaves_out():
    res = rg.nearby(_alerts(6), 0, 5000, CRS, limit=2)
    assert [r.position for r in res.rows] == [1, 2] and res.more == 3


def test_the_main_is_never_listed_and_other_evaluators_are_blocked():
    res = rg.nearby(_alerts(3, evaluators=["ana", "bo", "ana"]), 0, 1000, CRS)
    assert [(r.position, r.blocked, r.evaluator) for r in res.rows] == [(1, True, "bo"), (2, False, "ana")]


def test_reviewed_rows_from_before_grouping_are_not_offered():
    gdf = _alerts(4, statuses=[NR, "Confirmed", "Confirmed", NR], groups=[None, None, 102, None])
    assert [r.position for r in rg.nearby(gdf, 0, 1000, CRS).rows] == [2, 3]
    assert [r.position for r in rg.nearby(gdf, 0, 1000, CRS, offer_reviewed=True).rows] == [1, 2, 3]


def test_in_a_copy_rows_of_others_are_blocked():
    gdf = _alerts(4, evaluators=["ana", "ana", "bo", ""])
    res = rg.nearby(gdf, 0, 1000, CRS, evaluator="ana")
    assert [(r.position, r.blocked) for r in res.rows] == [(1, False), (2, True), (3, True)]


def test_the_metric_cache_is_keyed_and_bounded():
    gdf = _alerts(3)
    first = rg._metric(gdf, CRS, ("s", 1))
    assert rg._metric(gdf, CRS, ("s", 1)) is first
    assert rg._metric(gdf, CRS, ("s", 2)) is not first
    assert rg._metric(gdf, CRS, ("s", 1)) is not first  # only the last key is kept


def test_a_keyless_call_bypasses_the_cache_and_keeps_it():
    gdf = _alerts(3)
    first = rg._metric(gdf, CRS, ("s", 1))
    assert rg.nearby(gdf, 0, 300, CRS).rows[0].distance_m == pytest.approx(101.7, abs=1.0)
    assert rg.neighbourhoods(gdf, 300, CRS) == [(0, 1, 2)]
    assert rg._metric(gdf, CRS, ("s", 1)) is first
    assert rg._metric(gdf, "EPSG:32722", ("s", 1)) is not first  # same key, other CRS


# ---- neighbourhoods -----------------------------------------------------------
def test_neighbourhoods_chain_by_single_linkage():
    gdf = _spaced([100, 100, 900, 100])
    assert rg.neighbourhoods(gdf, 500, CRS) == [(0, 1, 2), (3, 4)]


def test_a_stored_group_is_one_unit():
    gdf = _spaced([2000, 2000, 2000, 2000], statuses=["Confirmed"] + [NR] * 3 + ["Confirmed"],
                  groups=[100, None, None, None, 100])
    assert rg.neighbourhoods(gdf, 100, CRS) == [(0, 4), (1,), (2,), (3,)]


def test_ineligible_rows_are_left_out():
    gdf = _spaced([100, 100])
    assert rg.neighbourhoods(gdf, 150, CRS, eligible=[0, 2]) == [(0,), (2,)]
    assert rg.neighbourhoods(gdf, 250, CRS, eligible=[0, 2]) == [(0, 2)]


# ---- neighbourhood_counts (the Overview card) --------------------------------
def test_neighbourhood_counts_sum_up_the_chains():
    gdf = _spaced([100, 100, 900, 100, 900])
    c = rg.neighbourhood_counts(gdf, 500, CRS)
    assert (c.radius_m, c.n_alerts, c.n_units, c.n_alone, c.n_clustered, c.largest) == (500.0, 6, 3, 1, 2, 3)
    assert (c.n_groups, c.n_grouped) == (0, 0)


def test_neighbourhood_counts_count_saved_groups_of_two_rows_or_more():
    gdf = _spaced([2000, 2000, 2000, 2000], statuses=["Confirmed"] + [NR] * 3 + ["Confirmed"],
                  groups=[100, None, 101, None, 100])
    c = rg.neighbourhood_counts(gdf, 100, CRS)
    assert (c.n_units, c.n_alone, c.n_clustered, c.largest) == (4, 3, 1, 2)
    assert (c.n_groups, c.n_grouped) == (1, 2)  # 101 holds one row: no group


def test_neighbourhood_counts_without_alerts():
    c = rg.neighbourhood_counts(_alerts(0), 300, CRS)
    assert (c.n_alerts, c.n_units, c.largest, c.n_groups, c.n_grouped) == (0, 0, 0, 0, 0)
