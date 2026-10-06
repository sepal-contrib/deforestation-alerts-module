"""The Overview's reading and drawing helpers (``gui.scripts.overview_data``)
for the questionnaire (spec D9): which answer columns a summary reads, what
makes a memoized summary stale, and one figure per question.

Solara-free: a project, a store on disk and the module's own functions.
"""

from datetime import date, datetime

import pandas as pd
import pytest

from deforestation_alerts.project.models import (
    AlertSet,
    AlertSetParams,
    Period,
    Project,
    Question,
    QuestionOption,
)
from gui.scripts import charts, overview_data, set_store

JAN = Period(id="2026-01-01_2026-01-31", start=date(2026, 1, 1), end=date(2026, 1, 31))
ROAD = Question(key="road", label="Road access", options=[QuestionOption(code="yes", label="Yes"),
                                                          QuestionOption(code="no", label="No")])


def _project(tmp_path, monkeypatch):
    """A project (default questionnaire: Loss driver) with one set whose store
    holds four alerts -- three reviewed, one answering Road access."""
    monkeypatch.setenv("DEFORESTATION_ALERTS_DATA_DIR", str(tmp_path))
    now = datetime.now()
    p = Project(name="d", created_at=now, updated_at=now)
    p.periods[JAN.id] = JAN
    rel = f"periods/{JAN.id}/sets/glad-l"
    s = AlertSet(id="s1", period_id=JAN.id, sources=["GLAD-L"], digit_map={0: "GLAD-L"},
                 params=AlertSetParams(), store_rel_path=f"{rel}/alert_db.parquet",
                 combined_rel_path=f"{rel}/c.tif", footprint_rel_path=f"{rel}/footprint.tif",
                 n_alerts=4, n_reviewed=3, fingerprint="f", created_at=now)
    p.alert_sets[s.id] = s
    p.active_set = s.id
    store = set_store.store_path(p, s)
    store.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "alert_id": [0, 1, 2, 3],
        "status": ["Confirmed", "False Positive", "Need revision", "Not reviewed"],
        "area_ha": [1.5, 0.25, 2.0, 0.75],
        "description": ["Logging", "", "mining", "logging"],  # a legacy label read back to its code
        "q_road": ["yes", "", "no", "yes"],
    }).to_parquet(store)
    return p, s


@pytest.fixture(autouse=True)
def _forget():
    overview_data.forget_summaries()
    yield
    overview_data.forget_summaries()


# --- which columns -------------------------------------------------------

def test_answer_columns_map_each_question_to_its_store_column_in_order(tmp_path, monkeypatch):
    p, _ = _project(tmp_path, monkeypatch)
    assert overview_data.answer_columns(p) == {"loss_driver": "description"}
    p.questions.insert(0, ROAD)
    assert list(overview_data.answer_columns(p).items()) == [("road", "q_road"), ("loss_driver", "description")]


def test_no_project_or_no_questions_means_no_answer_columns(tmp_path, monkeypatch):
    p, _ = _project(tmp_path, monkeypatch)
    p.questions.clear()
    assert overview_data.answer_columns(p) == {} and overview_data.answer_columns(None) == {}


# --- the memo key --------------------------------------------------------

def test_summary_key_changes_when_a_question_is_added_or_removed(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    before = overview_data.summary_key(p, s)
    p.questions.append(ROAD)
    added = overview_data.summary_key(p, s)
    assert added != before
    p.questions.pop(0)  # Loss driver deleted
    assert overview_data.summary_key(p, s) not in (before, added)


def test_summary_key_changes_when_a_questions_options_change(tmp_path, monkeypatch):
    """The split reads labels back to codes through the options, so an added
    option can change what a stored answer counts as."""
    p, s = _project(tmp_path, monkeypatch)
    p.questions.append(ROAD.model_copy(deep=True))
    before = overview_data.summary_key(p, s)
    p.questions[-1].options.append(QuestionOption(code="maybe", label="Maybe"))
    assert overview_data.summary_key(p, s) != before


def test_summary_key_is_hashable_with_questions(tmp_path, monkeypatch):
    """Ruling R4: pydantic models are not hashable, and the key is a dict key."""
    p, s = _project(tmp_path, monkeypatch)
    p.questions.append(ROAD)
    hash(overview_data.summary_key(p, s))
    assert overview_data.summary_key(p, s) == overview_data.summary_key(p, s)


# --- reading -------------------------------------------------------------

def _split(p):
    from gui.scripts import questions as qs

    by_key = {q.key: q for q in p.questions}
    return lambda key, value: qs.codes(by_key[key], value)


def test_summary_of_breaks_down_every_question_of_the_project(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    p.questions.append(ROAD)
    summary = overview_data.summary_of(p, s, split_of=_split(p))
    assert [k for k, _ in summary.by_question] == ["loss_driver", "road"]
    assert dict((c, n) for c, _, n in summary.breakdown("loss_driver")) == {
        "logging": 1, "mining": 1, "unknown": 1}  # "Logging" read back to its code
    assert dict((c, n) for c, _, n in summary.breakdown("road")) == {"yes": 1, "no": 1, "unknown": 1}


def test_a_question_added_after_the_read_is_read_again_not_served_stale(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    first = overview_data.summary_of(p, s, split_of=_split(p))
    assert first.breakdown("road") == ()
    p.questions.append(ROAD)
    assert overview_data.summary_of(p, s, split_of=_split(p)).breakdown("road") != ()


def test_a_question_whose_column_the_store_lacks_reads_as_unanswered(tmp_path, monkeypatch):
    """Review focus 5: an old store, a new question -- never an error."""
    p, s = _project(tmp_path, monkeypatch)
    p.questions.append(Question(key="fire", label="Fire"))
    summary = overview_data.summary_of(p, s, split_of=_split(p))
    assert summary.breakdown("fire") == (("unknown", pytest.approx(3.75), 3),)


def test_a_project_with_no_questions_reads_no_breakdown(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    p.questions.clear()
    summary = overview_data.summary_of(p, s)
    assert summary.by_question == () and summary.n_alerts == 4


def test_history_of_carries_the_questions_too(tmp_path, monkeypatch):
    p, _ = _project(tmp_path, monkeypatch)
    p.questions.append(ROAD)
    [(_, _, summary)] = overview_data.history_of(p, split_of=_split(p))
    assert [k for k, _ in summary.by_question] == ["loss_driver", "road"]


# --- drawing -------------------------------------------------------------

def test_draw_figures_draws_one_figure_per_question_labelled_by_the_caller(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    p.questions.append(ROAD)
    summary = overview_data.summary_of(p, s, split_of=_split(p))
    seen = []
    figures = overview_data.draw_figures(
        summary, (), measure=charts.MEASURE_AREA, theme=charts.theme_for(False),
        label_of=lambda q, code: seen.append((q.key, code)) or f"{q.key}:{code}",
        questions=tuple(p.questions))
    assert set(figures) == set(overview_data.FIGURES) | {"q:loss_driver", "q:road"}
    assert "drivers" not in figures
    assert ("road", "yes") in seen and ("loss_driver", "logging") in seen
    assert "road:yes" in figures["q:road"] and "road:yes" not in figures["q:loss_driver"]


def test_draw_figures_without_questions_draws_only_the_fixed_figures(tmp_path, monkeypatch):
    p, s = _project(tmp_path, monkeypatch)
    summary = overview_data.summary_of(p, s, split_of=_split(p))
    figures = overview_data.draw_figures(summary, (), measure=charts.MEASURE_AREA,
                                         theme=charts.theme_for(False), label_of=lambda q, code: code)
    assert set(figures) == set(overview_data.FIGURES)
