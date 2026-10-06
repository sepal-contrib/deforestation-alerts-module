"""The message catalogue on pysepal 4's ``catalog()`` (spec §11, §13).

``gui.i18n.messages`` binds ``deforestation_alerts/message``; ``t()`` is its
thin alias. Every key the code uses exists in English, including keys built at
run time; counts are plural nodes; nothing reads the legacy ``cm`` Translator.
"""

import ast
import json
import re
from pathlib import Path

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


def test_the_catalogue_is_complete_in_every_locale():
    """Spec §13 from slice 6: no missing, extra, misshapen or mistemplated message."""
    assert i18n.messages.check() == ()


#: Names kept as they are in every language (product, format and unit names).
PROPER_NAMES = ("GLAD-L", "GLAD-S2", "RADD", "Planet", "NICFI", "Earth Engine", "SEPAL", "GeoTIFF", "GeoPackage",
                "EPSG", "UTM", "CCDC", "Sentinel-1", "Sentinel-2", "Landsat", "Hansen", "PMTiles", "tippecanoe",
                "Python", "PATH", "Word", ".docx", "CSV", "GPKG", "GeoJSON", "GIF", "yyddd", "yymm", "yyyyddd", "Int32",
                "uint8", "AOI", "GB", "MB", "ha", "px", "m")
#: Translations rightly identical to English (a cognate such as French
#: "Source" or "Total", Spanish "Total"). Add a key only when the word is the
#: same in that language, never to silence the test.
SAME_IN = {
    "es-ES": {"steps.layers.region_asia", "overview_tile.table_total", "steps.integrate.attr_col_raster"},
    "fr": {
        # "images", "source" and "verdict" are the same words in French.
        "analysis.timeline.images", "analysis_tile.facts.source",
        "analysis_tile.verdict.title",
        "common.actions", "overview_tile.table_total", "steps.alerts.col_source", "steps.alerts.list_title",
        "steps.integrate.attr_col_raster", "steps.integrate.attr_stat_max", "steps.integrate.attr_stat_min",
        "steps.layers.field_max", "steps.layers.field_min", "steps.prepare.col_mode",
        "steps.prepare.col_source", "steps.project.count_sources.one", "steps.project.count_sources.many",
        "steps.project.count_sources.other", "workflow.count_sources.one", "workflow.count_sources.many",
        "workflow.count_sources.other",
    },
}


def _letters(text: str) -> str:
    """What is left to translate once placeholders and names are taken out."""
    text = re.sub(r"\{[A-Za-z_]+\}", "", text)
    for name in sorted(PROPER_NAMES, key=len, reverse=True):
        text = text.replace(name, "")
    return re.sub(r"[^A-Za-z]", "", text)


@pytest.mark.parametrize("locale", ["es-ES", "fr"])
def test_no_english_is_left_in_the_translations(locale):
    from pysepal.i18n.loading import load_locale

    english = load_locale(i18n.MESSAGES_DIR, "en")
    target = load_locale(i18n.MESSAGES_DIR, locale)

    def english_of(key):
        base, _, _ = key.rpartition(".")
        if base in english.plural_keys:
            return english.messages.get(key, english.messages[f"{base}.other"])
        return english.messages.get(key)

    copies = sorted(k for k, text in target.messages.items()
                    if text == english_of(k) and len(_letters(text)) >= 3 and k not in SAME_IN[locale])
    assert not copies, f"{locale} still reads English for: {copies}"


@pytest.mark.parametrize("locale", ["es-ES", "fr"])
def test_the_step_labels_are_translated(locale):
    keys = ["alerts", "layers", "period", "prepare", "integrate", "analysis", "project", "overview"]
    english = {k: i18n.t(f"app.drawer_item.{k}") for k in keys}
    set_locale(locale)
    assert all(i18n.t(f"app.drawer_item.{k}") != english[k] for k in keys)


def test_the_scaffold_namespaces_are_complete_in_every_locale():
    assert not [p for p in i18n.messages.check() if p.key.split(".")[0] in SCAFFOLD_NAMESPACES]


