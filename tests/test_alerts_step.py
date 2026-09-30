from datetime import date, datetime

from deforestation_alerts.project.models import Period, Project, add_predefined_source
from gui.scripts import catalogue_meta
from gui.widget.source_modal import available_predefined


def _p():
    return Project(name="d", created_at=datetime.now(), updated_at=datetime.now())


def test_available_predefined_hides_the_ones_already_added():
    p = _p()
    assert available_predefined(p) == ["GLAD-L", "RADD", "GLAD-S2"]
    add_predefined_source(p, "RADD")
    assert available_predefined(p) == ["GLAD-L", "GLAD-S2"]


def test_coverage_text():
    assert catalogue_meta.coverage_text("RADD", {"end": "2026-09-20"}) == "2019-01-01 → 2026-09-20"
    assert catalogue_meta.coverage_text("RADD", None) == "…"


def test_preview_window_prefers_the_active_period():
    p = _p()
    per = Period(id="x", start=date(2026, 1, 1), end=date(2026, 1, 31))
    p.periods["x"] = per
    p.active_period = "x"
    assert catalogue_meta.preview_window(p, {"end": "2026-09-20"}, "RADD") == per.search_window(p.look_back_days)


def test_preview_window_without_period_is_the_whole_catalogue():
    # Ruling #15: spec §6.2 says "the whole catalogue" (not the last 90 days).
    assert catalogue_meta.preview_window(_p(), {"end": "2026-09-20"}, "RADD") == (date(2019, 1, 1), date(2026, 9, 20))
