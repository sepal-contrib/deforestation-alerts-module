from datetime import date, datetime

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from deforestation_alerts.project.growth import (
    LinkRef, build_graph, candidates_of_set, format_link, history_area, kind_of, links_of,
    node_key, nodes_of_set, parse_link, run_id_of)
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Period
from deforestation_alerts.project.review_status import CONFIRMED, FALSE_POSITIVE, NEED_REVISION, NOT_REVIEWED


def _set(sid="s1", created=None):
    return AlertSet(id=sid, period_id="p", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
                    store_rel_path="a", combined_rel_path="b", footprint_rel_path="c", fingerprint="f",
                    created_at=created or datetime(2026, 10, 1, 12, 0, 0, 123))


def _period(pid="p", start=date(2026, 8, 1)):
    return Period(id=pid, start=start, end=date(2026, 8, 31))


def _gdf(rows):
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def _sq(x, y=-1.0, d=0.001):
    return box(x, y, x + d, y + d)


def _node(sid, aid, status=NOT_REVIEWED, links=(), shape=None, start=date(2026, 8, 1)):
    s = _set(sid)
    g = _gdf({"alert_id": [aid], "status": [status], "area_ha": [1.0], "geometry": [shape or _sq(-57)],
              "grows_from": [list(links)]})
    return nodes_of_set(s, _period(sid, start), g)[0]


def test_link_round_trip():
    ref = LinkRef("2026-09-01_2026-09-30__glad-l", 12, "20261001120000000000")
    assert parse_link(format_link(ref)) == ref
    assert parse_link("x") is None
    assert parse_link("a#b@c") is None


def test_links_of_normalises_cells():
    assert links_of(None) == []
    assert links_of(float("nan")) == []
    assert links_of("") == []
    assert links_of(np.array(["a#1@r", "a#1@r", "b#2@r"])) == ["a#1@r", "b#2@r"]
    assert links_of("a#1@r;b#2@r") == ["a#1@r", "b#2@r"]


def test_kind_of():
    assert kind_of(["x"], False) == "growth"
    assert kind_of(["x"], True) == "growth_small"
    assert kind_of([], False) == "new"
    assert kind_of([], True) == "new_small"
    assert kind_of([], None) == "new"


def test_run_id_of_uses_created_at():
    assert run_id_of(_set()) == "20261001120000000123"


def test_candidates_keep_only_loss_verdicts_and_prefer_drawn_shape():
    drawn = _sq(-57, d=0.002)
    g = _gdf({"alert_id": [1, 2, 3], "status": [CONFIRMED, NEED_REVISION, FALSE_POSITIVE],
              "area_ha": [1.0] * 3, "geometry": [_sq(-57), _sq(-56.9), _sq(-56.8)],
              "alert_polygon": [drawn, None, None]})
    cands = candidates_of_set(_set(), _period(), g)
    assert [c.node.alert_id for c in cands] == [1, 2]
    assert cands[0].node.shape.equals(drawn)
    assert cands[1].node.shape.equals(_sq(-56.9))
    assert cands[0].link == format_link(LinkRef("s1", 1, run_id_of(_set())))


def _chain(b_status=CONFIRMED):
    a = _node("A", 1, CONFIRMED, start=date(2026, 8, 1))
    b = _node("B", 1, b_status, links=["A#1@rA"], start=date(2026, 9, 1))
    c = _node("C", 1, NOT_REVIEWED, links=["B#1@rB"], start=date(2026, 10, 1))
    return a, b, c


RUNS = {"A": "rA", "B": "rB", "C": "rC"}


def test_graph_components_follow_links_both_ways():
    a, b, c = _chain()
    g = build_graph([a, b, c], RUNS)
    full = frozenset({a.key, b.key, c.key})
    assert g.component(c.key) == full
    assert g.component(a.key) == full


def test_false_positive_neither_joins_nor_bridges():
    a, b, c = _chain(FALSE_POSITIVE)
    g = build_graph([a, b, c], RUNS)
    assert g.component(c.key) == {c.key}
    assert g.component(a.key) == {a.key}
    assert g.component(b.key) == frozenset()


def test_run_mismatch_breaks_link():
    a, b, c = _chain()
    g = build_graph([a, b, c], {**RUNS, "A": "other"})
    assert g.is_broken("A#1@rA")
    assert not g.is_broken("B#1@rB")
    assert a.key not in g.component(c.key)
    assert g.component("Z#9") == {"Z#9"}
    assert g.node(node_key("A", 1)) is a


