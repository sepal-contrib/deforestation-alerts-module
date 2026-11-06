from datetime import date

import pytest

from deforestation_alerts.engine import coverage as cv
from deforestation_alerts.project.models import AoiRecord, Project, RegionCoverage, SourceCoverage

GAP = (date(2024, 7, 17), date(2024, 12, 31))


def _glad_l(regions, fraction=1.0):
    return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction,
                          regions=[RegionCoverage(name=n, fraction=f, first=date(2018, 1, 1), last=last)
                                   for n, f, last in regions], gaps=[GAP])


def _radd(first=date(2020, 1, 1), fraction=1.0):
    return SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=fraction,
                          regions=[RegionCoverage(name="sa", fraction=fraction, first=first, last=date(2026, 9, 23))])


def test_parse_coverage_static_first_days_and_glad_l_gaps():
    raw = {
        "GLAD-L": {"fraction": 1.0, "bands": ["conf25", "alertDate25", "conf26", "alertDate26"],
                   "regions": [{"name": "SA", "fraction": 0.7, "last": "2026-10-01"},
                               {"name": "CA", "fraction": 0.3, "last": "2026-09-30"}]},
        "RADD": {"fraction": 1.0, "regions": [{"name": "sa", "fraction": 1.0, "last": "2026-09-23"}]},
        "GLAD-S2": {"fraction": 0.0, "regions": [{"name": "S2", "fraction": 0.0, "last": "2026-09-30"}]},
        "mine": {"fraction": 0.42, "regions": [{"name": "mine", "fraction": 0.42}]},
    }
    out = cv.parse_coverage(raw, aoi_digest="d", today=date(2026, 10, 1))
    assert out["GLAD-L"].gaps == [GAP] and out["GLAD-L"].regions[0].first == date(2018, 1, 1)
    assert out["RADD"].regions[0].first == date(2020, 1, 1) and out["RADD"].regions[0].last == date(2026, 9, 23)
    assert out["GLAD-S2"].fraction == 0.0
    assert out["mine"].regions[0].first is None and out["mine"].regions[0].last is None
    assert all(c.aoi_digest == "d" and c.checked_on == date(2026, 10, 1) for c in out.values())


def test_radd_africa_starts_in_2019():
    out = cv.parse_coverage({"RADD": {"fraction": 1, "regions": [{"name": "africa", "fraction": 1,
                                                                    "last": "2026-09-23"}]}},
                            aoi_digest="d", today=date(2026, 10, 1))
    assert out["RADD"].regions[0].first == date(2019, 1, 1)


def test_glad_l_gaps_follow_the_collections():
    assert cv.glad_l_gaps(["conf25", "conf26"], date(2026, 10, 1)) == [GAP]
    assert cv.glad_l_gaps(["conf24", "conf25", "conf26"], date(2026, 10, 1)) == []
    assert cv.glad_l_gaps([], None) == []


def test_a_window_inside_the_glad_l_gap_is_no_data():
    state, notes = cv.window_coverage(_glad_l([("SA", 1.0, date(2026, 10, 1))]), "GLAD-L",
                                      date(2024, 8, 1), date(2024, 11, 30))
    assert state == cv.NO_DATA
    assert notes[0].key == "coverage.no_data_gap" and notes[0].kwargs()["start"] == "2024-07-17"


def test_a_window_straddling_the_gap_is_partial_with_the_gap_line():
    state, notes = cv.window_coverage(_glad_l([("SA", 1.0, date(2026, 10, 1))]), "GLAD-L",
                                      date(2024, 6, 1), date(2025, 2, 28))
    assert state == cv.PARTIAL and [n.key for n in notes] == ["coverage.partial_gap"]


