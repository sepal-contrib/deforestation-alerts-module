"""The Overview dashboard's aggregation (spec: Data layer).

``summarize_alerts`` is pure -- it takes a DataFrame -- so every branch is
covered here without a store, a project or a render harness.
"""

from datetime import date

import pandas as pd
import pytest

from deforestation_alerts.engine.pmtiles import STATUS_CODES
from deforestation_alerts.scripts import set_summary as ss


def _frame(**overrides):
    """Four alerts, one per status, with every summary column filled."""
    data = {
        "alert_id": [10, 11, 12, 13],
        "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
        "area_ha": [2.0, 0.25, 1.5, 0.75],
        "alert_date_min": [2026.045, 2026.045, 2026.052, 2026.010],
        "alert_sources": ["GLAD-L and RADD", "RADD", "GLAD-L", "GLAD-L"],
        "description": ["logging, mining", "", "agriculture_expansion", ""],
        "admin1": ["Canindeyu", "Canindeyu", "Amambay", ""],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def _status(summary, name):
    code = STATUS_CODES[name]
    return next(b for b in summary.by_status if b.code == code)


def test_an_empty_frame_is_zeroed_not_none():
    s = ss.summarize_alerts(pd.DataFrame(), set_id="x")
    assert s.set_id == "x" and s.n_alerts == 0 and s.area_ha == 0.0 and s.n_reviewed == 0
    assert [b.code for b in s.by_status] == [0, 1, 2, 3]
    assert all(b.n == 0 and b.area_ha == 0.0 and b.median_ha == 0.0 for b in s.by_status)
    assert s.by_week == () and s.by_driver == () and s.by_source == () and s.by_admin == ()
    assert s.largest is None


def test_none_is_zeroed_too():
    assert ss.summarize_alerts(None).n_alerts == 0


def test_totals_and_status_breakdown_are_in_code_order():
    s = ss.summarize_alerts(_frame(), set_id="jan")
    assert s.n_alerts == 4 and s.area_ha == pytest.approx(4.5)
    assert s.n_reviewed == 3  # everything but "Not reviewed"
    assert [b.code for b in s.by_status] == [0, 1, 2, 3]
    assert _status(s, "Confirmed").area_ha == pytest.approx(2.0)
    assert _status(s, "Confirmed").median_ha == pytest.approx(2.0)
    assert _status(s, "Not reviewed").n == 1


def test_all_unreviewed_leaves_the_review_only_rollups_empty():
    s = ss.summarize_alerts(_frame(status=["Not reviewed"] * 4))
    assert s.n_reviewed == 0
    assert s.by_driver == () and s.by_admin == ()
    assert s.n_alerts == 4 and _status(s, "Not reviewed").n == 4  # the set itself is still summarised


def test_all_confirmed():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4))
    assert s.n_reviewed == 4 and _status(s, "Confirmed").n == 4
    assert _status(s, "Confirmed").median_ha == pytest.approx(1.125)  # median of 0.25, 0.75, 1.5, 2.0


def test_an_unknown_status_falls_to_not_reviewed():
    s = ss.summarize_alerts(_frame(status=["Reviewed by Bob", None, "Confirmed", "Not reviewed"]))
    assert _status(s, "Not reviewed").n == 3 and _status(s, "Confirmed").n == 1


def test_a_missing_column_does_not_raise():
    frame = _frame().drop(columns=["description", "admin1", "alert_sources", "alert_date_min"])
    s = ss.summarize_alerts(frame)
    assert s.n_alerts == 4 and s.by_week == ()
    assert s.by_source == ((ss.UNKNOWN_SOURCE, pytest.approx(4.5), 4),)
    assert s.by_driver == ((ss.UNKNOWN_DRIVER, pytest.approx(3.75), 3),)
    assert s.by_admin == ((ss.UNATTRIBUTED, pytest.approx(3.75), pytest.approx(2.0), 3),)


def test_a_negative_or_null_area_counts_as_zero():
    s = ss.summarize_alerts(_frame(area_ha=[2.0, None, -4.0, "x"]))
    assert s.area_ha == pytest.approx(2.0)
    assert _status(s, "False Positive").area_ha == 0.0


# --- drivers -------------------------------------------------------------

