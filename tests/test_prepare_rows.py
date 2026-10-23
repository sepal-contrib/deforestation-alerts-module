"""Download & harmonize rows (spec §7.3, option B): states, verbs, actions, notes, refusals and Stop."""

import asyncio
import threading
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from deforestation_alerts.engine import runner
from deforestation_alerts.engine.coverage import Note
from deforestation_alerts.engine.fingerprint import Status, raw_key
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project.models import (
    AoiRecord, Artifact, BandMap, Period, Project, add_custom_source, add_predefined_source,
)
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


def _build(rows, p=None, **over):
    kw = dict(mine=set(), errors={}, on=set(), building=set(), run=lambda s: None, toggle=lambda s: None,
              on_info=lambda s: None, on_refresh=lambda s: None, on_delete=lambda s: None)
    kw.update(over)
    return {r["key"]: r for r in prepare_step.build_rows(p or _p(), "/tmp/x", JAN, rows, **kw)}


def _acts(row) -> dict:
    return {a["kind"]: a for a in row["actions"]}


def _art(key, kind):
    return Artifact(key=key, kind=kind, rel_path="x.tif", fingerprint="f", inputs={}, content_id="c",
                    size_bytes=1000, created_at=datetime(2026, 2, 3))


def test_blocked_row_reads_its_status_and_no_verb():
    rows = [
        PrepareRow("GLAD-S2", Status.MISSING, Status.MISSING, "not_covered",
                   (Note("coverage.not_covered_glad_s2", (("source", "GLAD-S2"),), "error"),)),
        PrepareRow("RADD", Status.MISSING, Status.MISSING, "no_data",
                   (Note("coverage.no_data_starts", (("source", "RADD"), ("first", "2020-01-01")), "error"),)),
        PrepareRow("GLAD-L", Status.MISSING, Status.MISSING, "unchecked", (Note("coverage.unchecked", tone="error"),)),
    ]
    out = _build(rows)
    assert out["GLAD-S2"]["cells"][0]["subtitle"].startswith("Not covered: GLAD-S2 does not cover this AOI")
    assert out["RADD"]["cells"][0]["subtitle"] == "No data for this period: RADD starts 2020-01-01 here"
    assert out["GLAD-L"]["cells"][0]["subtitle"] == "Coverage not checked"
    assert out["GLAD-S2"]["cells"][1]["label"] == "not covered"
    assert all(r["notes"] == [] for r in out.values())  # the subtitle says it once, no line repeats it
    for r in out.values():
        assert r["cells"][1]["type"] == "state"
        acts = _acts(r)
        assert acts["refresh"]["disabled"] and acts["delete"]["disabled"] and acts["map_toggle"]["disabled"]
        assert not acts["info"].get("disabled", False)


def test_only_warning_and_error_notes_stay():
    """The neutral notes (what a harmonized row found, its size) are in its subtitle now."""
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
    assert [n["tone"] for n in out["GLAD-L"]["notes"]] == ["warning"]
    assert out["GLAD-L"]["cells"][0]["subtitle"].endswith("1,200 alert pixels")
    assert out["RADD"]["notes"] == []
    assert out["GLAD-S2"]["notes"] == [{"text": "ready · all 40 alert pixels removed by masks", "tone": "warning"}]
    assert not _acts(out["GLAD-L"])["map_toggle"]["disabled"]


def test_four_action_slots_in_order():
    p = _p()
    add_custom_source(p, name="tif", origin="file", ref="/x/tif.tif", asset_type="FILE", format="radd",
                      date_encoding="yyddd", band_map=BandMap(alert=1, date=2), native=None)
    rows = [PrepareRow("RADD", Status.CURRENT, Status.CURRENT), PrepareRow("tif", Status.MISSING, Status.MISSING)]
    out = _build(rows, p=p)
    for r in out.values():
        assert [a["kind"] for a in r["actions"]] == ["map_toggle", "info", "refresh", "delete"]
        assert len(r["actions"]) == prepare_step.SOURCE_ACTIONS
    radd, tif = _acts(out["RADD"]), _acts(out["tif"])
    assert not radd["refresh"]["disabled"] and radd["refresh"]["title"] == "Download again"
    assert tif["refresh"]["disabled"] and tif["refresh"]["title"] == "Nothing to download again: read from a file"
    assert radd["delete"]["title"] == "Delete files"
    assert out["tif"]["cells"][1]["label"] == "Harmonize"  # a GeoTIFF is never downloaded


