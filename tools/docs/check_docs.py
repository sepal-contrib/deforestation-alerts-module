"""Check the user guide under doc/ (a dev tool; also run by tests/test_docs.py).

    python -m tools.docs.check_docs [--no-images] [--labels] [--langs en,es]

The guide is one reStructuredText article per language, ``doc/<en|es|fr|pt>.rst``,
written in SEPALSTYLE (https://docs.sepal.io/en/latest/team/style_guide.html). SEPAL's
documentation build copies ``doc/en.rst`` alone, so its images are absolute
:data:`RAW_BASE` URLs. Reports, one line per problem:

* an article missing from a language;
* a ``figure``/``image`` URL outside :data:`RAW_BASE`, or whose file is missing under
  ``doc/`` (the file check is skipped with ``--no-images``), or a translation showing
  another language's screenshot;
* a reference ```text <name_>`_`` to a target ``.. _name:`` the article does not define;
* a heading whose underline is shorter than its title, or heading symbols out of the
  SEPALSTYLE order ``= - ^ " # +``;
* Markdown left in an article (``](``, ``> [!``, a fence or a ``#`` heading);
* a translation whose headings, targets or images differ from the English article;
* a GIF under doc/anim of 2 MB or more, or a PNG of 600 KB or more;
* with ``--labels``: bold text (``**...**``) that is not a message-catalogue value of the
  article's language. ``{placeholders}`` in catalogue values match any text. Bold text
  that is not a label goes in a comment ``.. labels-ok: text one; text two``;
* when docutils is installed: every warning docutils reports while parsing an article.

``--langs`` (``langs=`` in :func:`check`) limits the check to those languages; the drift
checks then run only for the translations included (en is still read as their reference).

Exits 1 when there are problems.
"""

import io
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
MESSAGES = REPO / "deforestation_alerts" / "message"

LANGS = {"en": "en", "es": "es-ES", "fr": "fr", "pt": "pt-BR"}
RAW_BASE = "https://raw.githubusercontent.com/sepal-contrib/deforestation-alerts-module/release/doc/"
HEADING_ORDER = '=-^"#+'

MAX_GIF = 2 * 1024 * 1024
MAX_PNG = 600 * 1024

