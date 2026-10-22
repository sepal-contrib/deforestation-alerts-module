"""Write the guide's translations as gettext catalogues for docs.sepal.io (a dev tool).

    uvx --with sphinx --with polib python -m tools.docs.make_po [--check]

(from the repository root).

docs.sepal.io builds the guide from ``doc/en.rst`` alone and translates it with Sphinx
gettext: one ``.po`` catalogue per language, whose ``msgid`` is an English paragraph,
heading, list item or table cell, and whose ``msgstr`` is its translation. sepal-doc's
``modules.py`` fetches a module's catalogues from the ``locale`` URL of its ``en.json``
entry, at ``<locale>/<lang>/LC_MESSAGES/en.po`` (``en`` is the stem of the article's URL).

This tool derives those catalogues from the translated articles, which share the English
article's structure (``check_docs`` enforces it): Sphinx extracts the messages of
``doc/en.rst`` and of ``doc/<lang>.rst`` exactly as sepal-doc's gettext build does, and
the n-th English message is paired with the n-th translated one. A translated paragraph
with no English source follows a ``.. translation-only`` comment and is skipped. It writes
``doc/_locale/<lang>/LC_MESSAGES/en.po``; ``--check`` only reports catalogues that are
out of step with the articles. Exits 1 on a structural mismatch, or with ``--check`` on
a stale catalogue.
"""

import sys
import tempfile
from pathlib import Path

import polib
from docutils.nodes import comment
from sphinx.application import Sphinx
from sphinx.util.nodes import extract_messages

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "doc"
LANGS = ("es", "fr", "pt")
# The page docs.sepal.io builds from doc/en.rst (sepal-doc's en.json key).
DOCNAME = "modules/dwn/alert_module"


def _translation_only(node) -> bool:
    """A paragraph right after a ``.. translation-only`` comment has no English source
    (a note that only concerns that language), so it is left out of the pairing."""
    parent = node.parent
    if parent is None:
        return False
    i = parent.index(node)
    return i > 0 and isinstance(parent[i - 1], comment) and parent[i - 1].astext() == "translation-only"


def messages(article: Path) -> list:
    """The (node type, message) pairs Sphinx's gettext builder extracts, in order."""
    found = []

    def _collect(app, doctree):
        found.extend((type(node).__name__, msg) for node, msg in extract_messages(doctree)
                     if not _translation_only(node))

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        page = src / f"{DOCNAME}.rst"
        page.parent.mkdir(parents=True)
        page.write_text(article.read_text(encoding="utf-8"), encoding="utf-8")
        (src / "conf.py").write_text("", encoding="utf-8")
        (src / "index.rst").write_text(f"Index\n=====\n\n.. toctree::\n\n    {DOCNAME}\n", encoding="utf-8")
        app = Sphinx(str(src), str(src), str(Path(tmp) / "out"), str(Path(tmp) / "doctrees"),
                     "gettext", status=None, warning=None, freshenv=True)
        app.connect("doctree-read", lambda app, doctree: _collect(app, doctree)
                    if app.env.docname == DOCNAME else None)
        app.build()
    return found


def catalogue(lang: str, english: list) -> tuple:
    """The ``.po`` catalogue for ``lang`` and its problems (an empty list when sound)."""
    translated = messages(DOC / f"{lang}.rst")
    if [kind for kind, _ in english] != [kind for kind, _ in translated]:
        n = next((i for i, (a, b) in enumerate(zip(english, translated)) if a[0] != b[0]),
                 min(len(english), len(translated)))
        here = translated[n][1][:60] if n < len(translated) else "the end"
        return None, [f"{lang}.rst: message {n + 1} ({here!r}) does not line up with en.rst"]
    po = polib.POFile(wrapwidth=0)
    po.metadata = {
        "Project-Id-Version": "deforestation-alerts-module",
        "Language": lang,
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
    }
    problems, seen = [], {}
    for (_, msgid), (_, msgstr) in zip(english, translated):
        if msgid in seen:
            if seen[msgid] != msgstr:
                problems.append(f"{lang}.rst: {msgid[:60]!r} is translated two ways")
            continue
        seen[msgid] = msgstr
        po.append(polib.POEntry(msgid=msgid, msgstr=msgstr))
    return po, problems


def target(lang: str) -> Path:
    return DOC / "_locale" / lang / "LC_MESSAGES" / "en.po"


def main(argv: list) -> int:
    check_only = "--check" in argv
    english = messages(DOC / "en.rst")
    status = 0
    for lang in LANGS:
        po, problems = catalogue(lang, english)
        for problem in problems:
            print(problem)
        if po is None:
            status = 1
            continue
        path = target(lang)
        text = str(po) + "\n"
        if check_only:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"{path.relative_to(REPO)} is out of step with {lang}.rst")
                status = 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"{path.relative_to(REPO)}: {len(po)} messages")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
