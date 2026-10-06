"""Answers to the review's questions (spec D2): the store keeps codes, the
screen and every export show labels in the current language.

Generalises the former ``loss_drivers`` module to any question. An entry that
is not one of the question's options is typed text and is stored as typed,
except that a save turns its commas into ``;``: answers are joined with
``", "``, and a typed "Roads, bridges" must not read back as two answers
(stores written before that rule read back as they were stored). Built-in
options (``label_key``) are also recognised by their label in every shipped
locale, so stores written with labels before the codes, or in another
language, read back to codes; the retired labels and the old "A, B and C"
join are read back for Loss driver only.
"""

from functools import lru_cache

import pandas as pd

from deforestation_alerts.project.models import LOSS_DRIVER_KEY
from deforestation_alerts.project.questions import column
from gui.i18n import MESSAGES_DIR, t

SEP = ", "

#: Labels earlier releases shipped and later corrected: stores reviewed with
#: them must still read back to their codes (Loss driver's options).
_RETIRED_LABELS = {
    "expansión agricola": "agriculture_expansion",
    "cultivo transitorio": "shifting_cultivation",
    "élevage des animaux": "livestock_grazing",
    "bûcherie": "logging",
    "culture en rotation": "shifting_cultivation",
    "minage": "mining",
}


def question_label(q) -> str:
    """``q``'s label in the current language (a built-in's), else as typed."""
    return t(q.label_key) if q.label_key else q.label


def option_label(o) -> str:
    """``o``'s label in the current language (a built-in's), else as typed."""
    return t(o.label_key) if o.label_key else o.label


def items(q) -> list:
    """A combobox's items: labelled in the current language, codes as values."""
    return [{"text": option_label(o), "value": o.code} for o in q.options]


@lru_cache(maxsize=None)
def _locale_labels(label_key: str) -> tuple:
    """Every shipped translation of ``label_key``, case-folded."""
    from pysepal.i18n.loading import load_locale

    from gui.i18n import messages

    out = []
    for locale in messages.available_locales():
        labels = load_locale(MESSAGES_DIR, locale).messages
        if label_key in labels:
            out.append(labels[label_key].strip().casefold())
    return tuple(out)


def _label_codes(q) -> dict:
    """Every label of every option of ``q`` (case-folded) -> its code."""
    table = dict(_RETIRED_LABELS) if q.key == LOSS_DRIVER_KEY else {}
    for o in q.options:
        if o.label_key:
            for text in _locale_labels(o.label_key):
                table[text] = o.code
        if o.label:
            table[o.label.strip().casefold()] = o.code
    return table


def _code(q, entry, table) -> str:
    """One entry as an option's code, else its text (stripped)."""
    if isinstance(entry, dict):  # a combobox item
        entry = entry.get("value", entry.get("text", ""))
    text = str(entry).strip()
    if text in {o.code for o in q.options}:
        return text
    return table.get(text.casefold(), text)


def _join(q, entries, typed=lambda text: text) -> str:
    """``entries`` as codes, typed ones through ``typed``, deduplicated."""
    table = _label_codes(q)
    known = {o.code for o in q.options}
    out = []
    for entry in entries:
        code = _code(q, entry, table)
        if code not in known:
            code = typed(code)
        if code and code not in out:
            out.append(code)
    return SEP.join(out)


def encode(q, entries) -> str:
    """What a save stores for ``entries`` (codes, items, labels or typed text)."""
    if entries is None:
        return ""
    if isinstance(entries, (str, dict)):
        entries = [entries]
    return _join(q, entries, typed=lambda text: text.replace(",", ";"))


def codes(q, stored) -> list:
    """The codes (and typed entries) of a stored answer."""
    if stored is None or (not isinstance(stored, str) and pd.isna(stored)):
        return []
    text = str(stored).strip()
    if not text:
        return []
    parts = text.split(SEP)
    if q.key == LOSS_DRIVER_KEY and " and " in parts[-1]:  # the old join: "A, B and C"
        last = parts[-1].rsplit(" and ", 1)
        table = _label_codes(q)
        known = {o.code for o in q.options}
        if all(_code(q, p, table) in known for p in last):  # else typed text with an "and"
            parts[-1:] = last
    joined = _join(q, parts)
    return joined.split(SEP) if joined else []


def as_items(q, codes_) -> list:
    """``codes_`` as combobox values: an item per known code, typed text as is."""
    by_code = {o.code: o for o in q.options}
    return [{"text": option_label(by_code[c]), "value": c} if c in by_code else c for c in codes_]


def selection(q, stored) -> list:
    """A stored answer as a combobox ``v_model`` (``return_object=True``)."""
    return as_items(q, codes(q, stored))


def label(q, stored) -> str:
    """A stored answer in the current language."""
    by_code = {o.code: o for o in q.options}
    return SEP.join(option_label(by_code[c]) if c in by_code else c for c in codes(q, stored))


def visible(q, verdict) -> bool:
    """Whether ``q`` is asked for ``verdict`` (all are, before one is picked)."""
    return verdict is None or verdict in q.ask_for


def with_labels(gdf, questions):
    """``gdf`` with every current question's column in the current language,
    and the columns of deleted questions dropped (a copy; ``None`` stays None).
    An unanswered row keeps its empty value."""
    if gdf is None:
        return gdf
    current = {column(q): q for q in questions}
    stale = [c for c in gdf.columns
             if (c == "description" or str(c).startswith("q_")) and c not in current]
    out = gdf.drop(columns=stale)  # a copy
    for col, q in current.items():
        if col in out.columns:
            out[col] = [label(q, v) if codes(q, v) else v for v in out[col]]
    return out