def test_the_row_actions_call_back_with_their_source():
    calls = []
    record = lambda name: (lambda s: calls.append((name, s)))  # noqa: E731
    out = _build([PrepareRow("RADD", Status.MISSING, Status.MISSING)], run=record("run"), on_info=record("info"),
                 on_refresh=record("refresh"), on_delete=record("delete"))["RADD"]
    out["cells"][1]["on_click"]()
    for kind in ("info", "refresh", "delete"):
        _acts(out)[kind]["on_click"]()
    assert calls == [("run", "RADD"), ("info", "RADD"), ("refresh", "RADD"), ("delete", "RADD")]
    assert _acts(out)["info"]["is_on"] is False
    assert _acts(_build([PrepareRow("RADD", Status.MISSING, Status.MISSING)], info_open="RADD")["RADD"])["info"][
        "is_on"]


def test_delete_needs_a_file():
    p = _p()
    assert _acts(_build([PrepareRow("RADD", Status.MISSING, Status.MISSING)], p=p)["RADD"])["delete"]["disabled"]
    rkey = raw_key(JAN.id, "RADD")
    p.artifacts = {rkey: _art(rkey, "raw_alert")}
    assert not _acts(_build([PrepareRow("RADD", Status.CURRENT, Status.MISSING)], p=p)["RADD"])["delete"]["disabled"]


def test_a_review_copy_shows_text_not_verbs():
    out = _build([PrepareRow("RADD", Status.MISSING, Status.MISSING)], locked=True)["RADD"]
    assert (out["cells"][1]["type"], out["cells"][1]["label"]) == ("state", "To download")
    acts = _acts(out)
    assert acts["refresh"]["disabled"] and acts["delete"]["disabled"] and not acts["info"].get("disabled", False)


def test_a_failed_row_keeps_its_error_line():
    out = _build([PrepareRow("RADD", Status.MISSING, Status.MISSING)], errors={"RADD": "boom"})["RADD"]
    assert out["cells"][1]["label"] == "Retry" and out["error"] == "boom"


@pytest.mark.parametrize("raw, harmonized, verb", [
    (Status.MISSING, Status.MISSING, "Download"),  # a new period
    (Status.STALE, Status.STALE, "Download"),  # the period's dates changed
    (Status.CURRENT, Status.STALE, "Harmonize"),  # downloaded: Run only harmonizes again
    (Status.CURRENT, Status.CURRENT, None),  # downloaded and harmonized: only a forced refresh
])
def test_a_downloaded_source_offers_no_run_until_its_window_changes(raw, harmonized, verb):
    row = _build([PrepareRow("GLAD-L", raw, harmonized)])["GLAD-L"]
    assert (row["cells"][1]["label"] if row["cells"][1]["type"] == "verb" else None) == verb
    assert not _acts(row)["refresh"]["disabled"]


def test_a_busy_row_reads_queued_or_its_stage():
    key = prepare_step._inflight_key("r", JAN.id, "RADD")
    rows = [PrepareRow("RADD", Status.MISSING, Status.MISSING)]
    queued = _build(rows, mine={key})["RADD"]
    assert queued["cells"][1]["label"] == "Queued" and queued["cells"][0]["subtitle"] == "waiting for the row above"
    assert _acts(queued)["refresh"]["disabled"] and _acts(queued)["delete"]["disabled"]
    pkey = prepare_jobs.progress_key("r", JAN.id, "source", "RADD")
    running = _build(rows, mine={key}, stages={pkey: "download"}, progress={pkey: 37})["RADD"]
    assert running["cells"][1]["label"] == "Downloading 37%"
    other = prepare_jobs.progress_key("r", "another period", "source", "RADD")
    assert _build(rows, mine={key}, stages={other: "download"})["RADD"]["cells"][1]["label"] == "Queued"


