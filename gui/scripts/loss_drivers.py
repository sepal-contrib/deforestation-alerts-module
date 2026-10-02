"""Loss drivers: the review's "Loss driver" answer, stored as codes.

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

from functools import lru_cache

import pandas as pd

from gui.i18n import MESSAGES_DIR, t

#: The drivers the combobox offers: code, then the key of its label.
DRIVERS = (
    ("agriculture_expansion", "analysis_tile.questionarie.loss_driver1"),
    ("livestock_grazing", "analysis_tile.questionarie.loss_driver2"),
    ("logging", "analysis_tile.questionarie.loss_driver3"),
    ("urban_expansion", "analysis_tile.questionarie.loss_driver4"),
    ("shifting_cultivation", "analysis_tile.questionarie.loss_driver5"),
    ("mining", "analysis_tile.questionarie.loss_driver6"),
    ("forest_fires", "analysis_tile.questionarie.loss_driver7"),
    ("hurricanes", "analysis_tile.questionarie.loss_driver8"),
    ("other", "analysis_tile.questionarie.loss_driver9"),
)
DEFAULT = "other"
_KEYS = dict(DRIVERS)
#: Labels earlier releases shipped and later corrected: stores reviewed with
#: them must still read back to their codes.
_RETIRED_LABELS = {
    "expansión agricola": "agriculture_expansion",
    "cultivo transitorio": "shifting_cultivation",
    "élevage des animaux": "livestock_grazing",
    "bûcherie": "logging",
    "culture en rotation": "shifting_cultivation",
    "minage": "mining",
}
_SEP = ", "


def items() -> list:
    """The combobox items, labelled in the current language."""
    return [{"text": t(key), "value": code} for code, key in DRIVERS]


def selection(stored) -> list:
    """A stored description as the combobox's ``v_model``: a known driver as
    its item (the chip shows the label in the current language), free text as
    typed. The combobox returns objects (``return_object=True``): Vuetify 2's
    combobox draws a chip from the selected value itself, so a bare code
    would show as "agriculture_expansion"."""
    return [{"text": t(_KEYS[c]), "value": c} if c in _KEYS else c for c in codes(stored)]


@lru_cache(maxsize=1)
def _label_codes() -> dict:
    """Every shipped label of every driver (any locale, case-folded) → its code."""
    from pysepal.i18n.loading import load_locale

    from gui.i18n import messages

    table = dict(_RETIRED_LABELS)
    for locale in messages.available_locales():
        labels = load_locale(MESSAGES_DIR, locale).messages
        for code, key in DRIVERS:
            if key in labels:
                table[labels[key].strip().casefold()] = code
    return table


def _code(entry) -> str:
    """One selected entry as stored: a known driver's code, else the text."""
    if isinstance(entry, dict):  # a combobox item, should it return objects
        entry = entry.get("value", entry.get("text", ""))
    text = str(entry).strip()
    if text in _KEYS:
        return text
    return _label_codes().get(text.casefold(), text)


def encode(selected) -> str:
    """What a review Save stores for the combobox's selection."""
    if selected is None:
        return ""
    if isinstance(selected, (str, dict)):
        selected = [selected]
    out = []
    for entry in selected:
        code = _code(entry)
        if code and code not in out:
            out.append(code)
    return _SEP.join(out)


def codes(stored) -> list:
    """The codes (and free-text entries) of a stored description, legacy
    labels included."""
    if stored is None or (not isinstance(stored, str) and pd.isna(stored)):
        return []
    text = str(stored).strip()
    if not text:
        return []
    parts = text.split(_SEP)
    if " and " in parts[-1]:  # the old join: "A, B and C"
        last = parts[-1].rsplit(" and ", 1)
        if all(_known(p) for p in last):  # else free text with an "and" in it
            parts[-1:] = last
    stored_codes = encode(parts)
    return stored_codes.split(_SEP) if stored_codes else []


def _known(entry) -> bool:
    return _code(entry) in _KEYS


def label(stored) -> str:
    """A stored description in the current language."""
    return _SEP.join(t(_KEYS[c]) if c in _KEYS else c for c in codes(stored))


def with_labels(gdf):
    """``gdf`` with its ``description`` column in the current language (a
    copy; ``gdf`` itself when it has no such column)."""
    if gdf is None or "description" not in getattr(gdf, "columns", ()):
        return gdf
    out = gdf.copy()
    out["description"] = [label(d) if codes(d) else d for d in out["description"]]
    return out
