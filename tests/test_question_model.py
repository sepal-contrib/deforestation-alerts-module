"""The questionnaire's model (spec D1): the default Loss driver question, an
old project.json without questions, and the key/code rules."""

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from deforestation_alerts.project import io
from deforestation_alerts.project.models import (
    DRIVER_OPTIONS, LOSS_DRIVER_KEY, VERDICTS, Project, Question, QuestionOption, default_questions)
from deforestation_alerts.project.questions import (
    REPORT_PLACEHOLDERS, RESERVED_KEYS, column, new_code, new_key, slug)


def _project(**kw):
    now = datetime.now()
    return Project(name="p", created_at=now, updated_at=now, **kw)


def test_a_new_project_starts_with_the_loss_driver_question():
    (q,) = _project().questions
    assert q.key == LOSS_DRIVER_KEY and q.label == "" and q.label_key == "analysis_tile.questionarie.loss_driver_label"
    assert q.multiple and q.allow_typed and not q.required
    assert q.ask_for == ["Confirmed", "Need revision"]
    assert [(o.code, o.label_key) for o in q.options] == list(DRIVER_OPTIONS)


def test_each_project_gets_its_own_default_list():
    a, b = _project(), _project()
    a.questions.append(Question(key="x", label="X", options=[QuestionOption(code="y", label="Y")]))
    assert len(b.questions) == 1


def test_an_old_manifest_without_questions_loads_with_the_default(tmp_path):
    p = _project()
    io.save_project(p, root=tmp_path)
    manifest = tmp_path / "p" / "project.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data.pop("questions")
    manifest.write_text(json.dumps(data), encoding="utf-8")
    loaded = io.load_project("p", root=tmp_path)
    assert [q.key for q in loaded.questions] == [LOSS_DRIVER_KEY]


def test_questions_round_trip_through_the_manifest(tmp_path):
    q = Question(key="road_access", label="Road access", multiple=False, required=True,
                 options=[QuestionOption(code="yes", label="Yes"), QuestionOption(code="no", label="No")],
                 ask_for=["Confirmed"])
    p = _project(questions=[q])
    io.save_project(p, root=tmp_path)
    assert io.load_project("p", root=tmp_path).questions == [q]


def test_ask_for_keeps_verdict_order_and_refuses_none():
    q = Question(key="k", label="K", ask_for=["Need revision", "Confirmed", "bogus"])
    assert q.ask_for == ["Confirmed", "Need revision"]
    with pytest.raises(ValidationError):
        Question(key="k", label="K", ask_for=[])


@pytest.mark.parametrize("text, expected", [
    ("Road access", "road_access"),
    ("¿Acceso a caminos?", "acceso_a_caminos"),
    ("  Déjà   vu!! ", "deja_vu"),
    ("道路", ""),
    ("x" * 60, "x" * 40),
])
def test_slug(text, expected):
    assert slug(text) == expected


def test_new_key_avoids_reserved_names_existing_keys_and_empty_slugs():
    loss = default_questions()
    assert new_key("Road access", loss) == "road_access"
    taken = loss + [Question(key="road_access", label="Road access")]
    assert new_key("Road access", taken) == "road_access_2"
    assert new_key("Verdict", loss) == "verdict_2"          # reserved
    assert new_key("Admin1", loss) == "admin1_2"            # a report placeholder
    assert new_key("Loss driver", loss) == "loss_driver_2"  # the default's key
    assert new_key("道路", loss) == "question"
    assert new_key("道路", loss + [Question(key="question", label="?")]) == "question_2"


def test_new_code_is_unique_within_the_question():
    opts = [QuestionOption(code="yes", label="Yes")]
    assert new_code("No", opts) == "no"
    assert new_code("Yes!", opts) == "yes_2"
    assert new_code("¿?", opts) == "option"


def test_column_maps_loss_driver_to_description():
    assert column(LOSS_DRIVER_KEY) == "description"
    assert column(default_questions()[0]) == "description"
    assert column("road_access") == "q_road_access"


def test_reserved_keys_cover_the_report_placeholders_and_verdict():
    assert "verdict" in RESERVED_KEYS
    assert set(REPORT_PLACEHOLDERS) <= RESERVED_KEYS
    assert set(VERDICTS) == {"Confirmed", "False Positive", "Need revision"}
