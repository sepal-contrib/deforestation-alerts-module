"""The set selector shared by Overview and Analysis (spec §9.3)."""

import threading
from datetime import datetime

import ipyvuetify as v
import reacton
import solara

from deforestation_alerts.project import periods
from deforestation_alerts.project.models import AlertSet, AlertSetParams, Project, add_predefined_source
from gui.i18n import t
from gui.store.state_manager import AppState
from gui.widget import set_selector

JAN_ID, FEB_ID = "2026-01-01_2026-01-31", "2026-02-01_2026-02-28"


def _set(pid, sources, n, reviewed, created):
    slug = "-".join(s.lower() for s in sources)
    folder = f"periods/{pid}/sets/{slug}"
    return AlertSet(id=f"{pid}__{slug}", period_id=pid, sources=sources,
                    digit_map={i: s for i, s in enumerate(sources)}, params=AlertSetParams(),
                    store_rel_path=f"{folder}/alert_db.parquet", combined_rel_path=f"{folder}/c.tif",
                    footprint_rel_path=f"{folder}/footprint.tif", n_alerts=n, n_reviewed=reviewed,
                    fingerprint="f", created_at=created)


def _project():
    p = Project(name="sets", created_at=datetime.now(), updated_at=datetime.now())
    add_predefined_source(p, "GLAD-L")
    add_predefined_source(p, "RADD")
    for a, b in (("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28")):
        assert periods.new_period(p, a, b) is None
    jan = _set(JAN_ID, ["GLAD-L"], 4, 1, datetime(2026, 2, 2))
    feb_a = _set(FEB_ID, ["GLAD-L"], 7, 0, datetime(2026, 3, 2))
    feb_b = _set(FEB_ID, ["GLAD-L", "RADD"], 9, 2, datetime(2026, 3, 5))
    p.alert_sets = {s.id: s for s in (jan, feb_a, feb_b)}
    p.active_set = jan.id
    return p, jan, feb_a, feb_b


def test_items_are_newest_first_and_read_period_sources_alerts_reviewed():
    p, jan, feb_a, feb_b = _project()
    items = set_selector.set_items(p)
    assert [i["value"] for i in items] == [feb_b.id, feb_a.id, jan.id]
    assert items[0]["text"] == "2026-02-01 → 2026-02-28 · GLAD-L + RADD · 9 alerts · 2 reviewed"
    assert set_selector.set_items(None) == []


def test_selecting_a_set_changes_only_the_active_set():
    p, jan, feb_a, _ = _project()
    state = AppState()
    state.new_project_state(p)
    period = state.project.value.active_period
    assert set_selector.select_set(state, "sets", feb_a.id)
    assert state.project.value.active_set == feb_a.id and state.project.value.active_period == period
    before = state.project.value
    assert not set_selector.select_set(state, "sets", "gone")
    assert not set_selector.select_set(state, "other", jan.id)
    assert state.project.value is before


def test_the_component_follows_the_project_and_a_pick_selects():
    p, jan, feb_a, _ = _project()
    state = AppState()
    state.new_project_state(p)

    @solara.component
    def Page():
        set_selector.SetSelector(app_state=state)

    _, rc = reacton.render(Page(), handle_error=False)
    try:
        select = rc.find(v.Select).widget
        assert select.v_model == jan.id and len(select.items) == 3 and select.label == t("steps.sets.label")
        select.v_model = feb_a.id
        assert state.project.value.active_set == feb_a.id
        state.update_project("sets", lambda q: setattr(q, "active_set", jan.id))
        assert rc.find(v.Select).widget.v_model == jan.id
        state.close_project_state()
        assert rc.find(v.Select).widgets == []
    finally:
        rc.close()


def test_the_raw_selector_follows_publishes_from_any_thread_and_never_picks_back():
    p, jan, feb_a, feb_b = _project()
    state = AppState()
    select = set_selector.build_set_selector(state)
    assert select.style_ == "display: none;"  # no project yet
    state.new_project_state(p)
    assert select.style_ == "" and select.v_model == jan.id
    assert [i["value"] for i in select.items] == [feb_b.id, feb_a.id, jan.id]
    select.v_model = feb_b.id  # the browser's pick
    assert state.project.value.active_set == feb_b.id
    worker = threading.Thread(target=lambda: state.update_project(
        "sets", lambda q: setattr(q, "active_set", jan.id)))
    worker.start()
    worker.join()
    assert select.v_model == jan.id and state.project.value.active_set == jan.id
    state.close_project_state()
    assert select.style_ == "display: none;" and select.v_model is None
