"""The questionnaire's naming rules (spec D1, D2): a question's key and an
option's code are made once from their label and never change, and one
function maps a question to its alert-store column."""

import re
import unicodedata
from typing import Union

from deforestation_alerts.project.models import LOSS_DRIVER_KEY, Question
from deforestation_alerts.project.store_schema import RESERVED_COLUMNS

MAX_KEY = 40

#: What joins the codes of a multi-answer in its store column ("a, b").
SEP = ", "

#: The report placeholders the Word report fills from the alert itself
#: (``report_builder.generate_deforestation_report_with_word_template``).
REPORT_PLACEHOLDERS = ("admin1", "admin2", "admin3", "detection_date1", "detection_date2",
                       "confirmation_date", "before_img", "after_img", "alert_system", "area_loss",
                       "group_size", "group_alerts")

#: Every name a question key may not take: the review's own placeholder, the
#: report's, and the alert store's columns (``evaluator``: the split's, spec §3).
RESERVED_KEYS = frozenset({"verdict", *REPORT_PLACEHOLDERS, *RESERVED_COLUMNS})

_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """Lowercase ASCII letters, digits and ``_``, accents stripped, at most
    ``MAX_KEY`` characters; ``""`` when nothing Latin is left."""
    ascii_ = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return _NOT_SLUG.sub("_", ascii_.lower()).strip("_")[:MAX_KEY].strip("_")


def _unique(base: str, taken: set, fallback: str) -> str:
    base = base or fallback
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def new_key(label: str, questions: list, columns=()) -> str:
    """The key a new question labelled ``label`` gets in a project that has
    ``questions``. ``columns``: other alert-store columns its column must not
    take (``attribute_columns``: a layer made before attribution reserved the
    ``q_`` prefix may have written one)."""
    taken = set(RESERVED_KEYS) | {q.key for q in questions}
    taken |= {c[len("q_"):] for c in columns if isinstance(c, str) and c.startswith("q_")}
    return _unique(slug(label), taken, "question")


def attribute_columns(project) -> set:
    """Every column the project's alert sets' attribute layers write."""
    return {b.get("column") for s in project.alert_sets.values() for layer in s.params.attribute_list()
            for b in (layer or {}).get("bands", []) if b.get("column")}


def new_code(label: str, options: list) -> str:
    """The code a new option labelled ``label`` gets among ``options``."""
    return _unique(slug(label), {o.code for o in options}, "option")


def column(q: Union[Question, str]) -> str:
    """The alert-store column of a question (or of its key)."""
    key = q if isinstance(q, str) else q.key
    return "description" if key == LOSS_DRIVER_KEY else f"q_{key}"


def is_answer_column(name) -> bool:
    """Whether ``name`` is a column ``column`` makes (of any question, current or deleted)."""
    return isinstance(name, str) and (name == column(LOSS_DRIVER_KEY) or name.startswith("q_"))
