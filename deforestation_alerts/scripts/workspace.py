"""User files on the SEPAL sandbox disk, behind the method names the Analysis,
export, report and CNN code already call.

Why it exists
-------------
The app used to run inside SEPAL's app-launcher container, where user files
were remote and had to be staged through the SEPAL files API -- ``Workspace``
was that indirection. It now runs as Voila in the user's own sandbox (spec
D14), so ``~/module_results/deforestation_alerts`` *is* the user's workspace
and plain ``pathlib``/``open()`` work. This class keeps the OLD surface (same
method names, same shapes) purely so the Analysis, export, report and CNN
code that calls it keeps working unchanged in this slice; it is deleted in
slice 2 when that code is rewritten against the plain filesystem directly.

"Scratch" files still exist for libraries that build a file before it is
moved into place (rasterio, pyarrow, python-docx, zipfile): those write
locally and are then "uploaded" (now just copied) into the results tree.
``download_to_scratch`` no longer stages a copy -- the user's file already IS
local, so it is returned as-is -- and ``discard_scratch`` therefore only ever
deletes paths strictly inside this Workspace's own scratch root: a path
outside it (including the user file ``download_to_scratch`` just handed back)
is left untouched.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Optional, Sequence

from deforestation_alerts import gdal_env
from deforestation_alerts.project import paths

logger = logging.getLogger("SEPALUI.deforestation_alerts")


class Workspace:
    """User-file IO against the local sandbox disk.

    Args:
        root: results root (default: :func:`paths.data_dir`).
        scratch_parent: parent directory for this Workspace's private scratch
            tree (default: :func:`gdal_env.scratch_dir`).
    """

    def __init__(
        self, root: Optional[Path] = None, scratch_parent: Optional[Path] = None
    ) -> None:
        self._root = Path(root) if root else paths.data_dir()
        self._scratch_parent_dir = Path(scratch_parent) if scratch_parent else None
        # Created lazily: a Workspace that never stages a file makes no dirs.
        # Guarded because a Workspace is shared across the app's worker threads
        # and a race here would orphan a scratch tree.
        self._scratch_root: Optional[Path] = None
        self._scratch_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any:
        """``None`` -- there is no remote client any more. Kept so callers
        that still read ``workspace.client`` (e.g. to hand a SEPAL-aware
        widget its session) keep working; they degrade to the widget's local
        default rather than raising."""
        return None

    @property
    def root(self) -> str:
        """Absolute results root, e.g.
        ``/home/sepal-user/module_results/deforestation_alerts``."""
        return str(self._root)

    @property
    def home(self) -> str:
        """The user's home directory, e.g. ``/home/sepal-user``.

        The browse root for file pickers that let a user point at a raster or
        a report template they keep ANYWHERE in their workspace, not just
        under this module's results dir.
        """
        return str(Path.home())

    def results_dir(self, *parts: Any) -> str:
        """Path under the results root. Pure -- no I/O. Nothing is created;
        use :meth:`ensure_dir` (or just write, which creates the parent) for
        that.

        Raises:
            ValueError: if a part is absolute or contains ``..`` -- either
                would silently escape the results root.
        """
        path = self._root
        for part in parts:
            p = Path(str(part))
            if p.is_absolute():
                raise ValueError(f"results_dir part must be relative, got {part!r}")
            if ".." in p.parts:
                raise ValueError(f"results_dir part must not traverse, got {part!r}")
            path = path / p
        return str(path)

    def home_relative(self, path: Any) -> str:
        """``path`` relative to the user's home, e.g. for a SEPAL
        ``DownloadBtn`` URL.

        Raises:
            ValueError: for a path outside the home directory.
        """
        return Path(str(path)).resolve().relative_to(Path.home().resolve()).as_posix()

    def ensure_dir(self, *parts: Any) -> str:
        """Create a directory under the results root and return its path."""
        d = Path(self.results_dir(*parts))
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def read_bytes(self, path: Any) -> bytes:
        return Path(str(path)).read_bytes()

    def read_text(self, path: Any) -> str:
        return self.read_bytes(path).decode("utf-8")

    def read_json(self, path: Any) -> Any:
        return json.loads(self.read_text(path))

    @staticmethod
    def _tmp_sibling(dest: Path) -> Path:
        """A ``.part`` path next to ``dest`` that is unique to this call.

        Two overlapping writers targeting the *same* ``dest`` (e.g. two
        clicks of a save button, or a background job racing a manual save)
        must never share a tmp file: writing to a shared name lets one
        writer's bytes interleave with or clobber the other's before either
        ``replace()`` runs, corrupting whichever one loses the rename race.
        A per-call UUID suffix (matching ``project/io.py``'s
        ``.{name}.{uuid4}.tmp`` convention) makes that impossible -- each
        call owns a distinct inode until it renames into place.
        """
        return dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")

    @staticmethod
    def _rename_into_place(tmp: Path, dest: Path, *, overwrite: bool) -> None:
        """Move ``tmp`` to ``dest``, atomically, honouring ``overwrite``.

        ``overwrite=True`` is a plain atomic rename (last writer wins, same
        as before). ``overwrite=False`` must not do "check dest.exists(),
        then rename" -- that gap is a TOCTOU: another writer could create
        ``dest`` between the check and the rename. ``os.link`` has no such
        gap: it fails atomically, at the filesystem level, iff ``dest``
        already exists, with nothing to race.
        """
        if overwrite:
            tmp.replace(dest)
        else:
            try:
                os.link(tmp, dest)
            except FileExistsError:
                raise FileExistsError(str(dest)) from None

    def write_bytes(self, path: Any, data: bytes, *, overwrite: bool = True) -> str:
        """Write ``data`` to ``path``; returns the path written.

        The parent directory is created first. Written to a private ``.part``
        sibling, then moved into place, so a crash mid-write never leaves a
        truncated file where a reader expects a complete one, and a
        concurrent write to the same ``path`` never corrupts this one (see
        :meth:`_tmp_sibling`). ``overwrite`` defaults to **True**: every
        write here is a save of app state the user asked to re-save. Pass
        ``overwrite=False`` for a genuine create-only write.
        """
        p = Path(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp_sibling(p)
        try:
            tmp.write_bytes(data)
            self._rename_into_place(tmp, p, overwrite=overwrite)
        finally:
            tmp.unlink(missing_ok=True)
        return str(p)

    def write_text(self, path: Any, text: str, *, overwrite: bool = True) -> str:
        return self.write_bytes(path, text.encode("utf-8"), overwrite=overwrite)

    def write_json(
        self, path: Any, data: Any, *, overwrite: bool = True, indent: int = 2
    ) -> str:
        return self.write_text(
            path, json.dumps(data, indent=indent, default=str), overwrite=overwrite
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list(
        self,
        folder: Any = None,
        *,
        extensions: Optional[Sequence[str]] = None,
        include_hidden: bool = False,
    ) -> list[str]:
        """Sorted child paths of ``folder`` (default: the results root).

        A folder that does not exist yet lists as empty -- a picker asking
        before anything has ever been saved is normal, not an error.
        """
        d = Path(str(folder)) if folder is not None else self._root
        if not d.is_dir():
            return []
        exts = {e.lower() for e in (extensions or [])}
        out = []
        for child in d.iterdir():
            if not include_hidden and child.name.startswith("."):
                continue
            if exts and child.suffix.lower() not in exts:
                continue
            out.append(str(child))
        return sorted(out)

    def exists(self, path: Any) -> bool:
        return Path(str(path)).exists()

    # ------------------------------------------------------------------
    # Scratch staging
    # ------------------------------------------------------------------

    def _scratch_parent(self) -> Path:
        """This Workspace's private scratch root, created on first use."""
        with self._scratch_lock:
            if self._scratch_root is None:
                parent = self._scratch_parent_dir or gdal_env.scratch_dir()
                parent.mkdir(parents=True, exist_ok=True)
                self._scratch_root = Path(tempfile.mkdtemp(prefix="ws_", dir=parent))
            return self._scratch_root

    def scratch_dir(self) -> Path:
        """A fresh, empty, private directory. For multi-file artifacts (a
        GPKG plus sidecars, the report's intermediate JPEGs, a zip staging
        area) that must not collide with another job's."""
        return Path(tempfile.mkdtemp(dir=self._scratch_parent()))

    def scratch_file(self, suffix: str = "", *, name: Optional[str] = None) -> Path:
        """A fresh path for a library that only writes local files.

        The file is **not** created (GDAL/GeoPandas writers want to create it
        themselves); only its private parent directory is, so two calls never
        collide even with the same ``name``.
        """
        return self.scratch_dir() / (name or f"scratch{suffix}")

    def download_to_scratch(self, path: Any) -> Path:
        """Return ``path`` unchanged: it is already a local file.

        Kept so callers written against the remote-staging contract (stage,
        read, ``discard_scratch``) keep working -- but nothing is copied any
        more, and the returned path IS the user's own file. Callers must
        still call :meth:`discard_scratch` on it (as before); it is a no-op
        here since the path is outside this Workspace's scratch root.
        """
        return Path(str(path))

    def upload_from_scratch(
        self, local: Any, path: Any, *, overwrite: bool = True
    ) -> str:
        """Copy a staged local file to ``path``; returns that path.

        The local file is left in place: callers routinely reuse it. Copied
        (rather than moved) via a private ``.part`` sibling + atomic rename
        -- same crash-safety and same-destination concurrency guarantees as
        :meth:`write_bytes` (see :meth:`_tmp_sibling`) -- so a caller that
        reuses the staged file after uploading it keeps working; discard it
        explicitly when done.
        """
        dest = Path(str(path))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp_sibling(dest)
        try:
            shutil.copyfile(local, tmp)
            self._rename_into_place(tmp, dest, overwrite=overwrite)
        finally:
            tmp.unlink(missing_ok=True)
        return str(dest)

    def discard_scratch(self, local: Any) -> None:
        """Delete the private scratch directory ``local`` was staged in.

        Every :meth:`scratch_file` (via :meth:`scratch_dir`) call makes its
        own private ``mkdtemp`` directory, so dropping it drops ``local`` and
        nothing else built alongside it. A path outside this Workspace's
        scratch root -- including the user's own file, now handed back
        unchanged by :meth:`download_to_scratch` -- is left untouched: this
        must never be able to delete user data or a packaged asset.
        """
        if self._scratch_root is None:
            return
        p = Path(str(local))
        try:
            p_resolved = p.resolve()
            root_resolved = self._scratch_root.resolve()
        except (OSError, ValueError):  # pragma: no cover -- defensive
            return
        if p_resolved == root_resolved or root_resolved not in p_resolved.parents:
            return  # the scratch root itself, or not ours at all
        if p_resolved.is_dir():
            shutil.rmtree(p_resolved, ignore_errors=True)
            return
        parent = p_resolved.parent
        if parent == root_resolved:
            # A file sitting directly under the root (no private mkdtemp of
            # its own) -- drop just the file, never the root.
            p_resolved.unlink(missing_ok=True)
        else:
            # The common case: `local` was made by `scratch_file`, whose
            # parent is a private mkdtemp under the root holding only this
            # artifact's files -- drop the whole directory.
            shutil.rmtree(parent, ignore_errors=True)

    def cleanup_scratch(self) -> None:
        """Delete everything this Workspace staged locally. Safe to call
        twice; a later ``scratch_*`` call transparently re-creates the root."""
        with self._scratch_lock:
            if self._scratch_root is not None:
                shutil.rmtree(self._scratch_root, ignore_errors=True)
                self._scratch_root = None

    def __repr__(self) -> str:
        return f"Workspace(root={self.root!r})"