def test_regions_with_different_last_days_read_partial_not_no_data():
    cov = _glad_l([("CA", 0.4, date(2026, 9, 30)), ("SA", 0.6, date(2026, 10, 1))])
    state, notes = cv.window_coverage(cov, "GLAD-L", date(2026, 9, 1), date(2026, 10, 1))
    assert state == cv.PARTIAL
    assert [n.key for n in notes] == ["coverage.partial_until"] and notes[0].kwargs()["last"] == "2026-09-30"


def test_radd_starts_2020_over_the_amazon():
    state, notes = cv.window_coverage(_radd(), "RADD", date(2018, 10, 3), date(2019, 12, 31))
    assert state == cv.NO_DATA
    assert notes[0].key == "coverage.no_data_starts" and notes[0].kwargs()["first"] == "2020-01-01"
    state, notes = cv.window_coverage(_radd(), "RADD", date(2019, 12, 1), date(2020, 1, 31))
    assert state == cv.PARTIAL and notes[0].key == "coverage.partial_starts"


def test_after_the_last_day_is_no_data():
    state, notes = cv.window_coverage(_radd(), "RADD", date(2026, 9, 24), date(2026, 9, 30))
    assert state == cv.NO_DATA and notes[0].key == "coverage.no_data_until"


def test_shares():
    zero = SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.0, regions=[])
    state, notes = cv.window_coverage(zero, "GLAD-S2", date(2026, 1, 1), date(2026, 1, 31))
    assert state == cv.NOT_COVERED and notes[0].key == "coverage.not_covered_glad_s2"
    state, notes = cv.window_coverage(zero, "mine", date(2026, 1, 1), date(2026, 1, 31))
    assert state == cv.NOT_COVERED and notes[0].key == "coverage.not_covered"
    state, notes = cv.window_coverage(_radd(fraction=0.35), "RADD", date(2026, 1, 1), date(2026, 1, 31))
    assert state == cv.PARTIAL and notes[0].key == "coverage.partial_share" and notes[0].kwargs()["pct"] == "35"
    assert cv.window_coverage(_radd(), "RADD", date(2026, 1, 1), date(2026, 1, 31)) == (cv.COVERED, ())


def test_a_custom_source_is_never_blocked_on_dates():
    cov = SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=1.0,
                         regions=[RegionCoverage(name="mine", fraction=1.0)])
    assert cv.window_coverage(cov, "mine", date(1990, 1, 1), date(1990, 1, 2)) == (cv.COVERED, ())


def test_no_record_is_unchecked_and_another_aoi_is_no_record():
    assert cv.window_coverage(None, "RADD", date(2026, 1, 1), date(2026, 1, 2))[0] == cv.UNCHECKED
    p = Project(name="p", created_at=date(2026, 1, 1), updated_at=date(2026, 1, 1))
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="other", bounds=(0, 0, 1, 1))
    p.coverage = {"RADD": _radd()}
    assert cv.record_for(p, "RADD") is None
    p.aoi = p.aoi.model_copy(update={"digest": "d"})
    assert cv.record_for(p, "RADD") is p.coverage["RADD"]


@pytest.mark.parametrize("fraction, text", [(0.0, "0"), (0.004, "<1"), (0.35, "35"), (0.995, "100")])
def test_pct_text(fraction, text):
    assert cv.pct_text(fraction) == text


def test_chips_and_dates():
    assert cv.chip_note(None).key == "coverage.chip_unchecked"
    assert cv.chip_note(_radd()).key == "coverage.chip_full"
    assert cv.chip_note(_radd(fraction=0.5)).key == "coverage.chip_partial"
    cov = _glad_l([("CA", 0.4, date(2026, 9, 30)), ("SA", 0.6, date(2026, 10, 1)), ("ASIA", 0.0, date(2026, 9, 30))])
    assert cv.covering_dates(cov) == [("CA", date(2018, 1, 1), date(2026, 9, 30)),
                                      ("SA", date(2018, 1, 1), date(2026, 10, 1))]
    assert cv.data_window(cov) == (date(2018, 1, 1), date(2026, 10, 1))
