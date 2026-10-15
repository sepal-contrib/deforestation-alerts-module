"""Keep the es-ES, fr and pt-BR catalogues in step with English (plan Tasks 42 and 44).

    python -m tools.i18n.locales prune                    # drop what check() calls extra
    python -m tools.i18n.locales missing fr /tmp/fr.json  # flat {key: English} still to translate
    python -m tools.i18n.locales merge fr /tmp/fr.json    # write translated keys into fr/*.json

``missing`` lists every ``missing_key`` problem of ``messages.check()`` for the
locale, with the English text to translate (for a plural form English lacks,
such as French ``many``, the English ``other``). ``merge`` writes each key into
the locale file named like the English file that defines it, creating the file
when needed, in English's key order.
"""

import argparse
import json
import sys
from pathlib import Path

from pysepal.i18n.loading import ENGLISH, load_locale

ROOT = Path(__file__).resolve().parents[2]
MESSAGES = ROOT / "deforestation_alerts" / "message"


def _flat(node: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in node.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flat(value, dotted))
        else:
            out[dotted] = value
    return out


def _nest(flat: dict) -> dict:
    out: dict = {}
    for dotted, value in flat.items():
        node = out
        *parents, leaf = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return out


def _problems(locale: str):
    from gui.i18n import messages  # bound to MESSAGES

    return [p for p in messages.check() if p.locale == locale or locale == "*"]


def _english_files() -> dict:
    """{English file name: its flat keys, in file order}."""
    return {f.name: _flat(json.loads(f.read_text(encoding="utf-8")))
            for f in sorted((MESSAGES / ENGLISH).glob("*.json"))}


def _file_of(key: str, english: dict) -> str:
    base = key.rpartition(".")[0]
    for name, keys in english.items():
        if key in keys or f"{base}.other" in keys or f"{key}.other" in keys:
            return name
    raise KeyError(key)


def _write(locale: str, name: str, flat: dict, english_order: list) -> None:
    rank = {k: i for i, k in enumerate(english_order)}

    def order(k):
        base, _, cat = k.rpartition(".")
        return (rank.get(k, rank.get(f"{base}.other", len(rank))), cat)

    path = MESSAGES / locale / name
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: flat[k] for k in sorted(flat, key=order)}
    path.write_text(json.dumps(_nest(ordered), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prune() -> int:
    drop = {(p.locale, p.key) for p in _problems("*") if p.code in ("extra_key", "unsupported_plural_category")}
    english = _english_files()
    for locale in sorted({loc for loc, _ in drop}):
        for path in sorted((MESSAGES / locale).glob("*.json")):
            flat = _flat(json.loads(path.read_text(encoding="utf-8")))
            kept = {k: v for k, v in flat.items() if (locale, k) not in drop}
            if kept != flat:
                order = english.get(path.name, {})
                _write(locale, path.name, kept, list(order))
                print(f"{path.relative_to(ROOT)}: dropped {len(flat) - len(kept)}")
    return len(drop)


def missing(locale: str, out: Path) -> int:
    english = load_locale(MESSAGES, ENGLISH)
    todo = {}
    for p in _problems(locale):
        if p.code != "missing_key":
            continue
        base, _, cat = p.key.rpartition(".")
        text = english.messages.get(p.key)
        if text is None and base in english.plural_keys:
            text = english.messages[f"{base}.other"]
        todo[p.key] = text
    out.write_text(json.dumps(todo, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(todo)} key(s) to translate into {locale}: {out}")
    return len(todo)


def merge(locale: str, src: Path) -> int:
    new = json.loads(src.read_text(encoding="utf-8"))
    english = _english_files()
    by_file: dict = {}
    for key, text in new.items():
        by_file.setdefault(_file_of(key, english), {})[key] = text
    for name, keys in by_file.items():
        path = MESSAGES / locale / name
        flat = _flat(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
        flat.update(keys)
        _write(locale, name, flat, list(english[name]))
        print(f"{path.relative_to(ROOT)}: {len(keys)} key(s)")
    return len(new)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prune")
    m = sub.add_parser("missing")
    m.add_argument("locale")
    m.add_argument("out", type=Path)
    g = sub.add_parser("merge")
    g.add_argument("locale")
    g.add_argument("src", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "prune":
        prune()
    elif args.cmd == "missing":
        missing(args.locale, args.out)
    else:
        merge(args.locale, args.src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
