"""The STEPS / RAIL_ITEMS registries are the single source of truth for step
identity: three gated workflow steps in the Workflow panel view, four entries
on the left rail (three dialogs + the Overview panel view)."""

import json
from pathlib import Path

import pandas as pd

from gui.store.workflow_steps import (
    RAIL_ITEMS,
    STEPS,
    StepStatus,
    nav_targets,
    rail_item_for_step,
    step_index,
    step_states,
    step_status,
)

MESSAGES = Path(__file__).resolve().parents[1] / "deforestation_alerts" / "message"


def _gdf(n, reviewed=0):
    status = ["Confirmed"] * reviewed + ["Not reviewed"] * (n - reviewed)
    return pd.DataFrame({"status": status})


def _spec(key):
    return next(s for s in STEPS if s.key == key)


def _resolve(catalog, dotted):
    node = catalog
    for part in dotted.split("."):
        assert part in node, f"'{dotted}' missing from the en catalog"
        node = node[part]
    return node


def _en_catalog():
    catalog = {}
    for f in (MESSAGES / "en").glob("*.json"):
        catalog.update(json.load(open(f)))
    return catalog


def test_workflow_registry_is_the_three_pipeline_steps():
    assert [s.key for s in STEPS] == ["aoi", "filter", "analysis"]
    assert len({s.page_view for s in STEPS}) == len(STEPS)
    assert step_index("filter") == 1
    assert step_index("analysis") == 2


def test_rail_registry_is_four_items_with_overview_as_a_panel_view():
    assert [r.key for r in RAIL_ITEMS] == ["about", "recipes", "overview", "aux"]
    assert [r.step_id for r in RAIL_ITEMS] == [1, 2, 3, 4]
    assert [r.display for r in RAIL_ITEMS] == ["dialog", "dialog", "panel", "dialog"]
    for r in RAIL_ITEMS:
        if r.display == "dialog":
            assert r.dialog_width, f"rail dialog '{r.key}' needs a width"
    overview = rail_item_for_step(3)
    assert overview.key == "overview" and overview.owns_map is True
    assert overview.page_view == "overview_tile"
    assert rail_item_for_step(99) is None
    assert not ({r.page_view for r in RAIL_ITEMS} & {s.page_view for s in STEPS})


def test_layout_flags():
    assert [s.owns_map for s in STEPS] == [True, True, False]
    assert [s.full_stage for s in STEPS] == [False, False, True]


def test_keys_resolve_in_the_english_catalog():
    catalog = _en_catalog()
    for s in STEPS:
        _resolve(catalog, s.label_key)
        for key in filter(None, [s.lock_reason_key, s.count_key_one, s.count_key_other]):
            _resolve(catalog, key)
    for r in RAIL_ITEMS:
        _resolve(catalog, r.label_key)


def test_aoi_count_is_the_aoi_name():
    aoi = _spec("aoi")
    assert step_status(aoi, None, None) is StepStatus.EMPTY
    assert step_status(aoi, None, "Canindeyú") is StepStatus.HAS_OUTPUTS
    assert aoi.count(None, "Canindeyú") == "Canindeyú"


def test_gates_filter_requires_aoi():
    filter_spec = _spec("filter")
    assert step_status(filter_spec, None, None) is StepStatus.LOCKED
    assert step_status(filter_spec, None, "Canindeyú") is StepStatus.EMPTY
    assert step_status(filter_spec, _gdf(3), "Canindeyú") is StepStatus.HAS_OUTPUTS


def test_gates_analysis_requires_alerts_and_counts_reviewed():
    analysis = _spec("analysis")
    assert step_status(analysis, None, "x") is StepStatus.LOCKED
    assert step_status(analysis, _gdf(0), "x") is StepStatus.EMPTY
    assert analysis.count(_gdf(5, reviewed=0), "x") == 0
    assert analysis.count(_gdf(5, reviewed=2), "x") == 2
    assert step_status(analysis, _gdf(5, reviewed=0), "x") is StepStatus.EMPTY
    assert step_status(analysis, _gdf(5, reviewed=2), "x") is StepStatus.HAS_OUTPUTS


def test_nav_targets_skip_locked_steps():
    states = step_states(None, None)
    assert nav_targets(0, states) == (None, None)
    states = step_states(None, "Canindeyú")
    assert nav_targets(0, states) == (None, 1)
    assert nav_targets(1, states) == (0, None)
    states = step_states(_gdf(2), "Canindeyú")
    assert nav_targets(1, states) == (0, 2)
    assert nav_targets(2, states) == (1, None)
