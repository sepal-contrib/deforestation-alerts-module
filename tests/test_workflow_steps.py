"""The STEPS registry is the single source of truth for step identity.

Locks the scaffold contract: navigation derives its view->step mapping from
the registry, ids/keys/page-views are unique, gates and nav_targets behave.
"""

import json
from pathlib import Path

from gui.store.navigation import PAGE_VIEW_TO_STEP, STEP_TO_PAGE_VIEW
from gui.store.workflow_steps import (
    STEPS,
    StepStatus,
    nav_targets,
    step_states,
    step_status,
)

MESSAGES = Path(__file__).resolve().parents[1] / "deforestation_alerts" / "message"


class _FakeGdf:
    def __init__(self, n):
        self._n = n

    def __len__(self):
        return self._n


def _spec(key):
    return next(s for s in STEPS if s.key == key)


def test_registry_identity_is_unique_and_ordered():
    ids = [s.step_id for s in STEPS]
    assert ids == sorted(ids), "STEPS must be listed in drawer order"
    assert len(set(ids)) == len(ids)
    assert len({s.key for s in STEPS}) == len(STEPS)
    assert len({s.page_view for s in STEPS}) == len(STEPS)


def test_navigation_map_derives_from_registry():
    assert PAGE_VIEW_TO_STEP == {s.page_view: s.step_id for s in STEPS}
    assert STEP_TO_PAGE_VIEW == {s.step_id: s.page_view for s in STEPS}


def test_display_values_are_known():
    assert {s.display for s in STEPS} <= {"dialog", "panel", "step"}
    for s in STEPS:
        if s.display == "dialog":
            assert s.dialog_width, f"dialog step '{s.key}' needs a width"
        assert s.panel_action in ("open", "close")


def test_label_keys_resolve_in_the_english_catalog():
    catalog = {}
    for f in (MESSAGES / "en").glob("*.json"):
        catalog.update(json.load(open(f)))
    for s in STEPS:
        node = catalog
        for part in s.label_key.split("."):
            assert part in node, f"'{s.label_key}' missing from the en catalog"
            node = node[part]
        keys = [s.lock_reason_key, s.count_key_one, s.count_key_other]
        for key in filter(None, keys):
            node = catalog
            for part in key.split("."):
                assert part in node, f"'{key}' missing from the en catalog"
                node = node[part]


def test_gates_filter_requires_aoi():
    filter_spec = _spec("filter")
    assert step_status(filter_spec, None, None) is StepStatus.LOCKED
    assert step_status(filter_spec, None, object()) is StepStatus.EMPTY


def test_gates_overview_and_analysis_require_alerts():
    for key in ("overview", "analysis"):
        spec = _spec(key)
        assert step_status(spec, None, object()) is StepStatus.LOCKED
        assert step_status(spec, _FakeGdf(0), object()) is StepStatus.EMPTY
        assert step_status(spec, _FakeGdf(3), object()) is StepStatus.HAS_OUTPUTS


def test_nav_targets_skip_locked_steps():
    # No AOI, no alerts: filter/overview/analysis are locked. From AOI (index 3)
    # the next non-locked step is None (nothing after aoi is unlocked); the
    # previous is aux (index 2).
    states = step_states(None, None)
    prev_t, next_t = nav_targets(3, states)
    assert prev_t == 2
    assert next_t is None
    # With alerts loaded everything unlocks.
    states = step_states(_FakeGdf(2), object())
    prev_t, next_t = nav_targets(3, states)
    assert prev_t == 2
    assert next_t == 4
