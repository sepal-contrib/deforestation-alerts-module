"""One-shot catalogue migration to pysepal 4's plural nodes (plan Task 42).

1. In every ``deforestation_alerts/message/<locale>/*.json``, a pair of sibling
   leaves ``<x>_one`` / ``<x>_other`` becomes the plural node
   ``<x>: {"one": ..., "other": ...}`` (plus ``"many"``, a copy of ``other``, in a
   locale whose cardinal rules have it: es-ES and fr). ``{n}`` becomes ``{count}``.
2. In ``gui/``, ``deforestation_alerts/`` and ``tests/``, every
   ``plural(<n>, "<k>_one", "<k>_other", **kw)`` call becomes
   ``t("<k>", count=<n>, **kw)``, and ``plural`` leaves the ``gui.i18n`` import
   when it is no longer used.
3. Prints every place it did not rewrite: a ``plural(...)`` call whose keys are
   not two literals of one base, and every other string literal or f-string
   that ends in ``_one`` / ``_other`` and names a catalogue namespace. Fix those
   by hand (``t("<k>", count=n)``).

Idempotent: a second run changes nothing.

    python -m tools.i18n.plural_nodes            # rewrite in place
    python -m tools.i18n.plural_nodes --check    # only report; exit 1 if anything would change
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path

from pysepal.i18n.plurals import plural_categories

ROOT = Path(__file__).resolve().parents[2]
MESSAGES = ROOT / "deforestation_alerts" / "message"
CODE = (ROOT / "gui", ROOT / "deforestation_alerts", ROOT / "tests")
SUFFIX_RE = re.compile(r"^[a-z_]+(\.[A-Za-z0-9_]+)+_(one|other)$")


def _nodes(node: dict, categories: frozenset) -> tuple[dict, int]:
    """``node`` with its ``_one``/``_other`` pairs merged; returns (new, merged)."""
    out, merged = {}, 0
    for key, value in node.items():
        if isinstance(value, dict):
            out[key], n = _nodes(value, categories)
            merged += n
            continue
        if key.endswith("_other") and f"{key[:-6]}_one" in node:
            continue  # emitted with its _one sibling
        if key.endswith("_one") and isinstance(node.get(f"{key[:-4]}_other"), str):
            base = key[:-4]
            if base in node:
                raise SystemExit(f"{base!r} exists beside {key!r}: merge it by hand")
            one = value.replace("{n}", "{count}")
            other = node[f"{base}_other"].replace("{n}", "{count}")
            out[base] = {"one": one, **({"many": other} if "many" in categories else {}), "other": other}
            merged += 1
            continue
        out[key] = value
    return out, merged


def convert_catalogue(write: bool) -> int:
    changed = 0
    for folder in sorted(p for p in MESSAGES.iterdir() if p.is_dir() and any(p.glob("*.json"))):
        categories = plural_categories(folder.name)
        for path in sorted(folder.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            new, merged = _nodes(doc, categories)
            if merged:
                changed += merged
                print(f"{path.relative_to(ROOT)}: {merged} plural node(s)")
                if write:
                    path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def _plural_base(call: ast.Call):
    """The common base of a ``plural(n, "<b>_one", "<b>_other", ...)`` call, or None."""
    if len(call.args) != 3 or not all(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                      for a in call.args[1:]):
        return None
    one, other = call.args[1].value, call.args[2].value
    if one.endswith("_one") and other.endswith("_other") and one[:-4] == other[:-6]:
        return one[:-4]
    return None


def _offset(lines, lineno, col):
    return sum(len(line) for line in lines[: lineno - 1]) + col


def convert_code(write: bool) -> tuple[int, list]:
    changed, leftovers = 0, []
    for base in CODE:
        for py in sorted(base.rglob("*.py")):
            src = py.read_text(encoding="utf-8")
            tree = ast.parse(src)
            lines = src.splitlines(keepends=True)
            edits = []
            for node in ast.walk(tree):
                where = f"{py.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "plural":
                    key = _plural_base(node)
                    if key is None:
                        leftovers.append(f"{where} {ast.get_source_segment(src, node)}")
                        continue
                    count = ast.get_source_segment(src, node.args[0])
                    rest = [ast.get_source_segment(src, k) for k in node.keywords]
                    text = f't("{key}", count={count}' + "".join(f", {r}" for r in rest) + ")"
                    start = _offset(lines, node.lineno, node.col_offset)
                    end = _offset(lines, node.end_lineno, node.end_col_offset)
                    edits.append((start, end, text))
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SUFFIX_RE.match(node.value):
                    parent_is_plural = any(
                        isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id == "plural"
                        and node in p.args for p in ast.walk(tree))
                    if not parent_is_plural:
                        leftovers.append(f"{where} {node.value!r}")
                elif isinstance(node, ast.JoinedStr):
                    tail = node.values[-1] if node.values else None
                    if isinstance(tail, ast.Constant) and re.search(r"_(one|other)$", tail.value):
                        leftovers.append(f"{where} {ast.unparse(node)}")
            if not edits:
                continue
            for start, end, text in sorted(edits, reverse=True):
                src = src[:start] + text + src[end:]
            if "plural(" not in src.replace("def plural(", ""):
                src = re.sub(r"from gui\.i18n import ([^\n]*)", lambda m: _drop_plural(m.group(0)), src)
            changed += len(edits)
            print(f"{py.relative_to(ROOT)}: {len(edits)} plural() call(s)")
            if write:
                py.write_text(src, encoding="utf-8")
    return changed, leftovers


def _drop_plural(line: str) -> str:
    names = [n.strip() for n in line.split("import", 1)[1].split(",")]
    names = [n for n in names if n and n != "plural"]
    if "t" not in names:
        names.append("t")
    return "from gui.i18n import " + ", ".join(sorted(names))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    n_json = convert_catalogue(write=not args.check)
    n_code, leftovers = convert_code(write=not args.check)
    for line in leftovers:
        print("BY HAND:", line)
    print(f"{n_json} plural node(s), {n_code} call(s) rewritten, {len(leftovers)} left by hand")
    return 1 if args.check and (n_json or n_code) else 0


if __name__ == "__main__":
    sys.exit(main())
