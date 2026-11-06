"""Put or drop message keys in en, es-ES, fr and pt-BR at once (a dev tool for plan tasks).

    from tools.i18n.edit import edit
    edit("steps", put=[("steps.alerts.mode_label", "Source type", "Tipo de fuente", "Type de source",
                         "Tipo de fonte")],
         drop=["steps.alerts.col_kind"])

A dotted key starts at the file's root (``steps.json`` holds ``{"steps": ...}``,
``widgets.json`` holds ``{"common", "dialog", "widgets", "workflow"}``). A dropped
key that holds a dict goes with its subtree; a key that is not there is skipped.
Files are written as the catalogues are kept: 2-space indent, UTF-8, newline at
the end.
"""

import json
from pathlib import Path

MESSAGES = Path(__file__).resolve().parents[2] / "deforestation_alerts" / "message"
LOCALES = ("en", "es-ES", "fr", "pt-BR")


def _parent(tree: dict, dotted: str, create: bool):
    *parents, leaf = dotted.split(".")
    node = tree
    for part in parents:
        if part not in node:
            if not create:
                return None, leaf
            node[part] = {}
        node = node[part]
    return node, leaf


def edit(file: str, put=(), drop=()) -> None:
    """``put``: ``(dotted key, en, es-ES, fr, pt-BR)`` tuples; ``drop``: dotted keys."""
    for i, loc in enumerate(LOCALES):
        path = MESSAGES / loc / f"{file}.json"
        tree = json.loads(path.read_text(encoding="utf-8"))
        for key, *texts in put:
            node, leaf = _parent(tree, key, create=True)
            node[leaf] = texts[i]
        for key in drop:
            node, leaf = _parent(tree, key, create=False)
            if node is not None:
                node.pop(leaf, None)
        path.write_text(json.dumps(tree, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