def test_an_alerts_full_area_counts_under_each_driver_it_names():
    """``description`` is multi-code (loss_drivers._SEP), so the bars may sum
    past the reviewed total -- the spec's ruling."""
    s = ss.summarize_alerts(_frame())
    assert dict((c, n) for c, _, n in s.by_driver) == {
        "logging": 1, "mining": 1, "agriculture_expansion": 1, ss.UNKNOWN_DRIVER: 1}
    areas = dict((c, ha) for c, ha, _ in s.by_driver)
    assert areas["logging"] == pytest.approx(2.0) and areas["mining"] == pytest.approx(2.0)
    assert sum(ha for _, ha, _ in s.by_driver) > s.area_ha - 0.75  # more than the reviewed area


def test_drivers_are_sorted_by_area_descending():
    areas = [ha for _, ha, _ in ss.summarize_alerts(_frame()).by_driver]
    assert areas == sorted(areas, reverse=True)


def test_a_reviewed_alert_with_no_driver_falls_to_unknown():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4, description=[None, "", "   ", "mining"]))
    assert dict((c, n) for c, _, n in s.by_driver) == {ss.UNKNOWN_DRIVER: 3, "mining": 1}


def test_drivers_of_is_used_when_given():
    s = ss.summarize_alerts(_frame(), drivers_of=lambda value: ["only"])
    assert [c for c, _, _ in s.by_driver] == ["only"]


# --- sources -------------------------------------------------------------

def test_a_source_combination_is_one_bar_parsed_not_split():
    """``alert_sources`` is report_builder.format_list prose, so "GLAD-L and
    RADD" is one combination, counted once -- never once per source."""
    s = ss.summarize_alerts(_frame())
    assert dict((k, n) for k, _, n in s.by_source) == {"GLAD-L + RADD": 1, "RADD": 1, "GLAD-L": 2}


def test_a_combination_is_order_insensitive():
    s = ss.summarize_alerts(_frame(alert_sources=["GLAD-L and RADD", "RADD and GLAD-L", "GLAD-L", "GLAD-L"]))
    assert dict((k, n) for k, _, n in s.by_source)["GLAD-L + RADD"] == 2


def test_a_null_source_falls_to_the_unknown_bucket():
    s = ss.summarize_alerts(_frame(alert_sources=[None, "", "RADD", "RADD"]))
    assert dict((k, n) for k, _, n in s.by_source) == {ss.UNKNOWN_SOURCE: 2, "RADD": 2}


# --- admin ---------------------------------------------------------------

def test_admin_is_reviewed_only_and_carries_the_confirmed_area():
    s = ss.summarize_alerts(_frame())
    assert s.by_admin == (("Canindeyu", pytest.approx(2.25), pytest.approx(2.0), 2),
                          ("Amambay", pytest.approx(1.5), 0.0, 1))
    assert sum(n for *_, n in s.by_admin) == s.n_reviewed  # the unreviewed one is absent


def test_a_reviewed_alert_without_an_admin_name_groups_under_the_sentinel():
    s = ss.summarize_alerts(_frame(status=["Confirmed"] * 4, admin1=[None, "", "Amambay", "Amambay"]))
    assert dict((k, n) for k, _, _, n in s.by_admin) == {ss.UNATTRIBUTED: 2, "Amambay": 2}


# --- dates ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (2026.045, date(2026, 2, 14)),   # day of year 45
    (2024.060, date(2024, 2, 29)),   # a leap year's 60th day
    (2026.001, date(2026, 1, 1)),
    (2026.365, date(2026, 12, 31)),
])
def test_decimal_year_to_date_inverts_yyyyddd_to_decimal(value, expected):
    assert ss.decimal_year_to_date(value) == expected


def test_decimal_year_to_date_round_trips_the_engines_own_encoding():
    from deforestation_alerts.engine.vectorize import yyyyddd_to_decimal

    for stamp, expected in ((2026045, date(2026, 2, 14)), (2024060, date(2024, 2, 29))):
        assert ss.decimal_year_to_date(yyyyddd_to_decimal(stamp)) == expected


@pytest.mark.parametrize("value", [None, float("nan"), 0, 0.0, "", "x", -1.0])
def test_decimal_year_to_date_returns_none_for_anything_unusable(value):
    assert ss.decimal_year_to_date(value) is None


# --- weeks (Review focus 1) ---------------------------------------------