def test_history_area_unions_parallel_sets():
    side = 0.0009  # about 100 m
    sq = _sq(-57, d=side)
    nodes = [_node("A", 1, CONFIRMED, shape=sq), _node("B", 1, CONFIRMED, shape=sq),
             _node("C", 1, NOT_REVIEWED, shape=_sq(-57 + side, d=side)),
             _node("D", 1, FALSE_POSITIVE, shape=sq)]
    h = history_area(nodes, "EPSG:32721")
    assert h.n == 3
    assert abs(h.confirmed_ha - 1.0) < 0.05
    assert abs(h.unreviewed_ha - 1.0) < 0.05
    assert h.first_period_start == date(2026, 8, 1)


# ---- the review package's snapshot of earlier alerts (spec §6.6) -------------

def _row_of(sid, aid, status, links=(), shape=None, run="r"):
    from deforestation_alerts.project.growth import Candidate

    return Candidate(_node(sid, aid, status, links, shape), f"{run}{sid}")


def test_snapshot_round_trip(tmp_path):
    from dataclasses import replace

    from deforestation_alerts.project.growth import read_snapshot, write_snapshot

    rows = [_row_of("A", 1, CONFIRMED, shape=_sq(-57)),
            _row_of("A", 2, FALSE_POSITIVE, links=["Z#1@rZ", "Z#2@rZ"], shape=_sq(-56.9)),
            _row_of("B", 3, NOT_REVIEWED, shape=_sq(-56.8))]
    rows[0] = replace(rows[0], node=replace(rows[0].node, date_min=2026.6, date_max=2026.62, area_ha=2.5))
    path = write_snapshot(rows, tmp_path / "earlier_alerts.parquet")
    back = read_snapshot(path)
    assert back == rows
    assert back[1].node.links == ("Z#1@rZ", "Z#2@rZ") and back[2].node.date_min is None
    assert read_snapshot(write_snapshot([], tmp_path / "empty.parquet")) == []


def test_snapshot_rows_hold_the_candidates_and_every_linked_row():
    """Ruling 12: the eligible rows, plus every row named by a link (from the
    shared set's alerts or from a row already in), whatever its status."""
    from deforestation_alerts.project.growth import snapshot_rows

    pool = [_row_of("A", 1, NOT_REVIEWED),                       # named via B#2 only
            _row_of("A", 2, CONFIRMED),                          # a candidate
            _row_of("A", 3, NOT_REVIEWED),                       # named by nothing
            _row_of("B", 1, FALSE_POSITIVE),                     # named by the shared set
            _row_of("B", 2, FALSE_POSITIVE, links=["A#1@rA"]),   # named by a candidate
            _row_of("B", 3, NEED_REVISION, links=["B#2@rB", "gone#1@x"])]
    got = snapshot_rows(pool, ["B#1@rB", "nonsense"])
    assert [r.key for r in got] == ["A#2", "B#3", "A#1", "B#1", "B#2"]
    assert {r.key: r.node.status for r in got}["B#1"] == FALSE_POSITIVE


def _nodes_by_rows(s, period, gdf):
    """``nodes_of_set`` as it was, row by row (``iterrows``): the column-wise
    one must give exactly the same nodes."""
    import pandas as pd

    from deforestation_alerts.project.growth import Node, _float, parent_shape

    def _number(row, name):
        return _float(row[name]) if name in row else None

    has_links = "grows_from" in gdf.columns
    out = []
    for _, row in gdf.iterrows():
        status = row["status"] if "status" in row else None
        out.append(Node(
            set_id=s.id, alert_id=int(row["alert_id"]), period_id=period.id,
            period_start=period.start, period_end=period.end,
            status=NOT_REVIEWED if status is None or status is pd.NA or pd.isna(status) or status == "" else status,
            area_ha=_number(row, "area_ha") or 0.0,
            date_min=_number(row, "alert_date_min"), date_max=_number(row, "alert_date_max"),
            shape=parent_shape(row),
            links=tuple(links_of(row["grows_from"])) if has_links else ()))
    return out


def test_nodes_of_set_column_wise_matches_row_by_row():
    from shapely.geometry import Polygon

    link = "s0#1@20260101000000000000"
    full = _gdf({
        "alert_id": [0, 1, 2, 3, 4],
        "status": [CONFIRMED, None, np.nan, "", FALSE_POSITIVE],
        "area_ha": [1.5, np.nan, 0.0, 2.0, 3.0],
        "alert_date_min": [2026.1, np.nan, 2026.2, 2026.3, 2026.4],
        "alert_date_max": [2026.2, 2026.3, np.nan, 2026.4, 2026.5],
        "alert_polygon": [_sq(5), None, Polygon(), _sq(6).wkb, None],
        "grows_from": [[link], None, f"{link};{link}", np.array([link], dtype=object), []],
        "geometry": [_sq(0), _sq(1), _sq(2), None, _sq(4)],
    })
    bare = _gdf({"alert_id": [7, 8], "geometry": [_sq(0), None]})
    for g in (full, bare, full.iloc[0:0]):
        assert nodes_of_set(_set(), _period(), g) == _nodes_by_rows(_set(), _period(), g)


