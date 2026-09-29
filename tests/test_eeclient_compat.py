"""ee-client's task list must survive a project with no tasks (drawn AOI path)."""

import asyncio

from eeclient import tasks as ee_tasks

from deforestation_alerts.scripts.eeclient_compat import patch_empty_task_list


class _Session:
    """Stands in for an EESession whose project has no operations at all."""

    async def rest_call(self, method, url):
        return {}  # Earth Engine omits the empty ``operations`` list


def test_empty_operations_response_validates():
    patch_empty_task_list()
    assert ee_tasks.TasksResponse.model_validate({}).operations == []


def test_task_lookup_on_an_empty_project_finds_nothing():
    patch_empty_task_list()
    found = asyncio.run(ee_tasks.get_task_by_name_async(_Session(), "aoi_drawn"))
    assert found is None


def test_patch_is_idempotent():
    patch_empty_task_list()
    patch_empty_task_list()
    assert not ee_tasks.TasksResponse.model_fields["operations"].is_required()


def test_app_applies_the_patch_at_startup():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "gui/solara_app.py").read_text()
    assert "patch_empty_task_list()" in src
