"""The user-guide checker (tools/docs/check_docs.py) on small doc trees, and on the real doc/."""

import json
import re
from pathlib import Path

import pytest

from tools.docs.check_docs import LANGS, RAW_BASE, check

REPO = Path(__file__).resolve().parents[1]

TITLE = "Title\n=====\n\n"


def _tree(root: Path, body: str = TITLE) -> Path:
    for lang in LANGS:
        (root / lang / "img").mkdir(parents=True)
        (root / f"{lang}.rst").write_text(body, encoding="utf-8")
    (root / "anim").mkdir()
    return root


def _figure(path: str) -> str:
    return f".. figure:: {RAW_BASE}{path}\n    :alt: a picture\n\n"


def test_clean_tree_has_no_problems(tmp_path):
    assert check(_tree(tmp_path)) == []


def test_broken_link_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + "See `x <defo-missing_>`_.\n")
    problems = check(root, images=False)
    assert len(problems) == 1
    assert problems[0].startswith("en.rst") and "defo-missing" in problems[0]


def test_defined_target_and_external_links_pass(tmp_path):
    root = _tree(tmp_path)
    body = TITLE + ".. _defo-a:\n\nA\n-\n\nSee `a <defo-a_>`_ and `SEPAL <https://sepal.io>`__.\n"
    for lang in LANGS:
        (root / f"{lang}.rst").write_text(body)
    assert check(root) == []


def test_missing_image_reported_unless_no_images(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + _figure("en/img/aoi-a.png"))
    assert any("broken image en/img/aoi-a.png" in p for p in check(root))
    assert not any("broken" in p for p in check(root, images=False))


def test_image_outside_the_raw_base_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + ".. figure:: img/aoi-a.png\n\n")
    problems = check(root, images=False)
    assert any("is not under" in p for p in problems)


def test_language_missing_article_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "fr.rst").unlink()
    problems = check(root)
    assert problems == ["fr.rst: article is missing"]


def _with_image(root, lang, name):
    (root / lang / "img" / name).write_bytes(b"x")
    (root / f"{lang}.rst").write_text(TITLE + _figure(f"{lang}/img/{name}"))


def test_translation_image_ids_differ_from_en_reported(tmp_path):
    root = _tree(tmp_path)
    for lang in LANGS:
        _with_image(root, lang, "aoi-a.png")
    assert check(root) == []
    _with_image(root, "es", "aoi-b.png")
    problems = check(root)
    assert len(problems) == 1 and problems[0].startswith("es.rst") and "aoi-" in problems[0]


def test_translation_showing_the_english_screenshot_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "en" / "img" / "a.png").write_bytes(b"x")
    for lang in LANGS:
        (root / f"{lang}.rst").write_text(TITLE + _figure("en/img/a.png"))
    problems = check(root)
    assert any(p.startswith("pt.rst: shows another language's image") for p in problems)


def test_translation_headings_differ_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "pt.rst").write_text(TITLE + "More\n----\n")
    problems = check(root)
    assert len(problems) == 1 and problems[0].startswith("pt.rst") and "headings" in problems[0]


def test_translation_targets_differ_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "fr.rst").write_text(TITLE + ".. _defo-x:\n\nX\n-\n")
    (root / "en.rst").write_text(TITLE + ".. _defo-y:\n\nY\n-\n")
    problems = check(root, langs=("en", "fr"))
    assert any(p.startswith("fr.rst: targets differ") for p in problems)


def test_short_underline_and_heading_order_reported(tmp_path):
    root = _tree(tmp_path)
    for lang in LANGS:
        (root / f"{lang}.rst").write_text("Long title\n====\n\nSub\n^^^\n")
    problems = check(root, langs=("en",))
    assert any("shorter than its title" in p for p in problems)
    assert any("do not follow" in p for p in problems)


def test_markdown_leftovers_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + "See [x](y.md).\n\n> [!NOTE]\n> z\n")
    problems = check(root, langs=("en",))
    assert sum("Markdown syntax" in p for p in problems) == 2


def test_oversized_gif_and_png_reported(tmp_path):
    root = _tree(tmp_path)
    (root / "anim" / "big.gif").write_bytes(b"0" * (2 * 1024 * 1024))
    (root / "anim" / "ok.gif").write_bytes(b"0" * 1024)
    (root / "en" / "img" / "big.png").write_bytes(b"0" * (600 * 1024))
    (root / "en" / "img" / "ok.png").write_bytes(b"0" * 1024)
    problems = check(root, images=False)
    assert len(problems) == 2
    assert any("big.gif" in p for p in problems) and any("big.png" in p for p in problems)


def test_unknown_bold_label_reported_with_labels(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + "Select **Definitely Not A Label** now.\n")
    assert check(root) == []  # labels off by default
    problems = check(root, labels=True)
    assert len(problems) == 1 and "Definitely Not A Label" in problems[0]


