from datetime import date, timedelta

from deforestation_alerts.engine import catalogue as cat

# GLAD-L metadata as meta_request returns it: UpdResult's bands, distinct
# across the collection (only some of its images carry conf25).
GLAD_L_META = {
    "end": "2026-09-30",
    "bands": ["conf25", "alertDate25", "conf26", "alertDate26", "obsCount", "obsDate", "swir1", "nir", "red"],
}


def test_date_encodings():
    assert cat.yyyyddd(date(2026, 2, 14)) == 2026045
    assert cat.yyddd(date(2026, 2, 14)) == 26045
    assert cat.yyyyddd(date(2024, 12, 31)) == 2024366


def test_year_periods_split_inclusive():
    assert cat.year_periods(date(2025, 11, 3), date(2026, 2, 28)) == [
        (date(2025, 11, 3), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 2, 28)),
    ]
    assert cat.year_periods(date(2026, 1, 1), date(2026, 1, 31)) == [(date(2026, 1, 1), date(2026, 1, 31))]


def test_covers_uses_catalogue_range():
    meta = {"end": "2026-03-01"}
    assert cat.covers("RADD", meta, date(2026, 1, 1), date(2026, 1, 31))
    assert not cat.covers("RADD", meta, date(2026, 3, 2), date(2026, 3, 31))
    assert not cat.covers("RADD", meta, date(2018, 1, 1), date(2018, 12, 31))  # before 2019
    assert cat.covers("RADD", meta, date(2018, 12, 1), date(2019, 1, 1))  # touches the start day


def test_glad_s2_day_counts_become_yyyyddd_on_every_day():
    # Every day 2019-2026 through the whole-range window, and a window that
    # starts mid-year and crosses New Year (the per-year offsets restart).
    for win_start, win_end in [(date(2019, 1, 1), date(2026, 12, 31)), (date(2025, 11, 3), date(2026, 2, 28))]:
        offsets = cat._glad_s2_offsets(win_start, win_end)
        day = win_start
        while day <= win_end:
            count = (day - cat.GLAD_S2_ORIGIN).days
            # the builder's where() chain: the last year whose first count <= count wins
            add = [a for first, a in offsets if count >= first][-1]
            assert count + add == cat.yyyyddd(day), day
            day += timedelta(days=1)


def test_glad_l_reads_each_year_from_the_collection_that_holds_it():
    periods = cat._glad_l_periods(GLAD_L_META, date(2023, 11, 3), date(2024, 2, 28))
    assert periods == [
        ("projects/glad/alert/2023final", date(2023, 11, 3), date(2023, 12, 31)),
        ("projects/glad/alert/2023final", date(2024, 1, 1), date(2024, 2, 28)),  # there is no 2024final
    ]
    periods = cat._glad_l_periods(GLAD_L_META, date(2025, 11, 3), date(2026, 2, 28))
    assert periods == [
        (cat.GLAD_L_UPD, date(2025, 11, 3), date(2025, 12, 31)),
        (cat.GLAD_L_UPD, date(2026, 1, 1), date(2026, 2, 28)),
    ]
    periods = cat._glad_l_periods(GLAD_L_META, date(2022, 12, 1), date(2023, 1, 31))
    assert [p[0] for p in periods] == ["projects/glad/alert/2022final", "projects/glad/alert/2023final"]
    # nothing holds GLAD-L after 2023final was cut (2024-07-16) until UpdResult's 2025
    assert cat._glad_l_periods(GLAD_L_META, date(2024, 7, 1), date(2025, 1, 31)) == [
        ("projects/glad/alert/2023final", date(2024, 7, 1), cat.GLAD_L_FINAL[2024][1]),
        (cat.GLAD_L_UPD, date(2025, 1, 1), date(2025, 1, 31)),
    ]


def test_glad_l_covers_needs_a_collection_for_the_window():
    assert cat.covers("GLAD-L", GLAD_L_META, date(2024, 1, 1), date(2024, 3, 31))
    assert cat.covers("GLAD-L", GLAD_L_META, date(2025, 11, 3), date(2026, 2, 28))
    assert not cat.covers("GLAD-L", GLAD_L_META, date(2024, 8, 1), date(2024, 8, 31))  # the 2024 gap
    assert cat.covers("GLAD-L", GLAD_L_META, date(2024, 12, 1), date(2025, 1, 31))  # reaches 2025
    assert not cat.covers("GLAD-L", GLAD_L_META, date(2017, 1, 1), date(2017, 12, 31))  # before 2018
    assert not cat.covers("GLAD-L", GLAD_L_META, date(2026, 10, 1), date(2026, 10, 31))  # after the end
