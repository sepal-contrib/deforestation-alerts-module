"""Download & harmonize rows (spec §7.3): coverage states, results, sizes, refusals and Stop."""

import asyncio
import threading
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from deforestation_alerts.engine import runner
from deforestation_alerts.engine.coverage import Note
from deforestation_alerts.engine.fingerprint import Status
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project.models import AoiRecord, Period, Project, add_predefined_source
from gui.scripts import prepare_jobs
from gui.tile import prepare_step

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))


def _p():
    p = Project(name="r", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    for s in ("GLAD-L", "RADD", "GLAD-S2"):
        add_predefined_source(p, s)
    p.periods = {JAN.id: JAN}
    p.active_period = JAN.id
    return p


def _build(rows, **over):
    kw = dict(mine=set(), errors={}, on=set(), building=set(), run=lambda s: None, rerun=lambda s: None,
              toggle=lambda s: None)
    kw.update(over)
    return {r["key"]: r for r in prepare_step.build_rows(_p(), "/tmp/x", JAN, rows, **kw)}


def test_blocked_rows_explain_and_cannot_run():
    rows = [
        PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                   (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)),
        PrepareRow("RADD", Status.MISSING, Status.MISSING, "no_data",
                   (Note("coverage.no_data_starts", (("source", "RADD"), ("first", "2020-01-01")), "error"),)),
        PrepareRow("GLAD-L", Status.MISSING, Status.MISSING, "unchecked", (Note("coverage.unchecked", tone="error"),)),
    ]
    out = _build(rows)
    assert out["GLAD-S2"]["notes"][0]["text"].startswith("Not covered: GLAD-S2 does not cover this AOI")
    assert out["RADD"]["notes"][0]["text"] == "No data for this period: RADD starts 2020-01-01 here"
    assert out["GLAD-L"]["notes"][0]["text"] == "Coverage not checked"
    for r in out.values():
        assert all(a["disabled"] for a in r["actions"])
        assert r["cells"][1]["status"] in ("not_covered", "no_data", "unchecked")


def test_partial_lines_and_results():
    rows = [
        PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT, "partial",
                   (Note("coverage.partial_share", (("source", "GLAD-L"), ("pct", "35"))),),
                   stats={"alert_pixels": 1200, "alert_pixels_unmasked": 1300, "date_min": 1, "date_max": 2}),
        PrepareRow("RADD", Status.CURRENT, Status.CURRENT, "covered", (),
                   stats={"alert_pixels": 0, "alert_pixels_unmasked": 0}),
        PrepareRow("GLAD-S2", Status.CURRENT, Status.CURRENT, "covered", (),
                   stats={"alert_pixels": 0, "alert_pixels_unmasked": 40}),
    ]
    out = _build(rows)
    assert [n["tone"] for n in out["GLAD-L"]["notes"]] == ["warning", None]
    assert out["GLAD-L"]["notes"][1]["text"] == "ready · 1,200 alert pixels"
    assert out["RADD"]["notes"] == [{"text": "ready · 0 alerts found", "tone": "info"}]
    assert out["GLAD-S2"]["notes"] == [{"text": "ready · all 40 alert pixels removed by masks", "tone": "warning"}]
    assert not any(a["disabled"] for a in out["GLAD-L"]["actions"])


def test_a_missing_user_file():
    out = _build([PrepareRow("GLAD-L", Status.MISSING, Status.MISSING, "covered", (), file_missing="/home/u/x.tif")])
    assert out["GLAD-L"]["notes"][0] == {"text": "missing: file not found: /home/u/x.tif", "tone": "error"}


def test_size_text():
    assert prepare_step.size_text(0) == ""
    assert prepare_step.size_text(5_400_000) == "5.4 MB"
    assert prepare_step.size_text(2_500_000_000) == "2.5 GB"


def test_a_refused_row_is_reported_with_its_reason(monkeypatch):
    errors = {}
    state = SimpleNamespace(project=SimpleNamespace(value=_p()))

    def refuse(job, **kw):
        raise runner.CoverageRefused("GLAD-S2", "not_covered",
                                     (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),))

    monkeypatch.setattr(prepare_jobs, "run_source_row", refuse)
    ctx = prepare_jobs.PrepareContext(
        app_state=state, layers=None, map_=None, notifier=None, name="r", pdir="/tmp/x", period=JAN, meta={},
        aoi=None, cancel=threading.Event(),
        row_errors=SimpleNamespace(value=errors, set=lambda v: errors.update(v)), errors_lock=threading.Lock())
    prepare_jobs.run_rows(ctx, ["GLAD-S2"])
    assert errors["GLAD-S2"].startswith("GLAD-S2 does not cover this AOI")


def test_stop_cancels_the_task(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    state = SimpleNamespace(project=SimpleNamespace(value=_p()))
    ctx = prepare_jobs.PrepareContext(app_state=state, layers=None, map_=None, notifier=None, name="r",
                                      pdir="/tmp/x", period=JAN, meta={}, aoi=None, cancel=cancel)
    with pytest.raises(asyncio.CancelledError):
        prepare_jobs.run_rows(ctx, ["GLAD-L"])


def test_the_map_toggle_of_a_blocked_row_is_off_unless_its_layer_is_on():
    """Ruling T19 C: a row whose files are current but whose coverage was not
    checked keeps them for Integrate (``ready``) and still cannot be shown;
    a layer already on can always be switched off."""
    row = PrepareRow("GLAD-L", Status.CURRENT, Status.CURRENT, "unchecked", (Note("coverage.unchecked", tone="error"),))
    assert row.ready
    [toggle] = [a for a in _build([row])["GLAD-L"]["actions"] if a["kind"] == "map_toggle"]
    assert toggle["disabled"] and not toggle["is_on"]
    [toggle] = [a for a in _build([row], on={"src:GLAD-L"})["GLAD-L"]["actions"] if a["kind"] == "map_toggle"]
    assert toggle["is_on"] and not toggle["disabled"]
