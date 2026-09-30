from datetime import datetime

from deforestation_alerts.project.models import Project
from gui.store.autosave import install_autosave
from gui.store.state_manager import AppState


def _p(name="a"):
    return Project(name=name, created_at=datetime.now(), updated_at=datetime.now())


def test_every_commit_saves_but_loads_do_not():
    s = AppState()
    saved = []
    install_autosave(s, save=lambda p: saved.append(p.name))
    s.load_project_state(_p("loaded"))
    assert saved == []  # loading from disk must not rewrite it
    p = s.project.value
    p.look_back_days = 10
    s.commit(p)
    assert saved == ["loaded"]
    assert s.last_saved.value is not None and s.save_error.value is None


def test_save_failure_is_reported_not_raised():
    s = AppState()

    def boom(p):
        raise OSError("disk full")

    install_autosave(s, save=boom)
    s.new_project_state(_p())
    assert "disk full" in (s.save_error.value or "")


def test_closing_does_not_save():
    s = AppState()
    saved = []
    install_autosave(s, save=lambda p: saved.append(p))
    s.new_project_state(_p())
    saved.clear()
    s.close_project_state()
    assert saved == []


def test_worker_updates_save_and_the_unsubscribe_stops_saving():
    s = AppState()
    saved = []
    unsubscribe = install_autosave(s, save=lambda p: saved.append(p.look_back_days))
    s.new_project_state(_p("a"))
    s.update_project("a", lambda p: setattr(p, "look_back_days", 5))
    assert saved == [90, 5]
    unsubscribe()
    s.update_project("a", lambda p: setattr(p, "look_back_days", 6))
    assert saved == [90, 5]
