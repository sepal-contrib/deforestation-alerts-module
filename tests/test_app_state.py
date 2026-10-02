"""AppState: the per-session Project lifecycle plus reactive UI/nav state (spec §5.4)."""

from datetime import datetime

from deforestation_alerts.project.models import Project
from gui.store.state_manager import AppState


def _p(name="a"):
    return Project(name=name, created_at=datetime.now(), updated_at=datetime.now())


def test_commit_publishes_a_new_identity():
    s = AppState()
    p = _p()
    s.new_project_state(p)
    first = s.project.value
    first.look_back_days = 30
    s.commit(first)
    assert s.project.value is not first and s.project.value.look_back_days == 30


def test_load_bumps_signal_and_sets_last_saved():
    s = AppState()
    before = s.project_loaded_signal.value
    s.load_project_state(_p())
    assert s.project_loaded_signal.value == before + 1 and s.last_saved.value is not None


def test_update_project_only_touches_the_open_project():
    s = AppState()
    s.new_project_state(_p("a"))
    assert s.update_project("a", lambda p: setattr(p, "look_back_days", 7)) is True
    assert s.project.value.look_back_days == 7
    assert s.update_project("other", lambda p: setattr(p, "look_back_days", 1)) is False
    s.close_project_state()
    assert s.update_project("a", lambda p: None) is False


def test_new_project_clears_session_aoi_and_alerts():
    s = AppState()
    s.aoi_result.set("old")
    s.alerts_gdf.set("gdf")
    s.new_project_state(_p())
    assert s.aoi_result.value is None and s.alerts_gdf.value is None


def test_project_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    s = AppState()
    assert s.project_dir is None
    s.new_project_state(_p("demo"))
    assert s.project_dir == tmp_path / "demo"


def test_closing_the_project_returns_the_workflow_to_the_aoi_step():
    """Without a project every step but AOI is locked: closing (or deleting)
    the open project must not leave the workflow parked on one."""
    from gui.store.workflow_steps import STEPS, StepStatus, step_index, step_states

    s = AppState()
    s.load_project_state(_p("a"))
    s.active_step.set(step_index("analysis"))
    s.close_project_state()
    assert s.active_step.value == 0 and STEPS[0].key == "aoi"
    assert step_states(None)[s.active_step.value] is not StepStatus.LOCKED


def test_a_new_or_closed_project_forgets_the_previous_save_time():
    s = AppState()
    s.load_project_state(_p("a"))
    assert s.last_saved.value is not None
    s.new_project_state(_p("b"))  # no auto-save installed: never saved
    assert s.last_saved.value is None
    s.load_project_state(_p("a"))
    s.close_project_state()
    assert s.last_saved.value is None


# ---------------------------------------------------------------------------
# Navigation and alert review.
# ---------------------------------------------------------------------------
def test_defaults():
    s = AppState()
    assert s.project.value is None
    assert s.current_page_view.value == "about_tile"
    assert s.actual_alert_id.value == -1
    assert s.alerts_gdf.value is None


def test_reactives_are_independent_per_instance():
    a, b = AppState(), AppState()
    a.current_page_view.value = "aoi_tile"
    assert b.current_page_view.value == "about_tile"


def test_navigation_helpers():
    s = AppState()
    s.alerts_gdf.value = list(range(5))  # stand-in: 5 alerts
    s.actual_alert_id.value = 0
    s.go_next()
    assert s.actual_alert_id.value == 1
    s.go_to(4)
    s.go_next()  # clamp at last
    assert s.actual_alert_id.value == 4


def test_workflow_position_defaults_to_the_first_step():
    s = AppState()
    assert s.active_step.value == 0
    s.active_step.set(2)
    assert s.active_step.value == 2


def test_dark_theme_mirror_defaults_to_light():
    s = AppState()
    assert s.dark.value is False
    s.dark.set(True)
    assert s.dark.value is True


def test_the_recipe_era_reactives_are_gone():
    """Task 22: the map-layer registry replaced ``active_map_view``, the
    Project replaced the recipe and the AOI-name mirror, notifications replaced
    ``error_message``."""
    s = AppState()
    for name in ("active_map_view", "aoi_name", "recipe_name", "recipe_folder_path", "error_message"):
        assert not hasattr(s, name), name


def test_an_opened_project_was_last_saved_when_its_manifest_says():
    from datetime import datetime, timedelta

    from deforestation_alerts.project.models import Project

    s = AppState()
    when = datetime.now() - timedelta(days=2)
    s.load_project_state(Project(name="p", created_at=when, updated_at=when))
    assert s.last_saved.value == when
    s.new_project_state(Project(name="q", created_at=when, updated_at=when))
    assert s.last_saved.value is None or s.last_saved.value > when  # never saved before (the auto-save may stamp it)
