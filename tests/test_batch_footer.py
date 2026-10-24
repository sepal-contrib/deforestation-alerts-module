"""The Download & harmonize batch footer (spec "Footer"): its counts, idle and busy."""

from datetime import date, datetime

import ipyvuetify as v
import reacton

from deforestation_alerts.engine.fingerprint import Status
from deforestation_alerts.engine.plan import PrepareRow
from deforestation_alerts.project.models import AoiRecord, Period, Project, add_predefined_source
from gui.scripts import prepare_jobs
from gui.tile import prepare_step
from gui.widget.batch_footer import BatchFooter, footer_counts

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
FEB = Period(id="2026-02-01_2026-02-28", start=date(2026, 2, 1), end=date(2026, 2, 28))


def _walk(w, out):
    out.append(w)
    # a menu keeps its activator in a scoped slot, not in ``children``
    slotted = [sl["children"] for sl in getattr(w, "v_slots", None) or []]
    slotted = [c for x in slotted for c in (x if isinstance(x, list) else [x])]
    for c in list(getattr(w, "children", None) or []) + slotted:
        if not isinstance(c, str):
            _walk(c, out)
        else:
            out.append(c)
    return out


def _all(rc):
    out = []
    for w in rc.find().widgets:
        _walk(w, out)
    return out


def _texts(rc):
    return [w for w in _all(rc) if isinstance(w, str)]


def _btn(rc, label):
    return next(w for w in _all(rc) if isinstance(w, v.Btn) and label in w.children)


def _render(counts, menu=(), pending=2, disabled=False, stopping=False):
    calls = []
    box, rc = reacton.render(
        BatchFooter(pending=pending, run_label="Run 2 pending", on_run=lambda: calls.append("run"),
                    on_stop=lambda: calls.append("stop"), menu=list(menu), counts=counts, disabled=disabled,
                    stopping=stopping),
        handle_error=False)
    return rc, calls


IDLE = {"running": 0, "queued": 0, "done": None, "total": None}


def test_footer_counts_split_running_and_queued():
    assert footer_counts({"a", "b", "c"}, {"a": "download"}, (3, 0)) == {
        "running": 1, "queued": 2, "done": 0, "total": 3}
    assert footer_counts(set(), {}, None) == IDLE


def test_footer_counts_only_this_scope():
    """Another period's stages never reach this period's rows or counts."""
    p = Project(name="r", created_at=datetime.now(), updated_at=datetime.now())
    p.aoi = AoiRecord(spec={}, name="a", method="DRAW", digest="d", bounds=(0, 0, 1, 1))
    add_predefined_source(p, "RADD")
    p.periods = {JAN.id: JAN, FEB.id: FEB}
    p.active_period = JAN.id
    mine = {prepare_step._inflight_key("r", JAN.id, "RADD")}
    foreign = {prepare_jobs.progress_key("r", FEB.id, "source", "RADD"): "download",
               prepare_jobs.progress_key("other", JAN.id, "source", "RADD"): "download"}
    stages = prepare_step.scope_stages(foreign, "r", JAN.id)
    assert stages == {}
    [row] = prepare_step.build_rows(p, "/tmp/x", JAN, [PrepareRow("RADD", Status.MISSING, Status.MISSING)],
                                    mine=mine, errors={}, on=set(), building=set(), run=None, toggle=None,
                                    on_info=None, on_refresh=None, on_delete=None, stages=stages)
    assert row["cells"][1]["label"] == "Queued"
    rows = prepare_step.mine_rows(p, JAN, mine)
    assert rows == {prepare_jobs.progress_key("r", JAN.id, "source", "RADD")}
    assert footer_counts(rows, stages, None)["running"] == 0
    assert footer_counts(rows, stages, None)["queued"] == 1


def test_footer_idle_shows_run_and_caret():
    rc, calls = _render(IDLE, menu=[{"label": "Download all again (3)", "on_click": lambda: calls.append("again")}])
    try:
        _btn(rc, "Run 2 pending").click()
        [item] = [w for w in _all(rc) if isinstance(w, v.ListItem)]
        item.fire_event("click", None)
        assert calls == ["run", "again"]
        assert "mdi-menu-up" in _texts(rc) and "Download all again (3)" in _texts(rc)
        assert "mdi-stop" not in _texts(rc) and not any("running" in s for s in _texts(rc))
    finally:
        rc.close()


def test_footer_idle_without_a_menu_has_no_caret_and_nothing_pending_disables_run():
    rc, _ = _render(IDLE, pending=0)
    try:
        assert "mdi-menu-up" not in _texts(rc)
        assert _btn(rc, "Run 2 pending").disabled
    finally:
        rc.close()


def test_footer_busy_shows_stop_and_strip():
    rc, calls = _render({"running": 1, "queued": 2, "done": 1, "total": 4},
                        menu=[{"label": "Download all again (3)", "on_click": lambda: None}])
    try:
        stop = _btn(rc, "Stop")
        assert stop.outlined and "mdi-stop" in _texts(rc)
        stop.click()
        assert calls == ["stop"]
        assert "1 running · 2 queued" in _texts(rc) and "1 of 4 done" in _texts(rc)
        assert [b.value for b in _all(rc) if isinstance(b, v.ProgressLinear)] == [25]
        tips = [w.attributes.get("title") for w in _all(rc) if isinstance(w, v.Html)]
        assert "Stops after the row in progress" in tips
        assert not any(isinstance(w, v.Btn) and "Run 2 pending" in w.children for w in _all(rc))
    finally:
        rc.close()


def test_a_busy_footer_without_a_batch_has_no_bar():
    rc, _ = _render({"running": 1, "queued": 0, "done": None, "total": None})
    try:
        assert "1 running · 0 queued" in _texts(rc)
        assert not any(isinstance(w, v.ProgressLinear) for w in _all(rc))
    finally:
        rc.close()


def test_a_disabled_menu_item_keeps_its_tooltip():
    clicked = []
    rc, _ = _render(IDLE, menu=[{"label": "Download all again (3)", "on_click": lambda: clicked.append(1),
                                 "disabled": True, "title": "Run the pending masks first"}])
    try:
        [item] = [w for w in _all(rc) if isinstance(w, v.ListItem)]
        assert item.disabled
        tips = [w.attributes.get("title") for w in _all(rc) if isinstance(w, v.Html)]
        assert "Run the pending masks first" in tips
    finally:
        rc.close()


def test_a_stopping_footer_says_so_and_drops_the_queued_count():
    rc, calls = _render({"running": 1, "queued": 2, "done": 1, "total": 4}, stopping=True)
    try:
        btn = _btn(rc, "Stopping after this row…")
        assert btn.disabled and btn.outlined
        assert not any(isinstance(w, v.Btn) and "Stop" in w.children for w in _all(rc))
        assert "1 running" in _texts(rc) and not any("queued" in s for s in _texts(rc))
        assert "1 of 4 done" in _texts(rc)
    finally:
        rc.close()
