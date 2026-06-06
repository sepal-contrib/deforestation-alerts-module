"""Characterization tests for component/scripts/report_builder.py pure helpers."""

import pytest

from deforestation_alerts.scripts.report_builder import (
    get_unique_alerts,
    format_list,
    parse_formatted_string,
    ensure_list,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1, {"GLAD-L"}),          # ones digit
        (10, {"RADD"}),           # tens digit
        (100, {"GLAD-S2"}),       # hundreds digit
        (1000, {"CCDC"}),         # thousands digit
        (1111, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 1
        (2222, {"GLAD-L", "RADD", "GLAD-S2", "CCDC"}),  # all sources, code 2
        (0, set()),               # no source triggered
    ],
)
def test_get_unique_alerts_decodes_digit_encoding(value, expected):
    assert set(get_unique_alerts([value])) == expected


def test_get_unique_alerts_accepts_stringified_list():
    # Recipe storage round-trips this column as a string; the decoder must parse it.
    assert set(get_unique_alerts("[1, 10]")) == {"GLAD-L", "RADD"}


def test_get_unique_alerts_invalid_string_returns_empty():
    assert get_unique_alerts("not a list") == []


def test_format_list_join_rules():
    assert format_list([]) == ""
    assert format_list(["GLAD-L"]) == "GLAD-L"
    assert format_list(["GLAD-L", "RADD"]) == "GLAD-L and RADD"
    assert format_list(["GLAD-L", "RADD", "CCDC"]) == "GLAD-L, RADD and CCDC"


def test_parse_formatted_string_is_inverse_of_format_list():
    for items in (["GLAD-L"], ["GLAD-L", "RADD"], ["GLAD-L", "RADD", "CCDC"]):
        assert parse_formatted_string(format_list(items)) == items
    assert parse_formatted_string("") == []


def test_ensure_list():
    assert ensure_list("x") == ["x"]
    assert ensure_list(["x", "y"]) == ["x", "y"]
    with pytest.raises(ValueError):
        ensure_list(123)