@pytest.mark.parametrize("locale", LOCALES)
def test_no_duplicate_top_level_namespaces(locale):
    """A key defined by two files makes ``catalog()`` refuse the locale."""
    seen = {}
    for f in sorted((i18n.MESSAGES_DIR / locale).glob("*.json")):
        for key in json.loads(Path(f).read_text(encoding="utf-8")):
            assert key not in seen, f"{locale}: namespace '{key}' defined in both {seen[key]} and {f.name}"
            seen[key] = f.name


@pytest.mark.parametrize("locale", LOCALES)
def test_step_one_is_labelled_aoi(locale):
    """Dates moved to the Period step (spec §4.2): step 1 is "AOI"."""
    app = json.loads(Path(i18n.MESSAGES_DIR / locale / "app.json").read_text(encoding="utf-8"))
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
        catalogue.update(json.loads(Path(f).read_text(encoding="utf-8")))
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
    keys = _flat(json.loads(Path(i18n.MESSAGES_DIR / "en" / name).read_text(encoding="utf-8")))
    unused = sorted(keys - _used_keys())
    assert not unused, f"unused keys in en/{name} (delete them in every locale): {unused}"


@pytest.mark.parametrize("locale, whole, part", [
    ("en", "12,345", "0.50"), ("es-ES", "12.345", "0,50"), ("fr", "12 345", "0,50")])
def test_numbers_follow_the_language(locale, whole, part):
    set_locale(locale)
    assert i18n.format_number(12345) == whole
    assert i18n.format_number(0.5, 2) == part


@pytest.mark.parametrize("locale, many, one", [
    ("en", "ready · 12,345 alert pixels", "ready · 1 alert pixel"),
    ("es-ES", "lista · 12.345 píxeles de alerta", "lista · 1 píxel de alerta"),
    ("fr", "prête · 12 345 pixels d'alerte", "prête · 1 pixel d'alerte")])
def test_a_rows_pixel_count_is_counted_in_the_language(locale, many, one):
    from gui.scripts.coverage_ui import result_note

    set_locale(locale)
    assert result_note({"alert_pixels": 12345, "alert_pixels_unmasked": 20000})["text"] == many
    assert result_note({"alert_pixels": 1, "alert_pixels_unmasked": 1})["text"] == one


def test_counted_labels_agree_with_their_count():
    from gui.i18n import size_text

    assert i18n.tn("steps.prepare.run_all", 1) == "Run 1 pending"
    assert i18n.tn("steps.alerts.delete_used", 1).startswith("Used by 1 alert set;")
    assert i18n.tn("steps.integrate.replace_message", 1) == "1 reviewed alert in this set will be discarded."
    assert i18n.tn("steps.sets.alerts", 1) == "1 alert"
    set_locale("es-ES")
    assert i18n.tn("steps.prepare.run_all", 2) == "Ejecutar 2 pendientes"
    assert i18n.tn("steps.sets.alerts", 1_000_000) == "1.000.000 de alertas"
    assert size_text(5_400_000) == "5,4 MB"


@pytest.mark.parametrize("locale, old, button", [
    ("en", "Run all", "Run … pending"), ("es-ES", "Ejecutar todo", "Ejecutar … pendientes"),
    ("fr", "Tout exécuter", "Exécuter … en attente")])
def test_the_prepare_help_names_the_run_button_as_it_reads(locale, old, button):
    set_locale(locale)
    for key in ("info_md", "mask_not_ready", "masks_switched"):
        text = i18n.t(f"steps.prepare.{key}")
        assert old not in text and button in text, key
    first, _, last = i18n.tn("steps.prepare.run_all", 3).partition("3")
    assert button == f"{first}…{last}"


