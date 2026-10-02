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
    assert g.signature == "EPSG:32721|10.0|500000.0|7400000.0|100x50"
    assert g.affine.c == 500000 and g.affine.f == 7400000


def test_period_window_and_ids():
    per = Period(id=period_id(date(2026, 2, 1), date(2026, 2, 28)), start=date(2026, 2, 1), end=date(2026, 2, 28))
    assert per.id == "2026-02-01_2026-02-28"
    assert per.search_window(90) == (date(2025, 11, 3), date(2026, 2, 28))
    assert set_id(per.id, ["RADD", "GLAD-L"]) == "2026-02-01_2026-02-28__glad-l-radd"


def test_source_edits_replace_the_shared_sources_dict_instead_of_mutating_it():
    """The app publishes shallow copies of the project, so every snapshot
    shares ``sources``; a render iterating it on another thread must not see
    it change size under an add or a remove."""
    p = Project(name="d", created_at=datetime.now(), updated_at=datetime.now())
    add_predefined_source(p, "GLAD-L")
    snapshot = p.model_copy()
    being_iterated = iter(snapshot.sources.values())
    add_predefined_source(p, "RADD")
    remove_source(p, "GLAD-L")
    assert [s.name for s in being_iterated] == ["GLAD-L"]  # no "dictionary changed size during iteration"
    assert list(snapshot.sources) == ["GLAD-L"] and list(p.sources) == ["RADD"]


def test_locked_once_a_set_exists():
    p = _project()
    assert p.locked is False
    p.alert_sets["s"] = AlertSet(
        id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=AlertSetParams(),
        store_rel_path="a", combined_rel_path="c", footprint_rel_path="f", fingerprint="f",
        created_at=datetime(2026, 9, 30),
    )
    assert p.locked is True


from deforestation_alerts.project.models import (  # noqa: E402
    BandMap, NativeGrid, RegionCoverage, SourceCoverage, add_custom_source, sets_using_source,
    sources_slug, update_custom_source,
)


def _add(p, name, **kw):
    fields = dict(origin="gee", ref="users/x/a", asset_type="IMAGE", format="radd", date_encoding="yyddd",
                  band_map=BandMap(alert="Alert", date="Date"),
                  native=NativeGrid(crs="EPSG:4326", scale=10.0))
    fields.update(kw)
    return add_custom_source(p, name=name, **fields)


def test_round_trip_custom_source_coverage_and_stats():
    p = _project()
    _add(p, "Forest RADD")
    _add(p, "ccdc tif", origin="file", ref="/home/u/c.tif", asset_type="FILE", format="ccdc",
         date_encoding="decimal_year", band_map=BandMap(alert=1, date=2),
         native=NativeGrid(crs="EPSG:32721", scale=30.0, transform=(30.0, 0.0, 500000.0, 0.0, -30.0, 7400000.0),
                           bounds=(-56.0, -24.0, -55.0, -23.0)))
    p.coverage["RADD"] = SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=0.5,
                                        regions=[RegionCoverage(name="sa", fraction=0.5, first=date(2020, 1, 1),
                                                                last=date(2026, 9, 23))],
                                        gaps=[(date(2024, 7, 17), date(2024, 12, 31))])
    p.artifacts["harm:x"] = Artifact(key="harm:x", kind="harmonized", rel_path="h.tif", fingerprint="f",
                                     inputs={}, content_id="c", created_at=datetime(2026, 9, 30),
                                     stats={"alert_pixels": 3, "alert_pixels_unmasked": 5,
                                            "date_min": 2026001, "date_max": 2026010})
    back = Project.model_validate_json(p.model_dump_json())
    assert back == p
    assert back.sources["ccdc tif"].band_map.alert == 1 and back.sources["Forest RADD"].band_map.date == "Date"


def test_custom_digits_cap_and_reuse():
    p = _project()
    for i in range(6):
        _add(p, f"s{i}")
    assert sorted(s.digit for s in p.sources.values()) == [3, 4, 5, 6, 7, 8]
    # Controller ruling (Task 7): the cap is a message key too, like every
    # other add_custom_source error, not next_custom_digit's plain string.
    with pytest.raises(ValueError, match="steps.alerts.cap_reached"):
        _add(p, "s6")
    remove_source(p, "s2")
    assert _add(p, "again").digit == 5


def test_add_refuses_a_bad_name():
    with pytest.raises(ValueError, match="steps.alerts.name_reserved"):
        _add(_project(), "RADD")


def test_edit_is_refused_once_a_set_used_the_source():
    p = _project()
    _add(p, "mine")
    update_custom_source(p, "mine", ref="users/x/b")
    assert p.sources["mine"].ref == "users/x/b"
    p.alert_sets = {"x__mine": AlertSet(id="x__mine", period_id="x", sources=["mine"], digit_map={3: "mine"},
                                        params=AlertSetParams(), store_rel_path="s", combined_rel_path="c",
                                        footprint_rel_path="f", fingerprint="f", created_at=datetime.now())}
    assert sets_using_source(p, "mine") == ["x__mine"]
    with pytest.raises(ValueError, match="steps.alerts.edit_used"):
        update_custom_source(p, "mine", ref="users/x/c")


def test_remove_source_drops_a_custom_coverage_record_only():
    p = _project()
    add_predefined_source(p, "RADD")
    _add(p, "mine")
    cov = SourceCoverage(aoi_digest="d", checked_on=date(2026, 10, 1), fraction=1.0)
    p.coverage = {"RADD": cov, "mine": cov}
    remove_source(p, "mine")
    remove_source(p, "RADD")
    assert set(p.coverage) == {"RADD"}  # the three predefined entries are always kept (spec §5.1)


def test_set_ids_use_the_shared_slug():
    assert sources_slug(["RADD", "GLAD-L"]) == "glad-l-radd"
    assert set_id("2026-01-01_2026-01-31", ["RADD", "GLAD-L"]) == "2026-01-01_2026-01-31__glad-l-radd"
