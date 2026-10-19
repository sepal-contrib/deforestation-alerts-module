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
    assert per.search_window() == (date(2025, 11, 3), date(2026, 2, 28))
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


# ---------------------------------------------------------------------------
# Attribute layers (spec D3): review focus 2
# ---------------------------------------------------------------------------
LEGACY_CFG = {"source": "/home/u/lc.tif", "kind": "local",
              "bands": [{"band": "lc", "stat": "majority", "column": "lc"}]}
DEM_CFG = {"source": "users/me/dem", "kind": "gee",
           "bands": [{"band": "elevation", "stat": "mean", "column": "elevation", "crs": "EPSG:4326",
                      "scale": 30.0}]}
#: A set's params as saved before attribute_layers existed (the compact JSON
#: ``model_dump_json`` writes).
LEGACY_JSON = ('{"min_size_ha":1.0,"area_mode":"aoi","area_geojson":null,"sorting":3,"max_alerts":25,'
               '"attribute_config":{"source":"/home/u/lc.tif","kind":"local",'
               '"bands":[{"band":"lc","stat":"majority","column":"lc"}]}}')


def test_a_params_dump_without_layers_is_byte_for_byte_what_it_was():
    """Review focus 2: a set's fingerprint hashes ``params.model_dump(mode="json")``.
    The two hashes were taken before ``attribute_layers`` existed."""
    from deforestation_alerts.engine.fingerprint import fingerprint

    plain = AlertSetParams()
    assert list(plain.model_dump(mode="json")) == [
        "min_size_ha", "area_mode", "area_geojson", "sorting", "max_alerts", "attribute_config"]
    assert fingerprint({"params": plain.model_dump(mode="json")}) == (
        "0f7722196cb1889763d8ec9ae112a22eaacf78f9ff779e43651fe0820ffdde30")
    legacy = AlertSetParams.model_validate_json(LEGACY_JSON)
    assert legacy.model_dump_json() == LEGACY_JSON
    assert fingerprint({"params": legacy.model_dump(mode="json")}) == (
        "956ffac4e57e05a029b503f55831063d40abe106f1919ce97420c4c98be78148")
    assert "attribute_layers" not in AlertSetParams(attribute_layers=[]).model_dump_json()


def test_attribute_layers_are_dumped_last_and_round_trip():
    params = AlertSetParams(attribute_layers=[LEGACY_CFG, DEM_CFG])
    dump = params.model_dump(mode="json")
    assert list(dump)[-1] == "attribute_layers" and dump["attribute_layers"] == [LEGACY_CFG, DEM_CFG]
    assert dump["attribute_config"] is None
    assert AlertSetParams.model_validate_json(params.model_dump_json()) == params
    p = _project()
    p.alert_sets["s"] = AlertSet(id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"}, params=params,
                                 store_rel_path="a", combined_rel_path="c", footprint_rel_path="f",
                                 fingerprint="f", created_at=datetime(2026, 9, 30))
    back = Project.model_validate_json(p.model_dump_json())
    assert back.alert_sets["s"].params.attribute_layers == [LEGACY_CFG, DEM_CFG]


def test_attribute_list_reads_the_layers_else_a_legacy_raster():
    assert AlertSetParams().attribute_list() == []
    assert AlertSetParams(attribute_config=LEGACY_CFG).attribute_list() == [LEGACY_CFG]
    assert AlertSetParams(attribute_layers=[DEM_CFG, LEGACY_CFG]).attribute_list() == [DEM_CFG, LEGACY_CFG]
    # the layers win: a new run never writes both, but the list is the newer field
    assert AlertSetParams(attribute_config=LEGACY_CFG, attribute_layers=[DEM_CFG]).attribute_list() == [DEM_CFG]


def test_a_project_saved_before_attribute_layers_loads_its_raster_as_one_layer():
    p = _project()
    p.alert_sets["s"] = AlertSet(id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams.model_validate_json(LEGACY_JSON), store_rel_path="a",
                                 combined_rel_path="c", footprint_rel_path="f", fingerprint="f",
                                 created_at=datetime(2026, 9, 30))
    text = p.model_dump_json()
    assert "attribute_layers" not in text  # what an older manifest holds
    params = Project.model_validate_json(text).alert_sets["s"].params
    assert params.attribute_layers == [] and params.attribute_list() == [LEGACY_CFG]


