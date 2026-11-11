from datetime import date

import pandas as pd
import pytest

from deforestation_alerts.engine import vector_dates


@pytest.mark.parametrize("values, order, ambiguous, conflict, valid, skipped", [
    (["2026-08-14", "2026/08/15 10:30"], "iso", False, False, 2, 0),
    ([20260814, 20261301], "yyyymmdd", False, False, 1, 1),
    (["20260814"], "yyyymmdd", False, False, 1, 0),
    (["14/08/2026", "03/04/2026"], "dmy", False, False, 2, 0),
    (["08/14/2026", "03/04/2026"], "mdy", False, False, 2, 0),
    (["03/04/2026", "05.06.2026"], None, True, False, 2, 0),
    (["03/04/2026", "99/01/2026"], None, True, False, 1, 1),   # Codex review case
    (["14/08/2026", "08/14/2026"], None, False, True, 2, 0),
    (["14-08-2026", "2026-01-02", "", None, "garbage"], "dmy", False, False, 2, 1),
])
def test_detect(values, order, ambiguous, conflict, valid, skipped):
    r = vector_dates.detect(pd.Series(values))
    assert (r.order, r.ambiguous, r.conflict, r.valid, r.skipped) == (order, ambiguous, conflict, valid, skipped)


def test_native_dates_from_either_reader():
    as_dt = pd.Series(pd.to_datetime(["2026-08-14 13:00", None]))
    as_obj = pd.Series([date(2026, 8, 14), None], dtype=object)
    for s in (as_dt, as_obj):
        assert vector_dates.detect(s).order == "native"
        assert list(vector_dates.parse(s, "native")) == [2026226, 0]


def test_parse_values():
    s = pd.Series(["03/04/2026", "2026-02-01", "31/02/2026"])
    assert list(vector_dates.parse(s, "dmy")) == [2026093, 2026032, 0]
    assert list(vector_dates.parse(s, "mdy")) == [2026063, 2026032, 0]


def test_contradicts():
    assert vector_dates.contradicts(pd.Series(["03/04/2026", "25/03/2026"]), "mdy")
    assert not vector_dates.contradicts(pd.Series(["03/04/2026"]), "mdy")
    assert vector_dates.contradicts(pd.Series(["2026-08-14"]), "native")


def test_overflowing_numbers_are_skipped():
    s = pd.Series([1e30, 20260814])
    r = vector_dates.detect(s)
    assert (r.order, r.valid, r.skipped) == ("yyyymmdd", 1, 1)
    assert list(vector_dates.parse(s, "yyyymmdd")) == [0, 2026226]


@pytest.mark.parametrize("s", [
    pd.Series([20260814, None], dtype="Int64"),
    pd.Series(["2026-08-14", None], dtype="string"),
])
def test_pd_na_is_blank(s):
    r = vector_dates.detect(s)
    assert (r.valid, r.skipped) == (1, 0)
    assert vector_dates.parse(s, r.order)[1] == 0
    assert not vector_dates.contradicts(s, r.order)


def test_pd_na_is_blank_in_native_contradicts():
    s = pd.Series([date(2026, 8, 14), pd.NA], dtype=object)
    assert not vector_dates.contradicts(s, "native")


def test_contradicts_more():
    assert vector_dates.contradicts(pd.Series(["08/14/2026"]), "dmy")
    assert not vector_dates.contradicts(pd.Series(pd.to_datetime(["2026-08-14"])), "native")


def test_parse_yyyymmdd_float_column():
    s = pd.Series([20260814.0, float("nan")])
    assert list(vector_dates.parse(s, "yyyymmdd")) == [2026226, 0]
