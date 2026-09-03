"""WorkflowHeader binds the pipeline header to AppState: it passes the live
active_step down, hands PipelineHeader the reactives themselves (not their
values) and navigates by setting active_step.

The binding is asserted behaviourally, through a spy standing in for
PipelineHeader. A source grep cannot do it: ``inspect.getsource`` returns the
module's docstring and comments too, so a header pinned to a literal step with
a dead ``on_navigate`` still "contains" every expected string.
"""

import ipyvuetify as v
import reacton
import solara

from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS


def _spy_on_pipeline_header(monkeypatch):
    """Swap PipelineHeader for a recorder; return the list of kwargs it saw.

    Patching the name on the module works because the component body resolves
    ``PipelineHeader`` as a global at render time, not at import time.
    """
    from gui.widget import workflow_header

    calls = []

    @solara.component
    def _Recorder(active_step, on_navigate, alerts_gdf, aoi_name):
        calls.append(
            {
                "active_step": active_step,
                "on_navigate": on_navigate,
                "alerts_gdf": alerts_gdf,
                "aoi_name": aoi_name,
            }
        )

    monkeypatch.setattr(workflow_header, "PipelineHeader", _Recorder)
    return calls


def test_render_smoke():
    """Renders the real tree: the adapter has to actually mount PipelineHeader,
    and what it hands over has to be usable by it (passing ``.value`` instead
    of the reactive raises AttributeError in there)."""
    from gui.widget.workflow_header import WorkflowHeader

    state = AppState()
    box, rc = reacton.render(WorkflowHeader(app_state=state), handle_error=False)
    segments = [
        w for w in rc.find(v.Html).widgets if "data-step-key" in (w.attributes or {})
    ]
    assert [w.attributes["data-step-key"] for w in segments] == [s.key for s in STEPS]
    rc.close()


def test_passes_the_live_active_step_down(monkeypatch):
    calls = _spy_on_pipeline_header(monkeypatch)
    from gui.widget.workflow_header import WorkflowHeader

    state = AppState()
    state.active_step.value = 1
    box, rc = reacton.render(WorkflowHeader(app_state=state), handle_error=False)
    assert calls, "WorkflowHeader never rendered PipelineHeader"
    assert calls[-1]["active_step"] == 1
    rc.close()


def test_hands_over_the_reactives_not_their_values(monkeypatch):
    """The prop-equality-bailout rule: PipelineHeader gets the reactive objects
    and reads them itself, so a gdf-only change still reaches it."""
    calls = _spy_on_pipeline_header(monkeypatch)
    from gui.widget.workflow_header import WorkflowHeader

    state = AppState()
    box, rc = reacton.render(WorkflowHeader(app_state=state), handle_error=False)
    assert calls[-1]["alerts_gdf"] is state.alerts_gdf
    assert calls[-1]["aoi_name"] is state.aoi_name
    rc.close()


def test_navigating_sets_active_step_and_re_renders(monkeypatch):
    calls = _spy_on_pipeline_header(monkeypatch)
    from gui.widget.workflow_header import WorkflowHeader

    state = AppState()
    box, rc = reacton.render(WorkflowHeader(app_state=state), handle_error=False)
    assert calls[-1]["active_step"] == 0

    calls[-1]["on_navigate"](2)

    assert state.active_step.value == 2
    assert calls[-1]["active_step"] == 2
    rc.close()