NOW = datetime(2026, 1, 1)


def test_a_period_stored_without_look_back_takes_the_projects():
    raw = Project(name="x", created_at=NOW, updated_at=NOW).model_dump(mode="json")
    raw["look_back_days"] = 31
    raw["periods"] = {"2026-01-01_2026-01-31": {"id": "2026-01-01_2026-01-31", "start": "2026-01-01",
                                                "end": "2026-01-31", "masks": {}}}
    p = Project.model_validate(raw)
    period = p.periods["2026-01-01_2026-01-31"]
    assert period.look_back_days == 31
    assert period.search_window() == (date(2025, 12, 1), date(2026, 1, 31))


def test_a_new_project_has_no_review_mode_yet():
    assert Project(name="x", created_at=NOW, updated_at=NOW).multi_evaluator is None


@pytest.mark.parametrize("split, expected", [(None, None), ("made", True)])
def test_a_project_saved_before_the_evaluator_toggle_is_multi_when_a_set_was_split(split, expected):
    from deforestation_alerts.project.models import EvaluatorSplit

    p = _project()
    made = EvaluatorSplit(split_id="s1", evaluators=["Ana"], created_at=NOW) if split else None
    p.alert_sets["s"] = AlertSet(id="s", period_id="x", sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", fingerprint="f", created_at=NOW, split=made)
    raw = p.model_dump(mode="json")
    del raw["multi_evaluator"]  # what an older manifest holds
    assert Project.model_validate(raw).multi_evaluator is expected
    # a saved choice is kept as it is
    assert Project.model_validate({**raw, "multi_evaluator": False}).multi_evaluator is False


def test_old_manifest_keeps_every_status(tmp_path):
    """A d438e0b-era manifest (no per-period look-back) reopens with every raw
    and harmonized artifact CURRENT as before."""
    from deforestation_alerts.engine import fingerprint as fp
    from deforestation_alerts.project.models import AoiRecord
    from tests._integrate_helpers import GRID, blank, prepare

    p = Project(name="old", created_at=NOW, updated_at=NOW, look_back_days=31)
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="g", bounds=(0, 0, 1, 1))
    p.grid = GRID
    add_predefined_source(p, "GLAD-L")
    per = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31), look_back_days=31)
    p.periods[per.id] = per
    arr = blank()
    arr[2:10, 2:10] = 1
    prepare(p, tmp_path, per, {"GLAD-L": arr}, 2026015)
    from deforestation_alerts.engine import integrate
    from deforestation_alerts.project.models import AlertSetParams

    res = integrate.run_integration(integrate.IntegrationJob(
        project=p.model_copy(deep=True), pdir=tmp_path, period=per, sources=["GLAD-L"],
        params=AlertSetParams(min_size_ha=0.01)))
    p.artifacts.update(res.artifacts)
    p.alert_sets[res.alert_set.id] = res.alert_set
    assert fp.set_status(p, tmp_path, res.alert_set) is fp.Status.CURRENT
    raw = p.model_dump(mode="json")
    for d in raw["periods"].values():
        del d["look_back_days"]
    p2 = Project.model_validate(raw)
    per2 = p2.periods[per.id]
    assert per2.look_back_days == 31
    assert fp.status(p2, tmp_path, fp.raw_key(per.id, "GLAD-L"),
                     fp.raw_alert_inputs(p2, "GLAD-L", per2)) is fp.Status.CURRENT
    assert fp.status(p2, tmp_path, fp.harm_key(per.id, "GLAD-L"),
                     fp.harmonized_inputs(p2, "GLAD-L", per2)) is fp.Status.CURRENT
    assert fp.set_status(p2, tmp_path, p2.alert_sets[res.alert_set.id]) is fp.Status.CURRENT


def test_empty_confirmed_only_keeps_the_params_dump_unchanged():
    assert "confirmed_only" not in AlertSetParams().model_dump(mode="json")
    assert AlertSetParams(confirmed_only=["RADD"]).model_dump(mode="json")["confirmed_only"] == ["RADD"]