_IMAGE = re.compile(r"^\s*\.\. (?:figure|image|thumbnail):: *(\S+)", re.MULTILINE)
_TARGET = re.compile(r"^\.\. _([A-Za-z0-9-]+):\s*$", re.MULTILINE)
_REF = re.compile(r"`[^`<]*<([A-Za-z0-9-]+)_>`_")
_UNDERLINE = re.compile(r"^([=\-^\"#+])\1+$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_OK = re.compile(r"^\.\. labels-ok:(.*)$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")
_MARKDOWN = re.compile(r"\]\(|^> \[!|^```|^#{1,6} ", re.MULTILINE)
_UNKNOWN_TARGET = re.compile(r"Unknown target name|refers to target .* which does not exist")


def article(root: Path, lang: str) -> Path:
    return Path(root) / f"{lang}.rst"


def _headings(text: str) -> list[str]:
    """The underline symbol of each heading, in order."""
    lines = text.splitlines()
    out = []
    for i in range(1, len(lines)):
        prev = lines[i - 1].strip()
        if _UNDERLINE.match(lines[i]) and prev and not _UNDERLINE.match(prev) and not prev.startswith(".."):
            out.append(lines[i][0])
    return out


def _heading_problems(rel: str, text: str) -> list[str]:
    problems = []
    lines = text.splitlines()
    for i in range(1, len(lines)):
        prev = lines[i - 1]
        if _UNDERLINE.match(lines[i]) and prev.strip() and not _UNDERLINE.match(prev.strip()):
            if not prev.startswith(".."):
                if len(lines[i]) < len(prev.rstrip()):
                    problems.append(f"{rel}:{i + 1}: heading underline shorter than its title: {prev.strip()!r}")
    used = list(dict.fromkeys(_headings(text)))
    if used != list(HEADING_ORDER[: len(used)]):
        problems.append(f"{rel}: heading symbols {''.join(used)!r} do not follow {HEADING_ORDER!r}")
    return problems


def _images(text: str) -> list[str]:
    return _IMAGE.findall(text)


def _img_names(text: str, lang: str) -> set[str]:
    """The article's images relative to doc/, with its own language folder written ``<lang>/``."""
    names = set()
    for url in _images(text):
        if url.startswith(RAW_BASE):
            rest = url[len(RAW_BASE):]
            if rest.startswith(f"{lang}/"):
                rest = "<lang>/" + rest[len(lang) + 1:]
            names.add(rest)
    return names


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _norm(text: str) -> str:
    return " ".join(text.replace("**", "").replace("`", "").split())


def _catalogue(locale: str):
    """``(exact values, wildcard patterns)`` of one locale's catalogue."""
    exact, patterns = set(), []
    for path in sorted((MESSAGES / locale).glob("*.json")):
        for value in _strings(json.loads(path.read_text(encoding="utf-8"))):
            value = _norm(value)
            if _PLACEHOLDER.search(value):
                parts = [re.escape(p) for p in _PLACEHOLDER.split(value)]
                patterns.append(re.compile(".+?".join(parts)))
            else:
                exact.add(value)
    return exact, patterns


def _is_label(text: str, catalogue) -> bool:
    exact, patterns = catalogue
    text = _norm(text)
    return text in exact or any(p.fullmatch(text) for p in patterns)


def _check_labels(rel: str, text: str, catalogue) -> list[str]:
    allowed = {
        _norm(item)
        for block in _OK.findall(text)
        for item in block.split(";")
        if item.strip()
    }
    body = _OK.sub("", text)
    problems = []
    for span in _BOLD.findall(body):
        if _norm(span) not in allowed and not _is_label(span, catalogue):
            problems.append(f"{rel}: bold text is not a catalogue label: {span!r}")
    return problems


def _docutils_problems(rel: str, text: str) -> list[str]:
    """Warnings docutils reports for the article ([] when docutils is not installed).

    Unknown link targets are left out: ``check`` reports them as broken links."""
    try:
        from docutils.core import publish_doctree
    except ImportError:
        return []
    stream = io.StringIO()
    publish_doctree(
        text,
        source_path=rel,
        settings_overrides={"report_level": 2, "halt_level": 5, "warning_stream": stream},
    )
    return [f"docutils: {line}" for line in stream.getvalue().splitlines()
            if line.strip() and not _UNKNOWN_TARGET.search(line)]


def check(
    root: Path,
    images: bool = True,
    labels: bool = False,
    langs: Optional[Iterable[str]] = None,
) -> list[str]:
    """Problems found in the doc tree at ``root`` (empty list = OK).

    ``langs``: the languages to check (None = all of ``LANGS``)."""
    root = Path(root)
    selected = set(LANGS) if langs is None else set(langs)
    unknown = selected - set(LANGS)
    if unknown:
        raise ValueError(f"unknown languages: {', '.join(sorted(unknown))}")
    problems: list[str] = []
    texts: dict[str, str] = {}

    for lang, locale in LANGS.items():
        if lang not in selected:
            continue
        path = article(root, lang)
        rel = path.name
        if not path.is_file():
            problems.append(f"{rel}: article is missing")
            continue
        text = path.read_text(encoding="utf-8")
        texts[lang] = text
        for url in _images(text):
            if not url.startswith(RAW_BASE):
                problems.append(f"{rel}: image is not under {RAW_BASE}: {url}")
                continue
            rest = url[len(RAW_BASE):]
            folder = rest.split("/", 1)[0]
            if folder in LANGS and folder != lang:
                problems.append(f"{rel}: shows another language's image: {rest}")
            if images and not (root / rest).is_file():
                problems.append(f"{rel}: broken image {rest}")
        targets = set(_TARGET.findall(text))
        for name in sorted(set(_REF.findall(text)) - targets):
            problems.append(f"{rel}: broken link {name}_")
        problems.extend(_heading_problems(rel, text))
        for match in _MARKDOWN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{rel}:{line}: Markdown syntax: {match.group(0)!r}")
        if labels:
            problems.extend(_check_labels(rel, text, _catalogue(locale)))
        problems.extend(_docutils_problems(rel, text))

    en = texts.get("en")
    if en is None and article(root, "en").is_file():
        en = article(root, "en").read_text(encoding="utf-8")
    for lang in LANGS:
        tr = texts.get(lang)
        if lang == "en" or tr is None or en is None:
            continue
        rel = f"{lang}.rst"
        if _headings(en) != _headings(tr):
            problems.append(f"{rel}: headings {''.join(_headings(tr))!r}, en has {''.join(_headings(en))!r}")
        if set(_TARGET.findall(en)) != set(_TARGET.findall(tr)):
            diff = sorted(set(_TARGET.findall(en)) ^ set(_TARGET.findall(tr)))
            problems.append(f"{rel}: targets differ from en: {', '.join(diff)}")
        if _img_names(en, "en") != _img_names(tr, lang):
            diff = sorted(_img_names(en, "en") ^ _img_names(tr, lang))
            problems.append(f"{rel}: images or animations differ from en: {', '.join(diff)}")

    for gif in sorted((root / "anim").glob("*.gif")):
        if gif.stat().st_size >= MAX_GIF:
            problems.append(f"anim/{gif.name}: GIF is {gif.stat().st_size} bytes (limit 2 MB)")
    for png in sorted(root.glob("*/img/*.png")):
        if png.parent.parent.name not in selected:
            continue
        if png.stat().st_size >= MAX_PNG:
            problems.append(
                f"{png.parent.parent.name}/img/{png.name}: PNG is {png.stat().st_size} bytes "
                "(limit 600 KB)"
            )
    return problems


def _langs_arg(argv: list[str]) -> Optional[list[str]]:
    for i, arg in enumerate(argv):
        if arg.startswith("--langs="):
            return [x for x in arg.split("=", 1)[1].split(",") if x]
        if arg == "--langs" and i + 1 < len(argv):
            return [x for x in argv[i + 1].split(",") if x]
    return None


def main(argv: list[str]) -> int:
    problems = check(
        REPO / "doc",
        images="--no-images" not in argv,
        labels="--labels" in argv,
        langs=_langs_arg(argv),
    )
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
