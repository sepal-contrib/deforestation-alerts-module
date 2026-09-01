"""The one boundary every *user* file in this app is read and written through.

Why it exists
-------------
On the classic SEPAL path the app runs inside the user's own sandbox, so
``~/module_results/deforestation_alerts`` *is* the user's workspace and plain
``open()`` works. In the app-launcher container it is not: the container
filesystem is the app's, not the user's, and a recipe written to
``Path.home()`` there is invisible to the user and gone when the container
dies. Every runtime read/write/list/mkdir of *user data* therefore has to go
through the session ``SepalClient``'s user-files API.

``Workspace`` is that indirection, and deliberately the only one: there is no
``DEPLOY_ENV`` switch and no local fallback, so a single code path serves
voila-in-sandbox and the container alike. What legitimately stays
container-local is (a) packaged read-only assets shipped with the code (the
report template, the message JSONs) and (b) scratch files that a library which
only speaks local paths (rasterio, pyarrow, geopandas, python-docx, zipfile)
writes and we then upload -- which is what :meth:`Workspace.scratch_file`,
:meth:`Workspace.download_to_scratch` and :meth:`Workspace.upload_from_scratch`
are for.

Construction
------------
Built once in ``Page()`` from ``get_current_sepal_client()`` and passed down
like ``gee_interface``. Never fetch the client at module import time: it is
per-kernel session state and does not exist yet at import.

Notes on the client surface this binds to
-----------------------------------------
* It calls ``client.files.*`` (``pysepal_api.UserFilesEndpoint``), not the
  legacy ``client.get_file`` / ``set_file`` / ``list_files`` /
  ``get_remote_dir`` verbs. Those four live in ``pysepal.scripts.sepal_client``
  purely as a deprecation shim: each emits a ``DeprecationWarning`` and
  delegates to the very ``files.*`` methods used here, and they are scheduled
  for removal in pysepal 4.0. Absorbing that is exactly a boundary's job.
* ``client.results_path`` is *pure* -- ``module_results_path(module_name)``,
  computed in ``__init__`` with no I/O -- so :meth:`results_dir` is pure path
  arithmetic and safe to call on a UI render path.
  ``client.ensure_results_dir()`` is the opposite (a network mkdir its own
  docstring says to call once at startup, never per write) and is never called
  from here: ``files.mkdir(..., parents=True)`` creates the root as a
  by-product of creating any folder under it, and every directory this adapter
  creates is remembered so it is created at most once per session.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from pysepal_api.errors import NotFound
# `sanitize_write_path` is the exact rule the client applies to every write, so
# reusing it makes `home_relative` agree with what the server would accept
# instead of re-deriving the home prefix here.
from pysepal_api.paths import sanitize_write_path

from deforestation_alerts import gdal_env

logger = logging.getLogger("SEPALUI.deforestation_alerts")


def _as_posix(path: Any) -> PurePosixPath:
    """Coerce a remote path (str / PurePosixPath / Path) to ``PurePosixPath``."""
    return PurePosixPath(str(path))


class Workspace:
    """User-file IO against the session's SEPAL workspace.

    Args:
        sepal_client: the per-session client from ``get_current_sepal_client()``.
            Must be a real client created with a ``module_name`` -- both a
            ``None`` client (no SEPAL session) and a module-less one are
            programming errors here, and are raised at construction so they
            surface at startup rather than halfway through a user's save.
    """

    def __init__(self, sepal_client: Any) -> None:
        if sepal_client is None:
            raise ValueError(
                "Workspace requires a SepalClient; get_current_sepal_client() "
                "returned None, which means no SEPAL session is available. "
                "Construct the Workspace inside a @with_sepal_sessions Page()."
            )
        files = getattr(sepal_client, "files", None)
        if files is None:
            raise TypeError(
                "Workspace requires a pysepal_api SepalClient exposing `.files`; "
                f"got {type(sepal_client).__name__}"
            )
        results_path = getattr(sepal_client, "results_path", None)
        if results_path is None:
            raise ValueError(
                "SepalClient has no results_path: it was created without a "
                "module_name. Pass module_name='deforestation_alerts'."
            )

        self._client = sepal_client
        self._files = files
        self._root = _as_posix(results_path)
        # Remote directories already created in this session. `files.mkdir` is
        # idempotent but is still a round trip, and writes happen in loops
        # (one per alert), so remember them.
        self._ensured: set[str] = set()
        # Created lazily: a Workspace that never stages a file makes no dirs.
        # Guarded because a Workspace is shared across the app's worker threads
        # and a race here would orphan a scratch tree.
        self._scratch_root: Path | None = None
        self._scratch_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Remote paths
    # ------------------------------------------------------------------

    @property
    def client(self) -> Any:
        """The underlying ``SepalClient`` (for tasks/recipes endpoints)."""
        return self._client

    @property
    def root(self) -> str:
        """Absolute remote results root, e.g.
        ``/home/sepal-user/module_results/deforestation_alerts``."""
        return str(self._root)

    def results_dir(self, *parts: Any) -> str:
        """Remote path under the results root. Pure -- no I/O, no network.

        Safe to call on a render path. Nothing is created; use
        :meth:`ensure_dir` (or just write, which creates the parent) for that.

        Raises:
            ValueError: if a part is absolute or contains ``..`` -- either
                would silently escape the results root.
        """
        path = self._root
        for part in parts:
            p = _as_posix(part)
            if p.is_absolute():
                raise ValueError(f"results_dir part must be relative, got {part!r}")
            if ".." in p.parts:
                raise ValueError(f"results_dir part must not traverse, got {part!r}")
            path = path / p
        return str(path)

    def home_relative(self, remote_path: Any) -> str:
        """``remote_path`` relative to the SEPAL user's home.

        What a ``DownloadBtn`` needs: ``sw.DownloadBtn.set_url`` builds
        ``https://sepal.io/api/sandbox/jupyter/files/<p>`` where ``<p>`` is
        ``Path(path).relative_to(Path.home())`` -- and in the container
        ``Path.home()`` is the *container's* home, so that call raises and the
        link degrades to a container-absolute path that resolves to nothing.
        Handing it a home-relative path instead produces the correct URL on
        both targets.

        Raises:
            pysepal_api.InvalidPathError: for an absolute path outside the
                SEPAL home, or one containing ``..``.
        """
        return str(sanitize_write_path(str(remote_path)))

    def ensure_dir(self, *parts: Any) -> str:
        """Create a directory under the results root and return its path.

        Network I/O on the first call for a given path, then free: the created
        paths are remembered for the life of the Workspace. Creates the results
        root itself as a by-product (``parents=True``), which is why
        ``ensure_results_dir()`` is never needed.
        """
        return self._ensure_remote_dir(self.results_dir(*parts))

    def _ensure_remote_dir(self, remote_dir: str) -> str:
        # A bare filename's parent is "."; there is nothing to create, and
        # createFolder on it would be a pointless (and odd) round trip. A
        # duplicate mkdir from two threads is harmless -- it is idempotent
        # server-side -- so the memo needs no lock.
        if remote_dir in (".", "", "/"):
            return remote_dir
        if remote_dir not in self._ensured:
            self._files.mkdir(remote_dir, parents=True)
            self._ensured.add(remote_dir)
        return remote_dir

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def read_bytes(self, remote_path: Any) -> bytes:
        """Download ``remote_path``. Raises ``pysepal_api.NotFound`` if absent."""
        return self._files.read_bytes(str(remote_path))

    def read_text(self, remote_path: Any) -> str:
        """Download ``remote_path`` and decode it as text."""
        return self._files.read_text(str(remote_path))

    def read_json(self, remote_path: Any) -> Any:
        """Download ``remote_path`` and parse it as JSON."""
        return self._files.read_json(str(remote_path))

    def write_bytes(self, remote_path: Any, data: bytes, *, overwrite: bool = True) -> str:
        """Upload ``data`` to ``remote_path``; returns the path written.

        The parent directory is created first (once per session). ``overwrite``
        defaults to **True**, unlike the raw client: every write here is a save
        of app state the user asked to re-save (``alert_db.parquet``,
        ``recipe_parameters.json``, exports), and the client's ``False`` default
        would make the second save of a session raise ``Conflict``. Pass
        ``overwrite=False`` for a genuine create-only write.
        """
        path = _as_posix(remote_path)
        self._ensure_remote_dir(str(path.parent))
        self._files.write(str(path), data, overwrite=overwrite)
        return str(path)

    def write_text(self, remote_path: Any, text: str, *, overwrite: bool = True) -> str:
        """Upload ``text`` (UTF-8) to ``remote_path``."""
        return self.write_bytes(remote_path, text.encode("utf-8"), overwrite=overwrite)

    def write_json(
        self, remote_path: Any, obj: Any, *, overwrite: bool = True, **dumps_kwargs: Any
    ) -> str:
        """Serialize ``obj`` as JSON and upload it to ``remote_path``."""
        return self.write_text(
            remote_path, json.dumps(obj, **dumps_kwargs), overwrite=overwrite
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list(
        self,
        remote_path: Any = None,
        *,
        extensions: Sequence[str] | None = None,
        dirs_only: bool = False,
        files_only: bool = False,
        include_hidden: bool = False,
    ) -> list[str]:
        """Sorted child paths of ``remote_path`` (default: the results root).

        Returns full paths built from the folder argument, not the server's
        echoed ``path`` field, so a relative argument yields relative children
        and an absolute one absolute children. A folder that does not exist
        yet lists as empty -- a recipe picker asking before the first recipe
        exists is normal, not an error.
        """
        folder = str(remote_path) if remote_path is not None else self.root
        try:
            listing = self._files.list(
                folder, extensions=extensions, include_hidden=include_hidden
            )
        except NotFound:
            return []
        base = _as_posix(folder)
        out = []
        for entry in listing:
            is_dir = entry.type == "directory"
            if (dirs_only and not is_dir) or (files_only and is_dir):
                continue
            out.append(str(base / entry.name))
        return sorted(out)

    def exists(self, remote_path: Any) -> bool:
        """Whether a file or directory exists remotely (one listing round trip)."""
        path = _as_posix(remote_path)
        try:
            listing = self._files.list(str(path.parent), include_hidden=True)
        except NotFound:
            return False
        return any(entry.name == path.name for entry in listing)

    # ------------------------------------------------------------------
    # Container-local scratch staging
    # ------------------------------------------------------------------

    def _scratch_parent(self) -> Path:
        """This Workspace's private scratch root, created on first use.

        Sits under :func:`deforestation_alerts.gdal_env.scratch_dir` -- the
        directory already proven writable by a real write probe, and never the
        (possibly read-only) process CWD.
        """
        with self._scratch_lock:
            if self._scratch_root is None or not self._scratch_root.is_dir():
                self._scratch_root = Path(
                    tempfile.mkdtemp(prefix="workspace_", dir=gdal_env.scratch_dir())
                )
            return self._scratch_root

    def scratch_dir(self) -> Path:
        """A fresh, empty, container-local directory. For multi-file artifacts
        (a GPKG plus sidecars, the report's intermediate JPEGs, a zip staging
        area) that must not collide with another job's."""
        return Path(tempfile.mkdtemp(dir=self._scratch_parent()))

    def scratch_file(self, suffix: str = "", *, name: str | None = None) -> Path:
        """A fresh container-local path for a library that only writes local files.

        The file is **not** created (GDAL/GeoPandas writers want to create it
        themselves); only its private parent directory is, so two calls never
        collide even with the same ``name``. Upload the result with
        :meth:`upload_from_scratch` -- nothing under scratch is user-visible.
        """
        return self.scratch_dir() / (name or f"scratch{suffix}")

    def download_to_scratch(self, remote_path: Any) -> Path:
        """Stage a remote file locally and return its path.

        Keeps the remote basename -- rasterio/pyarrow/GDAL dispatch on the
        extension, and the report embeds image filenames -- in a private
        directory, so two recipes' ``alert_db.parquet`` do not overwrite each
        other.
        """
        path = _as_posix(remote_path)
        local = self.scratch_dir() / path.name
        local.write_bytes(self.read_bytes(path))
        return local

    def upload_from_scratch(
        self, local_path: Any, remote_path: Any, *, overwrite: bool = True
    ) -> str:
        """Upload a staged local file to ``remote_path``; returns that path.

        The local file is left in place: callers routinely reuse it (the report
        zips the same GeoTIFF it just uploaded). :meth:`cleanup_scratch` is the
        bulk sweep.
        """
        return self.write_bytes(
            remote_path, Path(local_path).read_bytes(), overwrite=overwrite
        )

    def cleanup_scratch(self) -> None:
        """Delete everything this Workspace staged locally. Safe to call twice;
        a later ``scratch_*`` call transparently re-creates the root."""
        with self._scratch_lock:
            if self._scratch_root is None:
                return
            shutil.rmtree(self._scratch_root, ignore_errors=True)
            self._scratch_root = None

    def __repr__(self) -> str:
        return f"Workspace(root={self.root!r})"
