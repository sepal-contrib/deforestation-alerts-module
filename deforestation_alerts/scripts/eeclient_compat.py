"""Workarounds for ee-client bugs the app hits through pysepal 4.

``patch_empty_task_list``: Earth Engine's REST ``projects/*/operations`` list
omits the ``operations`` key entirely when a project has no tasks (proto3 JSON
drops empty repeated fields), but ee-client 3.2.1 declares
``TasksResponse.operations`` as required. Any task lookup on a project with an
empty task list then raises ``1 validation error for TasksResponse operations
Field required``. pysepal 4 reaches it from ``AoiModel.export_to_asset`` ->
``GEEInterface.is_running`` whenever a drawn AOI is selected (3.x took the
global-``ee`` branch and never called ee-client there). Unfixed upstream as of
ee-client 3.2.1 (dfguerrerom/ee-client main); delete this module once a release
defaults the field.
"""

import logging

logger = logging.getLogger("SEPALUI.deforestation_alerts.eeclient_compat")


def patch_empty_task_list() -> None:
    """Default ``TasksResponse.operations`` to ``[]`` (idempotent)."""
    from eeclient.tasks import TasksResponse

    field = TasksResponse.model_fields["operations"]
    if not field.is_required():
        return
    field.default_factory = list
    TasksResponse.model_rebuild(force=True)
    logger.debug("Patched ee-client TasksResponse.operations to default to []")
