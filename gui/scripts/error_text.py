"""One way to turn an exception into the words a user reads (spec §11, §12)."""

import re

from gui.i18n import has_message, t

#: The asset an Earth Engine "not found" names: "Image.load: Image asset 'x'
#: not found (does not exist or caller does not have access)".
_EE_ASSET = re.compile(r"asset '([^']+)'", re.IGNORECASE)


def ee_no_access(exc: BaseException) -> bool:
    """Earth Engine's answer for an asset or collection the account cannot read
    ("... not found (does not exist or caller does not have access)"). Any
    other error (a network failure, an expired access token) is not a refusal
    and is shown as it is."""
    import ee

    text = str(exc).lower()
    return isinstance(exc, ee.EEException) and ("not found" in text or "caller does not have access" in text)


def error_text(exc: BaseException) -> str:
    """A keyed error (``deforestation_alerts.errors.KeyedError``, or any error
    with a ``key`` and ``values``) in the user's language; a ``ValueError``
    whose text is a message key likewise, or a wrapper chained to one; an Earth
    Engine "asset not found" as its own message; anything else (a library's or
    the OS's own error) as its text, or its class name when it has none.

    A keyed error whose message cannot be shown (a key the catalogue lacks, or
    a value missing) reads as its English detail, never as ``⟦key⟧``."""
    key = getattr(exc, "key", None)
    text = str(exc)
    if isinstance(key, str) and key:
        shown = t(key, **(getattr(exc, "values", None) or {})) if has_message(key) else key
        # t() gives the key back when a value is missing: the English detail reads better
        return text if shown == key and text else shown
    if (isinstance(exc, ValueError) or isinstance(exc.__cause__, ValueError)) and has_message(text):
        return t(text)
    if ee_no_access(exc):
        found = _EE_ASSET.search(text)
        if found:
            return t("errors.ee_asset_not_found", asset=found.group(1))
    return text or exc.__class__.__name__
