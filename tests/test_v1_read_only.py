from datetime import datetime
from types import SimpleNamespace

import pytest

from deforestation_alerts.project.models import MigratedFrom, Project
from deforestation_alerts.project.review_copy import ReadOnlyProject, is_migrated, read_only, refuse_if_read_only
from gui.i18n import t
from gui.scripts.setup_guard import banner_text


def _migrated():
    mf = MigratedFrom(migrated_at=datetime.now(), n_alerts=3, n_reviewed=1)
    return SimpleNamespace(name="v1", review_copy=None, migrated_from=mf)


def test_migrated_project_is_read_only():
    p = _migrated()
    assert read_only(p) and is_migrated(p)
    assert not is_migrated(SimpleNamespace(review_copy=None, migrated_from=None))


def test_refusal_carries_the_migrated_key():
    with pytest.raises(ReadOnlyProject) as exc:
        refuse_if_read_only(_migrated())
    assert exc.value.key == "migrated.read_only" and exc.value.values == {}


def test_review_copy_refusal_unchanged():
    rc = SimpleNamespace(evaluator="Ana", origin_project="orig")
    p = SimpleNamespace(name="c", review_copy=rc, migrated_from=None)
    with pytest.raises(ReadOnlyProject) as exc:
        refuse_if_read_only(p)
    assert exc.value.key == "review_copy.read_only"
    assert exc.value.values == {"evaluator": "Ana", "origin_project": "orig"}
    assert ReadOnlyProject.key == "review_copy.read_only"


def test_banner_text_for_migrated():
    assert banner_text(_migrated()) == t("migrated.read_only") != "migrated.read_only"


def test_old_manifest_loads_without_the_field():
    raw = Project(name="x", created_at=datetime.now(), updated_at=datetime.now()).model_dump(mode="json")
    raw.pop("migrated_from")
    assert Project.model_validate(raw).migrated_from is None
