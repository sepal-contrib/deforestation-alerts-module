"""Internationalization for the Forest Cover Loss Alerts GUI.

Reuses pysepal's Translator (JSON locale folders with es->en fallback). The
target language is pysepal 4's per-kernel Solara locale (``current_locale()``),
which MapApp's LocaleSelect writes, so a language switch re-renders t() strings
in place. No string may be resolved at import time — always call t()/plural()
inside a component render.

The catalog is the app's existing ``deforestation_alerts/message`` tree (the
same one ``deforestation_alerts.message.cm`` reads) — NOT a second parallel
``gui/messages`` tree — so the generic widgets' keys (``common.*``,
``widgets.*``, ``dialog.*``, ``workflow.*``) live beside the domain keys and
the LocaleSelect language switch applies to both.
"""

from pathlib import Path

from pysepal.i18n import current_locale
from pysepal.translator import Translator

MESSAGES_DIR = Path(__file__).resolve().parents[1] / "deforestation_alerts" / "message"

# One Translator per locale code; the catalog is read-only, so sharing across
# kernels is safe.
_translators: dict = {}


def get_translator() -> Translator:
    """Translator for the current kernel's locale (subscribes during a render)."""
    locale = current_locale()
    if locale not in _translators:
        _translators[locale] = Translator(MESSAGES_DIR, target=locale)
    return _translators[locale]


def t(key: str, /, **fmt) -> str:
    """Resolve a dotted key against the active catalog (es->en->key) and
    str.format(**fmt). A key missing in both languages returns the key string
    so a gap degrades visibly instead of crashing the GUI.

    ``key`` is positional-only so a catalog value may carry a ``{key}``
    placeholder passed as a keyword (``key=...``) without colliding with this
    lookup parameter."""
    node = get_translator()
    try:
        for part in key.split("."):
            node = node[part]
        text = str(node)
        return text.format(**fmt) if fmt else text
    except Exception:
        return key


def plural(n: int, one_key: str, other_key: str, /, **fmt) -> str:
    """Pick the singular vs plural key for a count and interpolate n.

    The selector args are positional-only so a catalog value may carry an
    ``{n}``/``{one_key}``/``{other_key}`` placeholder without colliding."""
    fmt.setdefault("n", n)
    return t(one_key if n == 1 else other_key, **fmt)


def app_available_locales() -> list:
    """Locale codes shipped under deforestation_alerts/message/ (drives the selector)."""
    return sorted(p.name for p in MESSAGES_DIR.glob("[!._]*") if p.is_dir())