# ---- group review (spec 2026-10-26 §7): a group is one patch -------------------

def _grouped(drawn=None, groups=(0, 0, 0, None), statuses=(CONFIRMED,) * 4):
    """Rows 0-2 a group (main 0) unless ``groups`` says otherwise; row 3 alone."""
    import pandas as pd

    return _gdf({"alert_id": [0, 1, 2, 3], "status": list(statuses), "area_ha": [1.0, 2.0, 3.0, 4.0],
                 "geometry": [_sq(-57), _sq(-56.999), _sq(-56.9), _sq(-56.5)],
                 "alert_polygon": [drawn, None, None, None],
                 "review_group": pd.array(list(groups), dtype="Int64")})


def test_a_drawn_groups_members_have_no_shape():
    drawn = _sq(-57, d=0.003)
    nodes = nodes_of_set(_set(), _period(), _grouped(drawn))
    assert nodes[0].shape.equals(drawn)
    assert nodes[1].shape is None and nodes[2].shape is None
    assert nodes[3].shape.equals(_sq(-56.5))
    assert [n.review_group for n in nodes] == [0, 0, 0, None]


def test_an_undrawn_groups_members_keep_their_own():
    nodes = nodes_of_set(_set(), _period(), _grouped())
    assert [n.shape.equals(g) for n, g in zip(nodes, (_sq(-57), _sq(-56.999), _sq(-56.9), _sq(-56.5)))] == [True] * 4


def test_a_store_without_review_group_has_no_groups():
    g = _grouped().drop(columns=["review_group"])
    assert [n.review_group for n in nodes_of_set(_set(), _period(), g)] == [None] * 4


def test_group_members_are_one_history():
    nodes = nodes_of_set(_set(), _period(), _grouped(_sq(-57, d=0.003)))
    graph = build_graph(nodes, {"s1": run_id_of(_set())})
    assert graph.component("s1#0") == {"s1#0", "s1#1", "s1#2"}
    assert graph.component("s1#2") == {"s1#0", "s1#1", "s1#2"}
    assert graph.component("s1#3") == {"s1#3"}


def test_group_candidates_fold_members_into_the_main():
    import shapely

    from deforestation_alerts.project.growth import group_candidates

    # row 3 a group of one
    nodes = nodes_of_set(_set(), _period(), _grouped(groups=(0, 0, 0, 3)))
    got = group_candidates(nodes)
    assert [n.key for n in got] == ["s1#0", "s1#3"]
    assert got[0].area_ha == 6.0 and got[0].status == CONFIRMED
    assert got[0].shape.equals(shapely.union_all([_sq(-57), _sq(-56.999), _sq(-56.9)]))
    assert got[1] == nodes[3]
    # a drawn group: its drawing
    drawn = _sq(-57, d=0.003)
    got = group_candidates(nodes_of_set(_set(), _period(), _grouped(drawn)))
    assert [n.key for n in got] == ["s1#0", "s1#3"] and got[0].shape.equals(drawn) and got[0].area_ha == 6.0
    # candidates_of_set folds too; a false-positive group is no candidate
    assert [c.key for c in candidates_of_set(_set(), _period(), _grouped(drawn))] == ["s1#0", "s1#3"]
    fp = _grouped(statuses=(FALSE_POSITIVE,) * 3 + (CONFIRMED,))
    assert [c.key for c in candidates_of_set(_set(), _period(), fp)] == ["s1#3"]


def test_the_snapshot_keeps_a_drawn_groups_members(tmp_path):
    from deforestation_alerts.project.growth import read_snapshot, rows_of_set, snapshot_rows, write_snapshot

    s = _set()
    pool = rows_of_set(s, _period(), _grouped(_sq(-57, d=0.003), statuses=(CONFIRMED,) * 3 + (NOT_REVIEWED,)))
    got = snapshot_rows(pool, [])
    assert [r.key for r in got] == ["s1#0", "s1#1", "s1#2"]
    run_ids = {"s1": run_id_of(s)}
    creator = build_graph([r.node for r in pool], run_ids)
    copy = build_graph([r.node for r in read_snapshot(write_snapshot(got, tmp_path / "snap.parquet"))], run_ids)
    assert copy.component("s1#0") == creator.component("s1#0") == {"s1#0", "s1#1", "s1#2"}
