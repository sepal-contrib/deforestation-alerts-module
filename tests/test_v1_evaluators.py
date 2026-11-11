"""The Evaluation step of a converted v1 project: a note only, no split or merge."""

from types import SimpleNamespace

import ipyvuetify as v
import pytest
import reacton
import solara
from pysepal.i18n import set_locale
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.project import io
from deforestation_alerts.project.v1_migration import convert
from gui.i18n import t
from gui.scripts import merge_service, split_service
from gui.store.state_manager import AppState
from gui.store.workflow_steps import STEPS
from gui.tile import evaluators_step as es
from gui.tile.analysis.state import AnalysisState
from tests._v1_helpers import write_recipe

AOI_FC = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
}]}


def _migrated(tmp_path):
    folder = write_recipe(tmp_path / "recipe_2026-09-29-223039")
    convert(folder, lambda params: AOI_FC)
    return io.load_project(folder.name, folder.parent)


def _state(p):
    set_locale("en")
    s = AppState()
    s.load_project_state(p)
    return s


@solara.component
def _Harness(state):
    NotificationProvider()
    es.EvaluatorsStep(app_state=state, app_tile_model=SimpleNamespace(), analysis=AnalysisState(), gee_interface=None)


def _strings(w):
    out = []
    for c in getattr(w, "children", None) or []:
        out.extend([c] if isinstance(c, str) else _strings(c))
    return out


def test_step_shows_only_the_locked_note(tmp_path):
    _box, rc = reacton.render(_Harness(state=_state(_migrated(tmp_path))), handle_error=False)
    try:
        assert t("steps.evaluators.v1_locked") in _strings(rc.container)
        assert not rc.find(v.Checkbox).widgets
        assert len(rc.find(v.Btn).widgets) == 1  # Start analysis only
    finally:
        rc.close()


def test_split_and_merge_are_refused(tmp_path):
    state = _state(_migrated(tmp_path))
    for call, err in ((lambda: split_service._target(state, SimpleNamespace()), split_service.SplitError),
                      (lambda: merge_service._target(state, SimpleNamespace(), None), merge_service.ShareError)):
        with pytest.raises(err) as exc:
            call()
        assert exc.value.key == "migrated.read_only"


def test_step_does_not_block_analysis(tmp_path):
    p = _migrated(tmp_path)
    (spec,) = [s for s in STEPS if s.key == "evaluators"]
    assert spec.unlocked(p) and spec.count(p) == 1


def test_review_copy_refusal_is_unchanged(tmp_path):
    from deforestation_alerts.project.models import ReviewCopy
    p = _migrated(tmp_path)
    p.migrated_from = None
    p.review_copy = ReviewCopy(evaluator="Ana", evaluator_index=1, origin_project="o", set_id=p.active_set,
                               split_id="s1", set_fingerprint="fp", imported_at=p.created_at)
    with pytest.raises(split_service.SplitError) as exc:
        split_service._target(_state(p), SimpleNamespace())
    assert exc.value.key == "review_copy.read_only"
