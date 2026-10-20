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
