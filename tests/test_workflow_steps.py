import json
from datetime import date, datetime
from pathlib import Path

import pytest

from deforestation_alerts.engine import fingerprint as fp
from deforestation_alerts.project.models import (
    AlertSet, AlertSetParams, AoiRecord, GridSpec, Period, Project, add_predefined_source,
)
from gui.store.workflow_steps import RAIL_ITEMS, STEPS, StepStatus, step_states

MESSAGES = Path(__file__).resolve().parents[1] / "deforestation_alerts" / "message" / "en"
JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))


def _p():
    return Project(name="demo", created_at=datetime.now(), updated_at=datetime.now())


def _states(p):
    return dict(zip([s.key for s in STEPS], step_states(p)))


def test_order_and_rail():
    assert [s.key for s in STEPS] == ["aoi", "alerts", "period", "prepare", "integrate", "analysis"]
    assert [(r.key, r.step_id, r.display) for r in RAIL_ITEMS] == [
        ("about", 1, "dialog"), ("project", 2, "dialog"), ("overview", 3, "panel")]


def test_gates_follow_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    assert _states(None)["alerts"] is StepStatus.LOCKED
    p = _p()
    p.aoi = AoiRecord(spec={}, name="Canindeyu", method="ADMIN1", digest="2317", bounds=(0, 0, 1, 1))
    s = _states(p)
    assert s["aoi"] is StepStatus.HAS_OUTPUTS and s["alerts"] is StepStatus.EMPTY
    assert s["period"] is StepStatus.LOCKED
    add_predefined_source(p, "GLAD-L")
    assert _states(p)["period"] is StepStatus.EMPTY and _states(p)["prepare"] is StepStatus.LOCKED
    p.periods[JAN.id] = JAN
    p.active_period = JAN.id
    s = _states(p)
    assert s["prepare"] is StepStatus.EMPTY and s["integrate"] is StepStatus.LOCKED
    # one ready source unlocks Integrate
    p.grid = GridSpec(epsg="EPSG:32721", resolution=10, xmin=0, ymax=0, width=1, height=1)
    pdir = tmp_path / "demo"
    pdir.mkdir()
    for key, kind, inputs in (
        (fp.raw_key(JAN.id, "GLAD-L"), "raw_alert", fp.raw_alert_inputs(p, "GLAD-L", JAN)),
    ):
        (pdir / "r.tif").write_bytes(b"x")
        p.artifacts[key] = fp.make_artifact(key, kind, pdir, pdir / "r.tif", inputs)
    hk = fp.harm_key(JAN.id, "GLAD-L")
    (pdir / "h.tif").write_bytes(b"x")
    p.artifacts[hk] = fp.make_artifact(hk, "harmonized", pdir, pdir / "h.tif", fp.harmonized_inputs(p, "GLAD-L", JAN))
    s = _states(p)
    assert s["prepare"] is StepStatus.HAS_OUTPUTS and s["integrate"] is StepStatus.EMPTY
    p.alert_sets["x"] = AlertSet(id="x", period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", n_alerts=3, n_reviewed=1, fingerprint="f",
                                 created_at=datetime.now())
    p.active_set = "x"
    s = _states(p)
    assert s["integrate"] is StepStatus.HAS_OUTPUTS and s["analysis"] is StepStatus.HAS_OUTPUTS


def test_every_label_and_lock_key_exists():
    catalog = {}
    for f in MESSAGES.glob("*.json"):
        catalog.update(json.loads(f.read_text()))

    def resolve(dotted):
        node = catalog
        for part in dotted.split("."):
            node = node[part]
        return node

    for s in STEPS:
        resolve(s.label_key)
        if s.lock_reason_key:
            resolve(s.lock_reason_key)
        if s.count_key_one:
            resolve(s.count_key_one)
            resolve(s.count_key_other)
    for r in RAIL_ITEMS:
        resolve(r.label_key)
