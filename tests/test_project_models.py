from datetime import date, datetime

import pytest

from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Artifact,
    CustomSource,
    GridSpec,
    Period,
    Project,
    add_predefined_source,
    next_custom_digit,
    period_id,
    remove_source,
    set_id,
)


def _project():
    return Project(name="demo", created_at=datetime(2026, 9, 30), updated_at=datetime(2026, 9, 30))


def test_round_trip_every_kind():
    p = _project()
    add_predefined_source(p, "GLAD-L")
    p.sources["mine"] = CustomSource(name="mine", origin="file", ref="/x.tif", format="ccdc", digit=3)
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=7400000, width=100, height=50)
    p.periods["2026-01-01_2026-01-31"] = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.alert_sets["s"] = AlertSet(
        id="s", period_id="2026-01-01_2026-01-31", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
        params=AlertSetParams(), store_rel_path="a.parquet", combined_rel_path="c.tif",
        footprint_rel_path="f.tif", fingerprint="abc", created_at=datetime(2026, 9, 30),
    )
    p.artifacts["raw:x"] = Artifact(key="raw:x", kind="raw_alert", rel_path="r.tif", fingerprint="f",
                                    inputs={"a": 1}, content_id="c1", created_at=datetime(2026, 9, 30))
    back = Project.model_validate_json(p.model_dump_json())
    assert back == p
    assert isinstance(back.sources["mine"], CustomSource)
    assert back.alert_sets["s"].digit_map == {0: "GLAD-L"}  # int keys survive JSON


def test_predefined_digits_are_fixed_and_unique():
    p = _project()
    assert add_predefined_source(p, "RADD").digit == 1
    assert add_predefined_source(p, "GLAD-S2").digit == 2
    with pytest.raises(ValueError):
        add_predefined_source(p, "RADD")


def test_custom_digit_reuses_the_lowest_free_one():
    p = _project()
    for d in (3, 4, 5):
        p.sources[f"c{d}"] = CustomSource(name=f"c{d}", origin="gee", ref="a", format="radd", digit=d)
    remove_source(p, "c4")
    assert next_custom_digit(p) == 4


def test_custom_digit_cap():
    p = _project()
    for d in range(3, 9):
        p.sources[f"c{d}"] = CustomSource(name=f"c{d}", origin="gee", ref="a", format="radd", digit=d)
    with pytest.raises(ValueError):
        next_custom_digit(p)


def test_grid_transform_and_signature():
    g = GridSpec(epsg="EPSG:32721", resolution=10, xmin=500000, ymax=7400000, width=100, height=50)
    assert g.transform6 == [10.0, 0.0, 500000.0, 0.0, -10.0, 7400000.0]
    assert g.signature == "EPSG:32721|10.0|500000.0|7400000.0|100x50"
    assert g.affine.c == 500000 and g.affine.f == 7400000


def test_period_window_and_ids():
    per = Period(id=period_id(date(2026, 2, 1), date(2026, 2, 28)), start=date(2026, 2, 1), end=date(2026, 2, 28))
    assert per.id == "2026-02-01_2026-02-28"
    assert per.search_window(90) == (date(2025, 11, 3), date(2026, 2, 28))
    assert set_id(per.id, ["RADD", "GLAD-L"]) == "2026-02-01_2026-02-28__glad-l-radd"


def test_locked_once_a_set_exists():
    p = _project()
    assert p.locked is False
    p.alert_sets["s"] = AlertSet(
        id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path="a", combined_rel_path="c", footprint_rel_path="f", fingerprint="f",
        created_at=datetime(2026, 9, 30),
    )
    assert p.locked is True
