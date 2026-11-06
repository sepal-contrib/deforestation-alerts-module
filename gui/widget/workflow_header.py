"""The Workflow view's header: PipelineHeader bound to AppState.

Rendered once by the shell (via ``_render_detached``) as the first widget of
the right panel's host. It re-renders when ``active_step``, ``project`` or
``dark`` change because it reads those reactives inside the component body;
``PipelineHeader`` itself receives the ``project``/theme reactives and reads
them again (the prop-equality-bailout rule from the scaffold).

Analysis is not a step: the Evaluation step ends with *Start analysis*,
and the left rail has an Analysis entry.

A review copy's banner (spec §6.3) sits under the header: above every step.
"""

import solara

from gui.widget.pipeline_header import PipelineHeader
from gui.widget.review_copy_banner import ReviewCopyBanner


@solara.component
def WorkflowHeader(app_state):
    active = app_state.active_step.value
    with solara.Column(style="gap:0;"):
        PipelineHeader(
            active_step=active,
            on_navigate=app_state.active_step.set,
            project=app_state.project,
            dark=app_state.dark,
        )
        ReviewCopyBanner(app_state=app_state)
