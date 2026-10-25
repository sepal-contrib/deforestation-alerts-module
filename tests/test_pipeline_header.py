"""PipelineHeader: badge text logic, render smoke, and codebase-gotcha
contracts (no rv.Btn(on_click), anchored dropdown, reactives-as-props).
Ported from the spatial_risk scaffold; gates read the Project."""

import inspect
from datetime import datetime

import reacton
import solara

from deforestation_alerts.project.models import AlertSet, AlertSetParams, AoiRecord, Project

JAN = "2026-01-01_2026-01-31"


def _project(aoi_name=None, n_reviewed=0, n_alerts=0):
    p = Project(name="demo", created_at=datetime.now(), updated_at=datetime.now())
    if aoi_name is not None:
        p.aoi = AoiRecord(spec={}, name=aoi_name, method="ADMIN1", digest="2317", bounds=(0, 0, 1, 1))
    if n_alerts or n_reviewed:
        p.alert_sets["x"] = AlertSet(
            id="x", period_id=JAN, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
            params=AlertSetParams(), store_rel_path="a", combined_rel_path="c",
            footprint_rel_path="f", n_alerts=n_alerts, n_reviewed=n_reviewed,
            fingerprint="f", created_at=datetime.now(),
        )
        p.active_set = "x"
    return p


def _reactive(p):
    return solara.reactive(p, equals=lambda a, b: a is b)


def test_count_text_pluralizes_and_falls_back():
    from gui.store.workflow_steps import STEPS, step_index
    from gui.widget.pipeline_header import count_text

    aoi_spec, alerts_spec, integrate = STEPS[0], STEPS[1], STEPS[step_index("integrate")]
    assert count_text(integrate, _project(n_alerts=5)) == "5 clusters"
    assert count_text(integrate, _project()) == "nothing yet"
    # AOI step: the badge is the AOI name (a string count).
    assert count_text(aoi_spec, _project(aoi_name="Acre")) == "Acre"
    assert count_text(aoi_spec, _project()) == "nothing yet"
    # Alerts step: count of configured sources (none in this fixture).
    assert count_text(alerts_spec, _project(aoi_name="Acre")) == "nothing yet"


def test_render_smoke_empty_session():
    from gui.widget.pipeline_header import PipelineHeader

    project = _reactive(None)
    box, rc = reacton.render(
        PipelineHeader(
            active_step=0, on_navigate=lambda i: None,
            project=project,
        ),
        handle_error=False,
    )
    rc.close()


def test_render_smoke_loaded_project():
    from gui.widget.pipeline_header import PipelineHeader

    project = _reactive(_project(aoi_name="Acre", n_alerts=3, n_reviewed=1))
    box, rc = reacton.render(
        PipelineHeader(
            active_step=5, on_navigate=lambda i: None,
            project=project,
        ),
        handle_error=False,
    )
    rc.close()


def test_jump_menu_is_a_dropdown_not_a_modal():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "solara.lab.Menu(" in src
    assert "rv.Dialog(" not in src
    # use_activator_width=False sends min-width="auto" to v-menu; Vuetify's
    # off-screen guard then computes NaN and stops clamping.
    assert "use_activator_width=False" not in src


def test_no_dead_click_patterns():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "rv.Btn(" not in src
    assert "rv.use_event(" in src
    # Reads the reactive inside the component (prop-equality bailout).
    assert "project.value" in src


def test_unified_activator_and_testable_segments():
    from gui.widget import pipeline_header

    src = inspect.getsource(pipeline_header)
    assert "workflow.step_badge" in src
    assert "mdi-menu-down" in src
    assert "sr-step-jump:hover" in src
    assert 'class_="sr-step-jump"' in src
    assert '"data-step-key"' in src  # the driver clicks segments by key
