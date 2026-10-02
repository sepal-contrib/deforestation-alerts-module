"""Harness tests for gui.i18n (ported from the spatial_risk scaffold).

The catalog under test is the app's single message tree
(deforestation_alerts/message); gui.i18n resolves against it with dotted keys.
"""

import ast
import json
import re

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


@pytest.mark.parametrize("locale", LOCALES)
def test_step_one_is_labelled_aoi(locale):
    """Dates moved to the Period step (spec §4.2): step 1 is "AOI"."""
    app = json.load(open(i18n.MESSAGES_DIR / locale / "app.json", encoding="utf-8"))
    assert app["app"]["drawer_item"]["aoi"] == "AOI"


@pytest.mark.parametrize("locale", LOCALES)
def test_the_download_refusal_points_at_integrate_not_at_recipes(locale):
    """Recipes are gone: the files of an alert live in its set's folder, which
    Integrate & vectorize creates (the step keeps its English name in es/fr,
    which fall back to English for it until slice 6)."""
    ui = json.load(open(i18n.MESSAGES_DIR / locale / "ui.json", encoding="utf-8"))
    text = ui["analysis_tile"]["export_labels"]["download_no_recipe"]
    assert "Integrate & vectorize" in text
    assert not re.search(r"recipe|receta|recette", text, re.IGNORECASE), text


# ---------------------------------------------------------------------------
# Spec §13: every key the code passes to t() exists in the English catalogue.
# ---------------------------------------------------------------------------
REPO = i18n.MESSAGES_DIR.parents[1]
SCANNED = ("gui", "deforestation_alerts")
KEY_RE = re.compile(r"[a-z_]+(\.[A-Za-z0-9_]+)+")


def _dynamic_keys():
    """Every key the code builds with an f-string, by its literal head, with
    every value its one placeholder takes."""
    from deforestation_alerts.engine.fingerprint import Status
    from deforestation_alerts.project import periods
    from gui.tile.integrate_step import SORTINGS
    from gui.widget.attribute_form import _STATS
    from gui.widget.product_table import STATUS_ICONS
    from gui.widget.source_modal import ENCODINGS, FORMATS

    return {
        "steps.integrate.set_": [Status.STALE.value, Status.MISSING.value],
        "steps.integrate.sorting_": SORTINGS,
        "steps.integrate.attr_stat_": _STATS,
        # deforestation_alerts.project.periods' error keys
        "steps.period.error_": list(periods.ERRORS),
        # engine.grid.validate_projection's error keys
        "steps.prepare.grid_": ["need_epsg", "bad_epsg", "geographic", "bad_resolution"],
        "widgets.product_table.status_": list(STATUS_ICONS),
        "legend.mask.": ["keep", "exclude", "off"],
        # gui.widget.source_modal's format presets and date encodings
        "steps.alerts.format_": list(FORMATS),
        "steps.alerts.format_help_": list(FORMATS),
        "steps.alerts.encoding_": list(ENCODINGS),
        # gui.tile.masks_card's origin chip
        "steps.prepare.mask_origin_": ["gee", "file"],
    }


def _english() -> dict:
    catalogue = {}
    for f in sorted((i18n.MESSAGES_DIR / "en").glob("*.json")):
        catalogue.update(json.load(open(f, encoding="utf-8")))
    return catalogue


def _resolves(catalogue: dict, key: str) -> bool:
    node = catalogue
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def _keys_in_code(namespaces):
    """``(where, key)`` for every literal key: the key arguments of ``t()`` and
    ``plural()``, and every other string naming a catalogue namespace (keys
    kept in a table or returned before they reach ``t()``); ``(where, node)``
    for every f-string that builds one."""
    literal, built = [], []
    for base in SCANNED:
        for py in sorted((REPO / base).rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            in_fstring = {id(n) for j in ast.walk(tree) if isinstance(j, ast.JoinedStr) for n in ast.walk(j)}
            for node in ast.walk(tree):
                where = f"{py.relative_to(REPO)}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "plural"):
                    args = node.args[:1] if node.func.id == "t" else node.args[1:3]
                    literal += [(where, a.value) for a in args
                                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in in_fstring:
                    if KEY_RE.fullmatch(node.value) and node.value.split(".")[0] in namespaces:
                        literal.append((where, node.value))
                elif isinstance(node, ast.JoinedStr) and node.values:
                    head = node.values[0]
                    if (isinstance(head, ast.Constant) and "." in head.value
                            and head.value.split(".")[0] in namespaces):
                        built.append((where, node))
    return literal, built


def test_every_key_the_code_uses_exists_in_the_english_catalogue():
    catalogue = _english()
    literal, built = _keys_in_code(set(catalogue))
    table = _dynamic_keys()
    missing, unknown, heads = [], [], set()
    for where, key in literal:
        if not _resolves(catalogue, key):
            missing.append(f"{where} {key}")
    for where, node in built:
        head, *rest = node.values
        tail = rest[1].value if len(rest) == 2 and isinstance(rest[1], ast.Constant) else ""
        if head.value not in table or not rest or not isinstance(rest[0], ast.FormattedValue) or len(rest) > 2:
            unknown.append(f"{where} {ast.unparse(node)}")
            continue
        heads.add(head.value)
        missing += [f"{where} {head.value}{value}{tail}" for value in table[head.value]
                    if not _resolves(catalogue, f"{head.value}{value}{tail}")]

    assert len({key for _, key in literal}) > 150, "the scan no longer sees the code's keys"
    assert not unknown, "keys built from values this test does not know; add them to _dynamic_keys(): " + "; ".join(unknown)
    assert heads == set(table), f"_dynamic_keys() lists heads the code no longer builds: {set(table) - heads}"
    assert not missing, "keys missing from message/en: " + "; ".join(missing)
