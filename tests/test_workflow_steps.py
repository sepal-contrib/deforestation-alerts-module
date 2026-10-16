import json
from datetime import date, datetime
from pathlib import Path

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
    assert [s.key for s in STEPS] == ["aoi", "alerts", "layers", "period", "prepare", "integrate", "questionnaire",
                                      "evaluators"]
    # The first three rail items are dialogs: the Overview dashboard is the
    # widest, over the map and the panel alike (spec §9.5). The Project dialog
    # is spatial_risk's status block (spec D5): Manage projects and the name
    # form open over it as dialogs of their own. Analysis, before the
    # Overview on the rail, opens its screen.
    assert [(r.key, r.step_id, r.display, r.dialog_width) for r in RAIL_ITEMS] == [
        ("about", 1, "dialog", 1300), ("project", 2, "dialog", 560), ("analysis", 4, "screen", None),
        ("overview", 3, "dialog", 1300)]


def test_gates_follow_the_project(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    assert _states(None)["alerts"] is StepStatus.LOCKED
    p = _p()
    p.aoi = AoiRecord(spec={}, name="Canindeyu", method="ADMIN1", digest="2317", bounds=(0, 0, 1, 1))
    s = _states(p)
    assert s["aoi"] is StepStatus.HAS_OUTPUTS and s["alerts"] is StepStatus.EMPTY
    assert s["layers"] is StepStatus.EMPTY
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
    assert s["integrate"] is StepStatus.HAS_OUTPUTS


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
        if s.count_key:
            assert isinstance(resolve(s.count_key), dict)  # a plural node
    for r in RAIL_ITEMS:
        resolve(r.label_key)


def _with_set(p):
    p.alert_sets["x"] = AlertSet(id="x", period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", n_alerts=3, fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    return p


def test_questionnaire_comes_before_evaluators_and_counts_questions():
    """Analysis is a screen of its own (Questionnaire's *Start analysis*)."""
    from gui.store.workflow_steps import step_index, step_status
    keys = [s.key for s in STEPS]
    assert keys[-2:] == ["questionnaire", "evaluators"] and "analysis" not in keys
    spec = STEPS[step_index("questionnaire")]
    assert not spec.unlocked(None)
    p = _with_set(_p())  # a new project holds the default questions
    assert spec.unlocked(p) and spec.count(p) == len(p.questions) > 0
    p.questions = []
    assert step_status(spec, p) is StepStatus.EMPTY


def test_questionnaire_needs_an_alert_set():
    """Bug 2026-10-16: the questions are asked of an alert set, so the step
    opens only once Integrate & vectorize has made one."""
    from gui.store.workflow_steps import step_index, step_status
    spec = STEPS[step_index("questionnaire")]
    assert spec.lock_reason_key == "workflow.lock_analysis"
    p = _p()
    assert step_status(spec, p) is StepStatus.LOCKED
    assert step_status(spec, _with_set(p)) is StepStatus.HAS_OUTPUTS


def test_evaluators_needs_an_active_set_and_counts_evaluators():
    from deforestation_alerts.project.models import EvaluatorSplit
    from gui.i18n import t
    from gui.store.workflow_steps import step_index, step_status
    spec = STEPS[step_index("evaluators")]
    assert (spec.page_view, spec.label_key, spec.icon) == (
        "evaluators_tile", "app.drawer_item.evaluators", "mdi-account-multiple-outline")
    p = _p()
    assert not spec.unlocked(None) and not spec.unlocked(p)
    p.alert_sets["x"] = AlertSet(id="x", period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                                 params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
                                 footprint_rel_path="f", n_alerts=3, fingerprint="f", created_at=datetime.now())
    p.active_set = "x"
    assert spec.unlocked(p) and spec.count(p) == 0 and step_status(spec, p) is StepStatus.EMPTY
    p.alert_sets["x"].split = EvaluatorSplit(split_id="s", evaluators=["A", "B"], created_at=datetime.now())
    assert spec.count(p) == 2 and step_status(spec, p) is StepStatus.HAS_OUTPUTS
    assert t(spec.count_key, count=2) == "2 evaluators"
