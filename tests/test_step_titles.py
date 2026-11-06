"""No workflow step repeats its own title (spec 2026-10-14 §2): the
PipelineHeader above the panel already names the active step."""

import importlib
import inspect

import pytest

from gui import i18n

STEP_MODULES = ["aoi_step", "alerts_step", "layers_step", "period_step", "prepare_step", "integrate_step",
                "questionnaire_step", "evaluators_step"]
TITLE_KEYS = ["steps.aoi.title", "steps.alerts.title", "steps.layers.title", "steps.period.title",
              "steps.prepare.title", "steps.integrate.title", "steps.questionnaire.title", "evaluators.title"]


@pytest.mark.parametrize("name", STEP_MODULES)
def test_a_step_body_does_not_repeat_its_title(name):
    source = inspect.getsource(importlib.import_module(f"gui.tile.{name}"))
    assert 'Markdown(f"### ' not in source


@pytest.mark.parametrize("key", TITLE_KEYS)
def test_the_step_title_keys_are_gone(key):
    assert not i18n.has_message(key), key
