"""Save, load, list, delete and duplicate projects on the sandbox disk."""

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from deforestation_alerts.project import paths
from deforestation_alerts.project.models import SCHEMA_VERSION, Project

_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]+$")


class ProjectLoadError(Exception):
    """The manifest is missing, unreadable, or from a newer app version."""


@dataclass(frozen=True)
class ProjectInfo:
    name: str
    n_sources: int
    n_periods: int
    n_sets: int
    updated_at: Optional[datetime]


def save_project(p: Project, root: Optional[Path] = None) -> Path:
    """Write the manifest atomically (temp file in the same folder + os.replace).

    Writes a stamped *copy* of ``p`` -- the caller's object is left untouched;
    read the new timestamp back via ``load_project`` if it's needed.
    """
    stamped = p.model_copy(update={"updated_at": datetime.now()})
    folder = paths.project_dir(stamped.name, root)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / paths.MANIFEST
    tmp = folder / f".{paths.MANIFEST}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_text(stamped.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, manifest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return manifest


def load_project(name: str, root: Optional[Path] = None) -> Project:
    """Read ``<root>/<name>/project.json``.

    The folder name wins over the ``name`` stored in the manifest: every path
    is built from ``project.name``, and a folder renamed on disk must reopen
    under its new name. Keeping the stored name would save the next commit
    into the old folder (a ghost project, or another project's folder that has
    since taken that name). Artifact paths are folder-relative, so they still
    resolve.
    """
    manifest = paths.project_dir(name, root) / paths.MANIFEST
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectLoadError(f"cannot read {manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectLoadError(f"{manifest} is not a project manifest (schema mismatch)")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ProjectLoadError(
            f"{manifest} has schema {version}; this app reads schema {SCHEMA_VERSION}"
        )
    raw["name"] = name
    try:
        return Project.model_validate(raw)
    except ValidationError as exc:
        raise ProjectLoadError(f"{manifest} is not a valid project: {exc}") from exc


def list_projects(root: Optional[Path] = None) -> list[ProjectInfo]:
    base = Path(root) if root else paths.data_dir()
    if not base.is_dir():
        return []
    infos = []
    for folder in sorted(base.iterdir(), key=lambda d: d.name.casefold()):
        manifest = folder / paths.MANIFEST
        if not manifest.is_file():
            continue
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            updated = datetime.fromisoformat(raw["updated_at"]) if raw.get("updated_at") else None
        except (OSError, ValueError, KeyError):
            continue
        infos.append(ProjectInfo(
            name=folder.name,
            n_sources=len(raw.get("sources") or {}),
            n_periods=len(raw.get("periods") or {}),
            n_sets=len(raw.get("alert_sets") or {}),
            updated_at=updated,
        ))
    return infos


def delete_project(name: str, root: Optional[Path] = None) -> None:
    """Remove the folder: contents first, manifest last (a half-deleted folder
    without a manifest no longer lists as a project). Callers must refuse while
    a writer is active (gui.store.project_writers)."""
    folder = paths.project_dir(name, root)
    manifest = folder / paths.MANIFEST
    for child in list(folder.iterdir()):
        if child == manifest:
            continue
        if child.is_symlink():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    manifest.unlink(missing_ok=True)
    folder.rmdir()


def validate_project_name(name: str, existing: list[str]) -> Optional[str]:
    text = (name or "").strip()
    if not text:
        return "empty"
    if not _NAME_RE.match(text):
        return "chars"
    if text.casefold() in {e.casefold() for e in existing}:
        return "exists"
    return None


def duplicate_project(p: Project, new_name: str) -> Project:
    """Same AOI, sources, layers, grid and look-back; no periods, files or sets.

    Does not copy the ``aoi.geojson`` sidecar -- Task 14's Duplicate action
    copies it with ``shutil.copy2`` when ``p.aoi.geometry_file`` is set.
    """
    now = datetime.now()
    return Project(
        name=new_name,
        created_at=now,
        updated_at=now,
        aoi=p.aoi.model_copy(deep=True) if p.aoi else None,
        sources={k: v.model_copy(deep=True) for k, v in p.sources.items()},
        masks={k: v.model_copy(deep=True) for k, v in p.masks.items()},
        vis_layers={k: v.model_copy(deep=True) for k, v in p.vis_layers.items()},
        grid=p.grid.model_copy() if p.grid else None,
        look_back_days=p.look_back_days,
        report_template=p.report_template,
    )
