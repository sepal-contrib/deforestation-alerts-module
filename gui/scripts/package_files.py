"""Built review zips found on disk (spec 2026-10-14 §4.4, §4.6).

A package or a reviewed copy is offered for download when its file is in
the set folder under the name ``share`` gives it, and its ``share.json``
names the current split and evaluator. Nothing is kept in memory: the
Evaluation step finds its zips again after a reload or a project reopen. A
file that is not a readable zip of that kind is skipped, never raised.

Blocking (a stat and a header read per zip): the step calls these from a
``use_memo`` keyed on what can change them, not on every render."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from deforestation_alerts.project import share
from deforestation_alerts.project import split as split_mod
from gui.scripts import set_store


@dataclass(frozen=True)
class BuiltZip:
    path: Path
    built_at: datetime
    #: built before the questionnaire changed: the evaluator would answer the old questions
    outdated: bool = False


def _header(path: Path) -> Optional[share.ShareHeader]:
    if not path.is_file():
        return None
    try:
        return share.peek_header(path)
    except (ValueError, OSError):  # ShareError is a ValueError: not a zip, not ours, unreadable
        return None


def _built_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def package_zips(p, s) -> dict[str, BuiltZip]:
    """The packages of ``s``'s current split in its set folder, by evaluator."""
    if p is None or s is None or s.split is None:
        return {}
    folder = Path(set_store.set_folder(p, s))
    digest = split_mod.questions_digest(p.questions)
    out = {}
    for index, name in enumerate(s.split.evaluators, start=1):
        path = folder / share.package_filename(p.name, s.id, index, name)
        header = _header(path)
        if (header is None or header.kind != share.KIND_PACKAGE or header.split_id != s.split.split_id
                or header.evaluator != name):
            continue
        out[name] = BuiltZip(path=path, built_at=_built_at(path), outdated=header.questions_digest != digest)
    return out


def return_zip(p) -> Optional[BuiltZip]:
    """A review copy's latest reviewed copy, in its set folder."""
    rc = p.review_copy if p is not None else None
    if rc is None:
        return None
    s = p.alert_sets.get(rc.set_id)
    if s is None:
        return None
    path = Path(set_store.set_folder(p, s)) / share.return_filename(rc.origin_project, rc.set_id,
                                                                     rc.evaluator_index, rc.evaluator)
    header = _header(path)
    if header is None or header.kind != share.KIND_RETURN or header.split_id != rc.split_id:
        return None
    return BuiltZip(path=path, built_at=_built_at(path))