def test_weeks_are_mondays_with_the_area_of_their_alerts():
    s = ss.summarize_alerts(_frame())
    assert [w for w, *_ in s.by_week] == [date(2026, 1, 5), date(2026, 2, 9), date(2026, 2, 16)]
    assert dict((w, n) for w, _, n, _ in s.by_week)[date(2026, 2, 9)] == 2  # the two Feb-14 alerts


def test_period_start_marks_the_look_back_weeks():
    s = ss.summarize_alerts(_frame(), period_start=date(2026, 2, 10))
    flags = dict((w, inside) for w, _, _, inside in s.by_week)
    assert flags[date(2026, 1, 5)] is False     # before the period
    assert flags[date(2026, 2, 9)] is True      # the week the period starts in
    assert flags[date(2026, 2, 16)] is True


def test_without_a_period_start_every_week_is_in_period():
    assert all(inside for *_, inside in ss.summarize_alerts(_frame()).by_week)


def test_an_unusable_date_is_skipped_rather_than_raising():
    """Review focus 1: a source harmonized without a date band leaves
    ``alert_date_min`` empty."""
    s = ss.summarize_alerts(_frame(alert_date_min=[None, float("nan"), 0.0, 2026.010]))
    assert [w for w, *_ in s.by_week] == [date(2026, 1, 5)]
    assert s.n_alerts == 4  # the three dateless alerts are still counted everywhere else


# --- sizes and the largest alert -----------------------------------------

def test_size_bins_cover_every_alert_once_and_end_open():
    s = ss.summarize_alerts(_frame())
    assert len(s.size_bins) == len(ss.SIZE_EDGES) + 1
    assert s.size_bins[0][0] == 0.0 and s.size_bins[-1][1] == float("inf")
    assert sum(n for _, _, n, _ in s.size_bins) == s.n_alerts
    assert sum(ha for *_, ha in s.size_bins) == pytest.approx(s.area_ha)


def test_the_largest_alert_carries_its_id_area_admin_and_date():
    assert ss.summarize_alerts(_frame()).largest == (10, pytest.approx(2.0), "Canindeyu", date(2026, 2, 14))


def test_the_largest_alert_may_be_unreviewed_and_dateless():
    s = ss.summarize_alerts(_frame(status=["Not reviewed"] * 4, alert_date_min=[None] * 4))
    assert s.largest[0] == 10 and s.largest[3] is None


# --- the store read ------------------------------------------------------

def test_read_summary_reads_only_the_summary_columns(tmp_path):
    """Constraint 1: the projected read is what makes an open-gate
    unnecessary. The guard is that every name it asks for exists."""
    import pyarrow.parquet as pq

    store = tmp_path / "alert_db.parquet"
    _frame().to_parquet(store)
    assert set(ss.SUMMARY_COLUMNS) <= set(pq.read_schema(store).names)
    s = ss.read_summary(store, "jan", period_start=date(2026, 2, 10))
    assert s.set_id == "jan" and s.n_alerts == 4 and s.n_reviewed == 3


def test_read_summary_survives_a_store_missing_an_optional_column(tmp_path):
    store = tmp_path / "alert_db.parquet"
    _frame().drop(columns=["admin1"]).to_parquet(store)
    assert ss.read_summary(store, "jan").by_admin == ((ss.UNATTRIBUTED, pytest.approx(3.75),
                                                       pytest.approx(2.0), 3),)


def test_read_summary_raises_for_a_missing_store(tmp_path):
    with pytest.raises(Exception):
        ss.read_summary(tmp_path / "gone.parquet", "jan")


def test_the_summary_columns_are_what_a_real_store_carries():
    """Guards against a column rename in vectorize drifting away from the
    reader; the review-time columns come from alert_table instead."""
    from deforestation_alerts.engine.vectorize import COLUMNS

    assert {"area_ha", "alert_date_min"} <= set(COLUMNS)
    assert set(ss.SUMMARY_COLUMNS) - {"alert_id", "status", "alert_sources", "description", "admin1"} <= set(COLUMNS)


def test_the_module_does_not_import_gui_or_solara():
    from pathlib import Path

    src = Path("deforestation_alerts/scripts/set_summary.py").read_text(encoding="utf-8")
    for banned in ("import gui", "from gui", "import solara", "import ipyvuetify"):
        assert banned not in src, banned
