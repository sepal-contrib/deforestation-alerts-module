"""One way to turn an exception into the words a user reads (spec §11, §12)."""

import re

from gui.i18n import has_message, t

#: Earth Engine's "Image.load: Image asset 'x' not found (does not exist or
#: caller does not have access)" and its collection/table variants.
_EE_NOT_FOUND = re.compile(r"asset '([^']+)' (?:is )?not found", re.IGNORECASE)


def error_text(exc: BaseException) -> str:
    """A keyed error (``deforestation_alerts.errors.KeyedError``, or any error
    with a ``key`` and ``values``) in the user's language; a ``ValueError``
    whose text is a message key likewise; an Earth Engine "asset not found"
    as its own message; anything else (a library's or the OS's own error) as
    its text, or its class name when it has none."""
    key = getattr(exc, "key", None)
    text = str(exc)
    if isinstance(key, str) and key:
        shown = t(key, **(getattr(exc, "values", None) or {}))
        # t() gives the key back when a value is missing: the English detail reads better
        return text if shown == key and text else shown
    if isinstance(exc, ValueError) and has_message(text):
        return t(text)
    if type(exc).__name__ == "EEException":
        found = _EE_NOT_FOUND.search(text)
        if found:
            return t("errors.ee_asset_not_found", asset=found.group(1))
    return text or exc.__class__.__name__
