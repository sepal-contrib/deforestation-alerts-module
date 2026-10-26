# tests/test_review_groups.py
"""Review-time groups (spec 2026-10-26 group review): identity, plans, what a
plan writes, the outputs' record and the nearby search. Pure: no Solara, no
Earth Engine."""

import geopandas as gpd
import pandas as pd
import pytest
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


def test_a_group_left_with_its_main_keeps_it_as_a_group_of_one():
    gdf = _alerts(5, statuses=[NR] * 3 + ["Confirmed"] * 2, groups=[None] * 3 + [103] * 2)
    plan = rg.plan_group(gdf, 0, {4})
    assert plan.members == (4,)
    assert 3 not in plan.positions()


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
    assert rg.keep_plan(gdf, 1) == plan


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
    rg.check_groups(_alerts(3))
    with pytest.raises(AssertionError):  # a Not reviewed row in a group
        rg.check_groups(_alerts(2, statuses=["Confirmed", NR], groups=[100, 100]))
    with pytest.raises(AssertionError):  # two statuses in a group
        rg.check_groups(_alerts(2, statuses=["Confirmed", "Need revision"], groups=[100, 100]))
    with pytest.raises(AssertionError):  # its main is missing
        rg.check_groups(_alerts(2, statuses=["Confirmed"] * 2, groups=[105, 105]))
    with pytest.raises(AssertionError):  # two evaluators in a group
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


def test_pre_grouping_is_a_review_without_a_group():
    gdf = _alerts(3, statuses=["Confirmed", "Confirmed", NR], groups=[None, 101, None])
    assert [rg.pre_grouping(gdf, p) for p in range(3)] == [True, False, False]
