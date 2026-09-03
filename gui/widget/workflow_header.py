"""The Workflow view's header: PipelineHeader bound to AppState.

Rendered once by the shell (via ``_render_detached``) as the first widget of
the right panel's host. It re-renders when ``active_step``, ``alerts_gdf`` or
``aoi_name`` change because it reads those reactives inside the component
body; ``PipelineHeader`` itself receives the gdf/AOI reactives and reads them
again (the prop-equality-bailout rule from the scaffold).
"""

import solara

from gui.widget.pipeline_header import PipelineHeader


@solara.component
def WorkflowHeader(app_state):
    active = app_state.active_step.value
    PipelineHeader(
        active_step=active,
        on_navigate=app_state.active_step.set,
        alerts_gdf=app_state.alerts_gdf,
        aoi_name=app_state.aoi_name,
    )
