"""Internationalization for the Forest Cover Loss Alerts GUI (spec §11).

The catalogue is ``deforestation_alerts/message/<locale>/*.json``, bound once
with pysepal 4's ``catalog()``. ``messages.msg(key, **values)`` renders a key
in the current kernel's locale (``pysepal.i18n.current_locale()``, which
MapApp's language selector writes); reading the locale during a render
subscribes the component, so a language switch re-renders it in place.

``t()`` is a thin alias of ``messages.msg``: a key English lacks renders as
``⟦key⟧`` (non-strict catalogue, logged once) and a template that cannot be
filled renders as the key, so a gap degrades visibly instead of crashing a
tile. Counts use plural nodes: ``t("workflow.count_sources", count=n)``.

No string may be resolved at import time: call ``t()`` inside a render, an
event handler or the code that builds a widget. Workers never translate; they
return a message key and its values (``deforestation_alerts.errors``).
"""

import logging
from functools import lru_cache
from pathlib import Path

from pysepal.i18n import MessageFormatError, catalog
from pysepal.i18n.loading import ENGLISH, load_locale

logger = logging.getLogger("SEPALUI.deforestation_alerts")

MESSAGES_DIR = Path(__file__).resolve().parents[1] / "deforestation_alerts" / "message"

messages = catalog(MESSAGES_DIR, strict=False)


def t(key: str, /, **values) -> str:
    """``messages.msg(key, **values)``; the key itself when a value is missing.

    ``key`` is positional-only so a message may carry a ``{key}`` placeholder."""
    try:
        return messages.msg(key, **values)
    except MessageFormatError:
        logger.warning("message %r could not be rendered with %s", key, sorted(values))
        return key


@lru_cache(maxsize=1)
def _english_keys() -> frozenset:
    english = load_locale(MESSAGES_DIR, ENGLISH)
    return frozenset(english.messages) | english.plural_keys


def has_message(key: str) -> bool:
    """Whether English defines ``key`` (a message or a plural node)."""
    return key in _english_keys()


def app_available_locales() -> list:
    """Locale codes the catalogue ships, English first (MapApp's selector)."""
    return list(messages.available_locales())
