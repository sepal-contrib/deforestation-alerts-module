"""Pure custom-alert-source rules (no Earth Engine)."""

import pytest

from deforestation_alerts.scripts.custom_sources import (
    CUSTOM_KIND,
    MAX_CUSTOM_SOURCES,
    custom_names_from_rasters,
    is_custom_entry,
    normalize_sources,
    remove_custom_alerts_dictionary,
    set_custom_alerts_dictionary,
    slot_value,
    validate_name,
)


def test_slot_values_follow_the_ten_thousands_digit():
    assert [slot_value(s) for s in range(5)] == [10**4, 10**5, 10**6, 10**7, 10**8]


@pytest.mark.parametrize("bad", [-1, 5, 1.0, None])
def test_slot_value_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        slot_value(bad)


@pytest.mark.parametrize(
    "name, existing, current, expected",
    [
        ("", [], None, "empty"),
        ("   ", [], None, "empty"),
        (None, [], None, "empty"),
        ("RADD", [], None, "reserved"),
        ("  radd ", [], None, "reserved"),
        ("glad-s2", [], None, "reserved"),
        ("Foo", [" foo"], None, "duplicate"),
        ("foo", ["Foo", "Bar"], "Foo", None),  # editing its own name
        ("Bar", ["Foo", "Bar"], "Foo", "duplicate"),  # renaming onto another
        ("Sentinel-1", ["Foo"], None, None),
    ],
)
def test_validate_name(name, existing, current, expected):
    assert validate_name(name, existing, current=current) == expected


def test_normalize_sources_cleans_and_caps():
    items = [
        {"name": " A ", "asset": " users/x/a "},
        {"name": "a", "asset": "users/x/dup"},  # duplicate (case) -> dropped
        {"name": "RADD", "asset": "users/x/r"},  # reserved -> dropped
        {"name": "B", "asset": ""},  # no asset -> dropped
        "not a dict",  # dropped
        {"name": "C", "asset": "users/x/c"},
        {"name": "D", "asset": "users/x/d"},
        {"name": "E", "asset": "users/x/e"},
        {"name": "F", "asset": "users/x/f"},
        {"name": "G", "asset": "users/x/g"},  # 6th valid -> truncated
    ]
    out = normalize_sources(items)
    assert out == [
        {"name": "A", "asset": "users/x/a"},
        {"name": "C", "asset": "users/x/c"},
        {"name": "D", "asset": "users/x/d"},
        {"name": "E", "asset": "users/x/e"},
        {"name": "F", "asset": "users/x/f"},
    ]
    assert len(out) == MAX_CUSTOM_SOURCES


@pytest.mark.parametrize("junk", [None, "", "x", {"name": "A"}, 3])
def test_normalize_sources_tolerates_junk(junk):
    assert normalize_sources(junk) == []


def _md():
    return {"GLAD-L": {"collection": "projects/glad/alert/UpdResult"}}


def test_set_custom_assigns_slots_in_order_and_keeps_builtins():
    md = _md()
    set_custom_alerts_dictionary(
        md,
        [{"name": "A", "asset": "users/x/a"}, {"name": "B", "asset": "users/x/b"}],
        "2015-01-01",
        "2026-09-30",
    )
    assert md["A"] == {
        "collection": "users/x/a",
        "kind": CUSTOM_KIND,
        "slot": 0,
        "start_date": "2015-01-01",
        "end_date": "2026-09-30",
    }
    assert md["B"]["slot"] == 1
    assert "GLAD-L" in md


def test_set_custom_replaces_the_whole_set_and_compacts_slots():
    md = _md()
    three = [
        {"name": "A", "asset": "users/x/a"},
        {"name": "B", "asset": "users/x/b"},
        {"name": "C", "asset": "users/x/c"},
    ]
    set_custom_alerts_dictionary(md, three, "2015-01-01", "2026-09-30")
    # delete the middle one and rename the first
    set_custom_alerts_dictionary(
        md,
        [{"name": "A2", "asset": "users/x/a"}, {"name": "C", "asset": "users/x/c"}],
        "2015-01-01",
        "2026-09-30",
    )
    assert "A" not in md and "B" not in md
    assert md["A2"]["slot"] == 0
    assert md["C"]["slot"] == 1
    assert "GLAD-L" in md


def test_set_custom_with_empty_list_clears_and_remove_keeps_builtins():
    md = _md()
    set_custom_alerts_dictionary(md, [{"name": "A", "asset": "a"}], "s", "e")
    set_custom_alerts_dictionary(md, [], "s", "e")
    assert list(md) == ["GLAD-L"]
    set_custom_alerts_dictionary(md, [{"name": "A", "asset": "a"}], "s", "e")
    remove_custom_alerts_dictionary(md)
    assert list(md) == ["GLAD-L"]


def test_set_custom_rejects_more_than_the_cap():
    too_many = [{"name": f"S{i}", "asset": f"a{i}"} for i in range(6)]
    with pytest.raises(ValueError):
        set_custom_alerts_dictionary({}, too_many, "s", "e")


def test_is_custom_entry():
    assert is_custom_entry({"kind": "CUSTOM"})
    assert not is_custom_entry({"collection": "x"})
    assert not is_custom_entry("CUSTOM")


def test_custom_names_from_rasters_reads_only_custom_entries():
    rasters = {
        "RADD": {"alert_raster": object()},
        "S1": {"alert_raster": object(), "custom_slot": 0},
        "Planet": {"alert_raster": object(), "custom_slot": 2},
    }
    assert custom_names_from_rasters(rasters) == {0: "S1", 2: "Planet"}


@pytest.mark.parametrize("unset", ["", [], None])
def test_custom_names_from_rasters_unset_model_values(unset):
    assert custom_names_from_rasters(unset) == {}