def test_a_missing_user_file():
    out = _build([PrepareRow("GLAD-L", Status.MISSING, Status.MISSING, "covered", (), file_missing="/home/u/x.tif")])
    assert out["GLAD-L"]["notes"][0] == {"text": "missing: file not found: /home/u/x.tif", "tone": "error"}
    assert out["GLAD-L"]["cells"][1]["label"] == "File missing"


def test_size_text():
    from gui.i18n import size_text

    assert size_text(0) == ""
    assert size_text(5_400_000) == "5.4 MB"
    assert size_text(2_500_000_000) == "2.5 GB"


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


def test_a_downloading_mask_reads_its_percent():
    """Bug 2026-10-16: the step's own rows say how far a download has got."""
    from gui.tile.masks_card import mask_status

    busy = mask_status(SimpleNamespace(raw=Status.MISSING, aligned=Status.MISSING), busy=True, progress=12)
    assert busy["label"] == "12%" and busy["compact"]
    assert "label" not in mask_status(SimpleNamespace(raw=Status.CURRENT, aligned=Status.CURRENT), busy=False,
                                      progress=12)


# Row stages and row ends (the batch footer reads them)
# ---------------------------------------------------------------------------
class _Recorder:
    """A reactive that keeps every value it was set to."""

    def __init__(self):
        self.value, self.history = {}, []

    def set(self, value):
        self.value = value
        self.history.append(dict(value))


def _stage_ctx(p, **over):
    state = SimpleNamespace(project=SimpleNamespace(value=p), update_project=lambda name, fn: fn(p) or True)
    kw = dict(app_state=state, layers=None, map_=None, notifier=None, name="r", pdir="/tmp/x", period=JAN, meta={},
              aoi=None, cancel=threading.Event(), errors_lock=threading.Lock(), row_stage=_Recorder())
    kw.update(over)
    return prepare_jobs.PrepareContext(**kw)


def test_run_rows_reports_each_rows_stage(monkeypatch):
    ctx = _stage_ctx(_p(), downloads=frozenset({("source", "GLAD-L")}))
    seen = {}

    def run(job, **kw):
        seen[job.source] = dict(ctx.row_stage.value)
        return {}

    monkeypatch.setattr(prepare_jobs, "run_source_row", run)
    prepare_jobs.run_rows(ctx, ["GLAD-L", "RADD"])
    key = lambda s: prepare_jobs.progress_key("r", JAN.id, "source", s)  # noqa: E731
    assert seen == {"GLAD-L": {key("GLAD-L"): "download"}, "RADD": {key("RADD"): "harmonize"}}
    assert ctx.row_stage.value == {}


def test_the_stage_turns_to_harmonize_at_100_percent(monkeypatch):
    ctx = _stage_ctx(_p(), downloads=frozenset({("source", "GLAD-L")}))
    key = prepare_jobs.progress_key("r", JAN.id, "source", "GLAD-L")

    def run(job, on_tiles, **kw):
        on_tiles(5, 10)
        assert ctx.row_stage.value == {key: "download"}
        on_tiles(10, 10)  # the reporter's on_pct(100)
        assert ctx.row_stage.value == {key: "harmonize"}
        return {}

    monkeypatch.setattr(prepare_jobs, "run_source_row", run)
    prepare_jobs.run_rows(ctx, ["GLAD-L"])
    assert ctx.row_stage.history == [{key: "download"}, {key: "harmonize"}, {}]


def test_row_end_is_reported_on_failure(monkeypatch):
    ends = []
    ctx = _stage_ctx(_p(), on_row_end=lambda: ends.append(1))

    def fail(job, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(prepare_jobs, "run_source_row", fail)
    prepare_jobs.run_rows(ctx, ["GLAD-L", "RADD", "gone"])  # a source removed meanwhile ends too
    assert len(ends) == 3 and ctx.row_stage.value == {}
