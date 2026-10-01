"""Harness tests for gui.i18n (ported from the spatial_risk scaffold).

The catalog under test is the app's single message tree
(deforestation_alerts/message); gui.i18n resolves against it with dotted keys.
"""

import json

import pytest

from gui import i18n

LOCALES = ["en", "es-ES", "fr"]

# Namespaces introduced by the scaffold widgets — must stay in parity across
# every shipped locale (the domain namespaces predate this and are checked
# only for duplicate top-level keys).
SCAFFOLD_NAMESPACES = ["common", "dialog", "widgets", "workflow"]


@pytest.fixture(autouse=True)
def _pin_english():
    """Pin the active locale to English so assertions are deterministic."""
    from pysepal.i18n import set_locale

    set_locale("en")
    yield
    set_locale("en")


def test_t_resolves_and_formats():
    assert i18n.t("common._test_fmt", name="you") == "hello you"


def test_t_missing_key_returns_key():
    assert i18n.t("common._no_such_key_") == "common._no_such_key_"


def test_plural_picks_singular_and_plural():
    assert i18n.plural(1, "common._test_one", "common._test_other") == "1 item"
    assert i18n.plural(3, "common._test_one", "common._test_other") == "3 items"


def test_t_follows_the_pysepal_locale():
    from pysepal.i18n import set_locale

    en = i18n.t("common._test_fmt", name="you")
    set_locale("fr")
    assert i18n.get_translator()._target == "fr"
    set_locale("en")
    assert i18n.t("common._test_fmt", name="you") == en


def test_available_locales():
    assert set(LOCALES) <= set(i18n.app_available_locales())


def _key_tree(node, prefix=""):
    keys = set()
    for k, v in node.items():
        dotted = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys |= _key_tree(v, dotted)
        else:
            keys.add(dotted)
    return keys


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_scaffold_namespaces_parity(locale):
    """widgets.json must carry the same key tree in every locale."""
    en = json.load(open(i18n.MESSAGES_DIR / "en" / "widgets.json"))
    other = json.load(open(i18n.MESSAGES_DIR / locale / "widgets.json"))
    assert set(en.keys()) == set(SCAFFOLD_NAMESPACES) == set(other.keys())
    assert _key_tree(en) == _key_tree(other)


@pytest.mark.parametrize("locale", LOCALES)
def test_no_duplicate_top_level_namespaces(locale):
    """Translator merges all JSON files per locale flat — a namespace defined
    in two files would silently shadow one of them."""
    seen = {}
    for f in sorted((i18n.MESSAGES_DIR / locale).glob("*.json")):
        for key in json.load(open(f)):
            assert key not in seen, (
                f"{locale}: namespace '{key}' defined in both {seen[key]} and {f.name}"
            )
            seen[key] = f.name
