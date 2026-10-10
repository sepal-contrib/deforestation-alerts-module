"""The Workflow view's header: PipelineHeader bound to AppState.

Rendered once by the shell (via ``_render_detached``) as the first widget of
the right panel's host. It re-renders when ``active_step``, ``project`` or
``dark`` change because it reads those reactives inside the component body;
``PipelineHeader`` itself receives the ``project``/theme reactives and reads
them again (the prop-equality-bailout rule from the scaffold).

*Start analysis* writes ``current_page_view``, the navigation signal every
other way into Analysis writes; ``_NavBridge`` turns it into the analysis
screen.
"""

import solara

from gui.widget.pipeline_header import PipelineHeader


@solara.component
def WorkflowHeader(app_state):
    active = app_state.active_step.value
    PipelineHeader(
        active_step=active,
        on_navigate=app_state.active_step.set,
        project=app_state.project,
        dark=app_state.dark,
        on_start_analysis=lambda: app_state.current_page_view.set("analysis_tile"),
    )
