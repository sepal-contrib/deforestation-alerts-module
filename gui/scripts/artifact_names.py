"""Solara-free helpers for artifact naming.

One home for the name conventions every creation form shares: suggested
default names, key sanitizing, and the (message, error) states of the shared
ArtifactNameField. Imported by tiles and form dialogs; never imports solara.
"""

import re
from typing import Optional, Tuple


def suggest_name(prefix: str, taken) -> str:
    """Smallest '<prefix>_<n>' (n>=1) not already in `taken`."""
    n = 1
    while f"{prefix}_{n}" in taken:
        n += 1
    return f"{prefix}_{n}"


def sanitize_key(name: Optional[str]) -> str:
    """Normalise a user-typed name to a key/path-safe token.

    Keeps alphanumerics, dash and underscore; collapses any other run into a
    single underscore and trims leading/trailing ones.
    """
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")


def name_field_messages(clean_key: str, exists: bool, attempted: bool) -> Tuple[str, bool]:
    """(i18n message key, is_error) for an ArtifactNameField helper line.

    Empty name is only *flagged* red after a submit attempt; an existing key
    is a warning (creation will ask to confirm), never a blocker.
    """
    if not clean_key:
        return "widgets.artifact_name.required", attempted
    if exists:
        return "widgets.artifact_name.exists_warning", False
    return "widgets.artifact_name.saved_as", False
