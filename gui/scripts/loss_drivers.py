"""Deprecated: a thin wrapper over ``gui.scripts.questions`` for the default
question; removed in Task 10.

Loss drivers: the review's "Loss driver" answer, stored as codes.

The alert store keeps language-independent codes (``"logging, mining"``),
like the review status (``REVIEW_CHOICES``): a project reviewed in Spanish and
then in French must not end up with labels of both languages, which nothing
could map back. The combobox shows the labels of the current language, and
an export translates the codes into the language it is made in.

The combobox also takes free text: an entry that is not a known driver is
stored as typed. Stores written before the codes hold the labels joined the
old way (``"Logging, Mining and Other"``, in whatever language the session
used); ``codes`` reads them back to codes.
"""

from deforestation_alerts.project.models import DRIVER_OPTIONS, default_questions
from gui.scripts import questions as _q

DRIVERS = DRIVER_OPTIONS
DEFAULT = "other"
_LOSS = default_questions()[0]


def items() -> list:
    return _q.items(_LOSS)


def selection(stored) -> list:
    return _q.selection(_LOSS, stored)


def encode(selected) -> str:
    return _q.encode(_LOSS, selected)


def codes(stored) -> list:
    return _q.codes(_LOSS, stored)


def label(stored) -> str:
    return _q.label(_LOSS, stored)


def with_labels(gdf):
    """Only ``description`` translated; nothing dropped (the old behaviour)."""
    if gdf is None or "description" not in getattr(gdf, "columns", ()):
        return gdf
    out = gdf.copy()
    out["description"] = [label(d) if codes(d) else d for d in out["description"]]
    return out
