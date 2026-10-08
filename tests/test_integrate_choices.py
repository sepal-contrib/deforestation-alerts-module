import threading
from datetime import date, datetime
from types import SimpleNamespace

from deforestation_alerts.engine.coverage import Note
from deforestation_alerts.engine.fingerprint import Status
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project.models import AoiRecord, Period, Project, add_predefined_source
from deforestation_alerts.scripts.raster_attribute import build_attribute_config, display_column
from gui.scripts import integrate_jobs
from gui.tile import integrate_step

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))


def _p():
    p = Project(name="i", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    for s in ("GLAD-L", "RADD", "GLAD-S2"):
        add_predefined_source(p, s)
    return p


def test_choices_list_ready_sources_and_explain_the_left_out(monkeypatch):
    rows = [
        PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT, "covered", (),
                   stats={"alert_pixels": 0, "alert_pixels_unmasked": 12}),
        PrepareRow("RADD", Status.MISSING, Status.MISSING, "no_data",
                   (Note("coverage.no_data_starts", (("source", "RADD"), ("first", "2020-01-01")), "error"),)),
        PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                   (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)),
    ]
    monkeypatch.setattr(integrate_step, "plan_prepare", lambda p, pdir, period: rows)
    out = {c["source"]: c for c in integrate_step.integrate_choices(_p(), "/tmp/x", JAN)}
    assert out["GLAD-L"]["enabled"] and out["GLAD-L"]["note"] == "ready · all 12 alert pixels removed by masks"
    assert not out["RADD"]["enabled"] and out["RADD"]["note"] == "RADD starts 2020-01-01 here"
    assert not out["GLAD-S2"]["enabled"] and "does not cover this AOI" in out["GLAD-S2"]["note"]


def test_has_probable_is_false_only_for_a_date_only_custom_source():
    from types import SimpleNamespace as NS
    from deforestation_alerts.engine.presets import has_probable
    p = _p()
    assert has_probable(p.sources["GLAD-L"]) and has_probable(p.sources["RADD"])
    assert has_probable(NS(format="ccdc")) and not has_probable(NS(format="date_only"))


def test_choices_skip_sources_that_are_neither_ready_nor_left_out(monkeypatch):
    """A covered source whose files are not built yet is not offered: it is
    the Download & harmonize step's business, not a reason to show."""
    rows = [
        PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT, "covered", (), stats=None),
        PrepareRow("RADD", Status.MISSING, Status.MISSING, "covered", ()),
    ]
    monkeypatch.setattr(integrate_step, "plan_prepare", lambda p, pdir, period: rows)
    assert integrate_step.integrate_choices(_p(), "/tmp/x", JAN) == [
        {"source": "GLAD-L", "label": "GLAD-L", "enabled": True, "note": "", "probable": True}]


def test_outcome_lines_of_an_empty_set():
    lines = integrate_jobs.outcome_lines(
        {"GLAD-L": {"alert_pixels": 120, "alert_pixels_unmasked": 120},
         "RADD": {"alert_pixels": 0, "alert_pixels_unmasked": 0},
         "MINE": {"alert_pixels": 0, "alert_pixels_unmasked": 7}},
        {"GLAD-S2": "GLAD-S2 does not cover this AOI"})
    assert lines == [
        "GLAD-L: 120 alert pixels (all below the minimum size, outside the drawn area or under earlier sets' footprints)",
        "MINE: all 7 alert pixels removed by masks",
        "RADD: 0 alerts found",
        "GLAD-S2 does not cover this AOI",
    ]


def test_outcome_lines_give_each_left_out_source_its_reason():
    """Review B M9: a source left out for no data must not read "not covered"."""
    lines = integrate_jobs.outcome_lines({}, {"RADD": "RADD has data until 2026-01-10",
                                              "MINE": "Coverage not checked", "OLD": ""})
    assert lines == ["MINE: Coverage not checked", "OLD: not covered", "RADD has data until 2026-01-10"]


def test_display_column_keeps_what_is_typed_readable():
    assert display_column("My Col") == "my_col"
    assert display_column("My ") == "my_"  # a trailing separator survives while typing
    cfg = build_attribute_config("x", "gee", [{"include": True, "band": "B4", "stat": "mean", "column": "admin1",
                                              "crs": "EPSG:32721", "scale": 10.0}])
    assert cfg["bands"] == [{"band": "B4", "stat": "mean", "column": "admin1_2", "crs": "EPSG:32721", "scale": 10.0}]


def test_a_stored_scale_reaches_the_download_estimate_in_metres():
    """Task 15's free-space gate divides the AOI's area in m² by scale²: the
    form stores Earth Engine's ``nominalScale()`` (metres) as is."""
    from deforestation_alerts.engine.download import estimate_native_bytes
    from deforestation_alerts.scripts.attribute_source import download_estimate

    bounds = (-55.5, -24.5, -55.0, -24.0)  # UTM 21S

    def cfg(scale):
        return build_attribute_config("users/me/lc", "gee", [
            {"include": True, "band": "b1", "stat": "majority", "column": "b1", "crs": "EPSG:32721",
             "scale": scale}])

    thirty = download_estimate(cfg(30.0), bounds)
    assert thirty == estimate_native_bytes(bounds, ("EPSG:32721", 30.0)) // 2
    # ~9x the pixels at 10 m than at 30 m: the scale was read as metres, not degrees
    assert 8.9 < download_estimate(cfg(10.0), bounds) / thirty < 9.1


def test_publish_alerts_refuses_once_another_set_is_active():
    state = SimpleNamespace(_lock=threading.RLock(),  # _publish_alerts publishes under AppState's lock
                            project=SimpleNamespace(value=SimpleNamespace(name="i", active_set="other")),
                            actual_alert_id=SimpleNamespace(set=lambda v: None),
                            alerts_gdf=SimpleNamespace(set=lambda v: None))
    result = SimpleNamespace(alert_set=SimpleNamespace(id="mine"), alerts=None)
    assert integrate_jobs._publish_alerts(state, "i", result) is False
