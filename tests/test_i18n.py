"""The message catalogue on pysepal 4's ``catalog()`` (spec §11, §13).

``gui.i18n.messages`` binds ``deforestation_alerts/message``; ``t()`` is its
thin alias. Every key the code uses exists in English, including keys built at
run time; counts are plural nodes; nothing reads the legacy ``cm`` Translator.
"""

import ast
import json
import re

import pytest
from pysepal.i18n import set_locale

from gui import i18n

LOCALES = ["en", "es-ES", "fr"]
# The generic widgets' namespaces (widgets.json): complete in every locale.
SCAFFOLD_NAMESPACES = ["common", "dialog", "widgets", "workflow"]
# The files whose every English key must be used by the code: the legacy
# cm-era strings were pruned in Task 42, and nothing may pile up there again.
PRUNED_FILES = ("app.json", "ui.json", "about.json")


@pytest.fixture(autouse=True)
def _pin_english():
    """Pin the active locale to English so assertions are deterministic."""
    set_locale("en")
    yield
    set_locale("en")


def test_t_resolves_and_formats():
    assert i18n.t("common._test_fmt", name="you") == "hello you"


def test_a_key_english_lacks_renders_visibly():
    assert i18n.t("common._no_such_key_") == "⟦common._no_such_key_⟧"


def test_a_missing_value_renders_the_key_instead_of_raising():
    assert i18n.t("common._test_fmt") == "common._test_fmt"


def test_counts_are_plural_nodes():
    assert i18n.t("common._test", count=1) == "1 item"
    assert i18n.t("common._test", count=3) == "3 items"
    assert not hasattr(i18n, "plural") and not hasattr(i18n, "get_translator")


def test_t_follows_the_pysepal_locale():
    en = i18n.t("common.cancel")
    set_locale("fr")
    assert i18n.t("common.cancel") == "Annuler" != en


def test_available_locales():
    assert i18n.app_available_locales()[0] == "en"
    assert set(LOCALES) <= set(i18n.app_available_locales())


def test_has_message():
    assert i18n.has_message("common.cancel") and i18n.has_message("common._test")
    assert not i18n.has_message("common._test.zero") and not i18n.has_message("nope")


def test_english_binds_and_every_other_problem_is_only_a_missing_translation():
    """``catalog()`` validated English when ``gui.i18n`` was imported. Until
    Task 44 completes es-ES and fr, the only problems allowed are untranslated
    keys: no placeholder mismatch, no extra key, no plural shape mismatch."""
    problems = i18n.messages.check()
    assert {p.code for p in problems} <= {"missing_key"}, [p for p in problems if p.code != "missing_key"]


def test_the_scaffold_namespaces_are_complete_in_every_locale():
    assert not [p for p in i18n.messages.check() if p.key.split(".")[0] in SCAFFOLD_NAMESPACES]


@pytest.mark.parametrize("locale", LOCALES)
def test_no_duplicate_top_level_namespaces(locale):
    """A key defined by two files makes ``catalog()`` refuse the locale."""
    seen = {}
    for f in sorted((i18n.MESSAGES_DIR / locale).glob("*.json")):
        for key in json.load(open(f, encoding="utf-8")):
            assert key not in seen, f"{locale}: namespace '{key}' defined in both {seen[key]} and {f.name}"
            seen[key] = f.name


@pytest.mark.parametrize("locale", LOCALES)
def test_step_one_is_labelled_aoi(locale):
    """Dates moved to the Period step (spec §4.2): step 1 is "AOI"."""
    app = json.load(open(i18n.MESSAGES_DIR / locale / "app.json", encoding="utf-8"))
    assert app["app"]["drawer_item"]["aoi"] == "AOI"


@pytest.mark.parametrize("locale", LOCALES)
def test_the_download_refusal_points_at_integrate_not_at_recipes(locale):
    """Recipes are gone: the files of an alert live in its set's folder, which
    Integrate & vectorize creates (named in the locale's own words once Task 44
    translated the step)."""
    set_locale(locale)
    text = i18n.t("analysis_tile.export_labels.download_no_recipe")
    assert i18n.t("app.drawer_item.integrate") in text or "Integrate & vectorize" in text
    assert not re.search(r"recipe|receta|recette", text, re.IGNORECASE), text


def test_nothing_reads_the_legacy_translator():
    """No ``pysepal.translator`` import and no ``cm`` name left (spec §11)."""
    offenders = []
    for base in SCANNED + ("tests",):
        for py in sorted((REPO / base).rglob("*.py")):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                bad = (isinstance(node, ast.ImportFrom)
                       and node.module in ("pysepal.translator", "deforestation_alerts.message")
                       or isinstance(node, ast.Import) and any(a.name == "pysepal.translator" for a in node.names)
                       or isinstance(node, ast.Name) and node.id == "cm")
                if bad:
                    offenders.append(f"{py.relative_to(REPO)}:{node.lineno}")
    assert offenders == []


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
    from gui.scripts.vis_layers import KINDS, NICFI_COLLECTIONS
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
        # gui.scripts.vis_layers: the layer kinds and the NICFI regions
        "steps.layers.kind_": list(KINDS),
        "steps.layers.region_": list(NICFI_COLLECTIONS),
        # gui.tile.overview_tile's export buttons
        "overview_tile.export_": ["csv", "gpkg", "geojson"],
    }


def _english() -> dict:
    catalogue = {}
    for f in sorted((i18n.MESSAGES_DIR / "en").glob("*.json")):
        catalogue.update(json.load(open(f, encoding="utf-8")))
    return catalogue


def _resolves(catalogue: dict, key: str) -> bool:
    """A message, or a plural node (``{"one", "other"}``) used with a count."""
    node = catalogue
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str) or (isinstance(node, dict) and set(node) == {"one", "other"})


def _keys_in_code(namespaces):
    """``(where, key)`` for every literal key: the key argument of ``t()``,
    and every other string naming a catalogue namespace (keys kept in a
    table or returned before they reach ``t()``); ``(where, node)`` for every
    f-string that builds one."""
    literal, built = [], []
    for base in SCANNED:
        for py in sorted((REPO / base).rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"))
            in_fstring = {id(n) for j in ast.walk(tree) if isinstance(j, ast.JoinedStr) for n in ast.walk(j)}
            for node in ast.walk(tree):
                where = f"{py.relative_to(REPO)}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t":
                    literal += [(where, a.value) for a in node.args[:1]
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


def _used_keys():
    """Every key the code uses: the literals, and every key the f-strings build."""
    catalogue = _english()
    literal, built = _keys_in_code(set(catalogue))
    table = _dynamic_keys()
    used = {key for _, key in literal}
    for _, node in built:
        head, *rest = node.values
        tail = rest[1].value if len(rest) == 2 and isinstance(rest[1], ast.Constant) else ""
        used |= {f"{head.value}{value}{tail}" for value in table.get(head.value, ())}
    return used


def _flat(node, prefix=""):
    out = set()
    for k, v in node.items():
        dotted = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and set(v) != {"one", "other"}:
            out |= _flat(v, dotted)
        else:
            out.add(dotted)
    return out


@pytest.mark.parametrize("name", PRUNED_FILES)
def test_every_english_key_of_the_legacy_files_is_used(name):
    keys = _flat(json.load(open(i18n.MESSAGES_DIR / "en" / name, encoding="utf-8")))
    unused = sorted(keys - _used_keys())
    assert not unused, f"unused keys in en/{name} (delete them in every locale): {unused}"
