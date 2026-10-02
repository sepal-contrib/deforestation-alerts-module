"""One-shot migration off the legacy ``cm`` Translator (plan Task 42).

Every attribute chain ``cm.<a>.<b>...`` in ``gui/``, ``deforestation_alerts/`` and ``tests/``
becomes ``t("<a>.<b>...")`` (single quotes inside an f-string, which Python
3.11 cannot nest), ``str(t("..."))`` collapses to ``t("...")``, and the
``from deforestation_alerts.message import cm`` import becomes
``from gui.i18n import t`` (merged into an existing ``gui.i18n`` import).
Prints the lines it cannot finish by itself: ``getattr(cm.<ns>, ...)``,
``t("...").format(...)`` (pass the values to ``t`` instead), a chain that names a
group of messages rather than one, and a ``cm`` read at import time.

    python -m tools.i18n.retire_cm            # rewrite in place
    python -m tools.i18n.retire_cm --check    # only report
"""

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = (ROOT / "gui", ROOT / "deforestation_alerts", ROOT / "tests")
ENGLISH = ROOT / "deforestation_alerts" / "message" / "en"
CHAIN_RE = re.compile(r"\bcm((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
IMPORT_RE = re.compile(r"^from deforestation_alerts\.message import cm\n", re.M)
GUI_IMPORT_RE = re.compile(r"^from gui\.i18n import ([^\n]+)\n", re.M)


def _string_spans(src: str) -> list[tuple[int, int]]:
    """Character spans of the string tokens (f-strings included) of ``src``."""
    lines = src.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    spans = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            spans.append((starts[tok.start[0] - 1] + tok.start[1], starts[tok.end[0] - 1] + tok.end[1]))
    return spans


def _import_time_lines(src: str) -> list[int]:
    """Lines where ``cm.`` is read outside any function: that runs at import,
    before any locale is known; such a table must hold keys, translated at use."""
    tree = ast.parse(src)
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inside |= {id(n) for n in ast.walk(node)}
    return sorted({n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and n.id == "cm" and id(n) not in inside})


def _is_message(key: str) -> bool:
    """Whether ``key`` names a message of the English catalogue (not a group)."""
    node = {}
    for f in sorted(ENGLISH.glob("*.json")):
        node.update(json.loads(f.read_text(encoding="utf-8")))
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def rewrite(src: str) -> tuple[str, int, list]:
    todo = [f"line {ln}: read at import time; keep the key, call t() where it is used"
            for ln in _import_time_lines(src)]
    spans = _string_spans(src)
    out, last, n = [], 0, 0
    for m in CHAIN_RE.finditer(src):
        inside = next(((a, b) for a, b in spans if a <= m.start() < b), None)
        if inside is not None and not src[inside[0]:inside[1]].lstrip("rbuRBU").startswith(("f", "F")):
            continue  # a plain string mentioning cm.x (a docstring): leave it
        quote = "'" if inside is not None else '"'
        key = m.group(1)[1:]
        if src[max(0, m.start() - 8):m.start()] == "getattr(":  # getattr(cm.ns, ...): finish by hand
            todo.append(f"getattr(cm.{key}, ...)")
        elif not _is_message(key):
            todo.append(f"t({key!r}) names a group of messages, not one: name each message where it is used")
        out.append(src[last:m.start()] + f"t({quote}{key}{quote})")
        last, n = m.end(), n + 1
    src = "".join(out) + src[last:]
    src = re.sub(r"str\((t\((['\"])[^'\"]+\2\))\)", r"\1", src)
    if n and IMPORT_RE.search(src):
        src = IMPORT_RE.sub("", src, count=1)
        gui = GUI_IMPORT_RE.search(src)
        if gui:
            names = sorted({x.strip() for x in gui.group(1).split(",")} | {"t"})
            src = src[:gui.start()] + f"from gui.i18n import {', '.join(names)}\n" + src[gui.end():]
        else:
            src = _add_import(src, "from gui.i18n import t\n")
    for m in re.finditer(r"t\((['\"])[^'\"]+\1\)\.format\(", src):
        todo.append(src[m.start():m.end()])
    return src, n, todo


def _add_import(src: str, line: str) -> str:
    """After the last top-level ``import``/``from`` line of the first import block."""
    lines = src.splitlines(keepends=True)
    last = max((i for i, ln in enumerate(lines) if ln.startswith(("import ", "from "))), default=-1)
    lines.insert(last + 1, line)
    return "".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    total = 0
    for base in CODE:
        for py in sorted(base.rglob("*.py")):
            if py.parent.name == "message" and py.name == "__init__.py":
                continue
            src = py.read_text(encoding="utf-8")
            new, n, todo = rewrite(src)
            if n:
                total += n
                print(f"{py.relative_to(ROOT)}: {n} cm reference(s)")
                for item in todo:
                    print(f"BY HAND: {py.relative_to(ROOT)}: {item}")
                if not args.check:
                    py.write_text(new, encoding="utf-8")
    print(f"{total} cm reference(s) rewritten")
    return 1 if args.check and total else 0


if __name__ == "__main__":
    sys.exit(main())