@pytest.mark.parametrize("locale", ["en", "es-ES", "fr"])
def test_the_analysis_copy_names_the_verdict_block_and_not_its_aria_label(locale):
    """Copy that directs the reviewer may only point at what the panel SHOWS.

    ``questionarie.confirmation_q`` is the verdict group's ``aria-label`` and
    nothing else (``gui/tile/analysis/review_panel.py``): a screen reader
    announces it, no pixel carries it. The block's visible heading is
    ``verdict.title``. Copy that names the question sends a sighted reviewer
    scanning the panel for a heading that is not there -- which is where this
    text came from, the pre-redesign panel whose visible label the question
    was.

    Two strings direct the reviewer to that block: the info dialog's
    ``info_md``, and ``confirmation_missing``, the warning a Save with no
    verdict raises (``gui/tile/analysis/export.py``). The warning is the
    sharper of the two -- it fires at the moment the reviewer is hunting for
    the control -- so both are pinned here.
    """
    set_locale(locale)
    question = i18n.t("analysis_tile.questionarie.confirmation_q")
    heading = i18n.t("analysis_tile.verdict.title")

    info = i18n.t("analysis_tile.info_md")
    assert f"**{heading}**" in info, f"{locale}: the help text must name {heading!r}"
    assert question not in info

    refusal = i18n.t("analysis_tile.questionarie.confirmation_missing")
    assert heading in refusal, f"{locale}: the refusal must name {heading!r}"
    assert question not in refusal


def test_every_catalogue_file_keeps_one_layout():
    """One layout (2-space indent, UTF-8 text) so a scripted edit is a no-op diff."""
    from pathlib import Path

    for path in sorted(Path(i18n.MESSAGES_DIR).glob("*/*.json")):
        text = path.read_text(encoding="utf-8")
        assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n", path.name


def test_unused_keys_are_pruned():
    for key in ("common.advanced", "common.load", "common.new", "common.remove", "steps.project.open"):
        assert not i18n.has_message(key), key


def test_the_integrate_and_grid_texts_follow_the_language():
    """The F1-file sites: Integrate's outcome lines and toast, the replace
    dialog's area, and the grid size hint count and separate in the language."""
    from types import SimpleNamespace

    from gui.scripts import integrate_jobs
    from gui.widget import grid_form

    assert integrate_jobs.outcome_lines({"GLAD-L": {"alert_pixels": 1, "alert_pixels_unmasked": 1}}, {}) == [
        "GLAD-L: 1 alert pixel (below the minimum size, outside the drawn area or under earlier sets' footprints)"]
    assert i18n.tn("steps.integrate.done", 1) == "1 alert ready for review"
    set_locale("es-ES")
    assert integrate_jobs.outcome_lines({"RADD": {"alert_pixels": 0, "alert_pixels_unmasked": 12345}}, {}) == [
        "RADD: los 12.345 píxeles de alerta eliminados por máscaras"]
    assert i18n.tn("steps.integrate.done", 2) == "2 alertas listas para revisar"
    grid = SimpleNamespace(width=12345, height=6789)
    original = grid_form.gridmod.estimate_bytes
    grid_form.gridmod.estimate_bytes = lambda g: 2_500_000_000
    try:
        assert grid_form.grid_size_text(grid).startswith("12.345 × 6789 píxeles, unos 2,5 GB")  # CLDR: no 6.789
    finally:
        grid_form.gridmod.estimate_bytes = original


def test_format_number_groups_the_rounded_value():
    """Grouping is decided on what is shown (final re-review, Minor 2):
    999.995 rounds to 1000.00, which groups, and Spanish 9999.6 to 10.000."""
    from pysepal.i18n import set_locale

    from gui.i18n import format_number

    try:
        set_locale("en")
        assert format_number(999.995, 2) == "1,000.00"
        assert format_number(-999.6) == "-1,000"
        assert format_number(999.4) == "999"
        set_locale("es-ES")
        assert format_number(9999.6) == "10.000"
        assert format_number(9999.4) == "9999"
        set_locale("fr")
        assert format_number(999.995, 2) == "1 000,00"
    finally:
        set_locale("en")