def test_known_label_and_whitelist_pass_with_labels(tmp_path):
    root = _tree(tmp_path)
    catalogue = REPO / "deforestation_alerts" / "message" / "en"
    value = next(
        v
        for f in catalogue.glob("*.json")
        for v in _strings(json.loads(f.read_text(encoding="utf-8")))
        if "{" not in v and "*" not in v and "`" not in v and "\n" not in v and 3 < len(v) < 30
    )
    (root / "en.rst").write_text(TITLE + f"**{value}** and **free text**\n\n.. labels-ok: free text; other\n")
    assert check(root, labels=True) == []


def test_labels_use_the_article_language_catalogue(tmp_path):
    root = _tree(tmp_path)
    messages = REPO / "deforestation_alerts" / "message"
    en = {_norm(v) for f in (messages / "en").glob("*.json") for v in _strings(json.loads(f.read_text("utf-8")))}
    es = {_norm(v) for f in (messages / "es-ES").glob("*.json") for v in _strings(json.loads(f.read_text("utf-8")))}
    value = next(v for v in sorted(en - es) if "{" not in v and "*" not in v and "`" not in v and 3 < len(v) < 40)
    (root / "en.rst").write_text(TITLE + f"**{value}**\n")
    (root / "es.rst").write_text(TITLE + f"**{value}**\n")
    problems = check(root, labels=True)
    assert len(problems) == 1
    assert problems[0].startswith("es.rst")


def _norm(text):
    return " ".join(text.split())


def test_placeholders_in_catalogue_values_are_wildcards(tmp_path):
    root = _tree(tmp_path)
    catalogue = REPO / "deforestation_alerts" / "message" / "en"
    value = next(
        v
        for f in catalogue.glob("*.json")
        for v in _strings(json.loads(f.read_text(encoding="utf-8")))
        if not v.startswith("{") and "{" in v and "*" not in v and "`" not in v and "\n" not in v
    )
    shown = re.sub(r"\{[^}]*\}", "12", value)
    (root / "en.rst").write_text(TITLE + f"**{shown}**\n")
    assert check(root, labels=True) == []


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def test_repo_doc_tree():
    assert check(REPO / "doc", images=True, labels=True) == []


def test_repo_doc_tree_parses_without_docutils_warnings():
    pytest.importorskip("docutils")
    assert [p for p in check(REPO / "doc", images=False) if p.startswith("docutils")] == []


def test_docutils_warnings_reported_when_installed(tmp_path):
    pytest.importorskip("docutils")
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + ".. figure::\n\n")
    assert any(p.startswith("docutils") for p in check(root, images=False, langs=("en",)))


def test_langs_limits_checked_languages(tmp_path):
    root = _tree(tmp_path)
    (root / "en.rst").write_text(TITLE + "New\n---\n\n`x <defo-missing_>`_\n")
    (root / "fr.rst").write_text(TITLE + "`y <defo-missing_>`_\n")
    en_only = check(root, langs=("en",))
    assert en_only == ["en.rst: broken link defo-missing_"]
    es_only = check(root, langs=("es",))
    assert es_only == ["es.rst: headings '=', en has '=-'"]  # en still read as the reference
    assert not any(p.startswith(("fr.", "pt.")) for p in check(root, langs=("en", "es")))
    assert any(p.startswith("fr.") for p in check(root))


def test_langs_cli_argument(tmp_path, monkeypatch, capsys):
    from tools.docs import check_docs

    seen = {}
    monkeypatch.setattr(check_docs, "check", lambda root, **kw: seen.update(kw) or [])
    assert check_docs.main(["--no-images", "--langs", "en,es"]) == 0
    assert seen["langs"] == ["en", "es"] and seen["images"] is False
    assert check_docs.main(["--langs=fr"]) == 0
    assert seen["langs"] == ["fr"]
    assert check_docs.main([]) == 0
    assert seen["langs"] is None


def test_anim_reference(tmp_path):
    root = _tree(tmp_path)
    for lang in LANGS:
        (root / f"{lang}.rst").write_text(TITLE + _figure("anim/workflow.gif"))
    assert any("workflow.gif" in p for p in check(root))
    assert check(root, images=False) == []
    (root / "anim" / "workflow.gif").write_bytes(b"x")
    assert check(root) == []
    (root / "fr.rst").write_text(TITLE)
    problems = check(root)
    assert len(problems) == 1 and problems[0].startswith("fr.rst")


def test_po_catalogues_in_step_with_articles():
    """doc/_locale/<lang>/LC_MESSAGES/en.po (docs.sepal.io's translations) match the articles;
    regenerate them with ``python -m tools.docs.make_po``."""
    pytest.importorskip("sphinx")
    pytest.importorskip("polib")
    from tools.docs import make_po

    assert make_po.main(["--check"]) == 0


def test_source_commit_names_an_ancestor_commit():
    """doc/SOURCE_COMMIT is the main commit the guide describes (see tools/docs/README.md)."""
    import subprocess

    sha = (REPO / "doc" / "SOURCE_COMMIT").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
    try:
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=REPO,
                                  capture_output=True).returncode
    except FileNotFoundError:  # no git here
        return
    assert ancestor in (0, 128), f"{sha} is not in this branch's history"  # 128: not a git checkout
