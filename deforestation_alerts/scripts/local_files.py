"""Plain local-file helpers (spec D14: the app runs in the user's sandbox).

They replace slice 1's ``Workspace``. A file is written to a unique
``<dest>.<uuid>.part`` beside its destination and moved into place with
``os.replace``, so a reader never sees half a file and two writers of the same
file never share a temporary. Libraries that insist on writing a named file
(GDAL drivers, python-docx, zipfile) write into a private ``scratch_dir()``
and the result is copied into place.
"""

import contextlib
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from deforestation_alerts import gdal_env
from deforestation_alerts.errors import NotDownloadable


def _part_sibling(dest: Path) -> Path:
    return dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.part")


@contextlib.contextmanager
def write_into(dest) -> Iterator[Path]:
    """Yield a unique ``.part`` path beside ``dest``; on success it replaces
    ``dest``, on any failure it is deleted and ``dest`` is left as it was."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_sibling(dest)
    try:
        yield part
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)


def copy_into(src, dest) -> Path:
    """Copy ``src`` onto ``dest`` atomically; ``src`` is left in place."""
    with write_into(dest) as part:
        shutil.copyfile(src, part)
    return Path(dest)


@contextlib.contextmanager
def scratch_dir() -> Iterator[Path]:
    """A private temporary directory under the process's proven-writable
    scratch root, removed with everything in it when the block ends."""
    parent = gdal_env.scratch_dir()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="defo_", dir=parent) as d:
        yield Path(d)


#: The URL the served root is reached at. The default is root-relative on
#: purpose: the browser resolves it against the host it is already on, so the
#: same build serves sepal.io and test.sepal.io with nothing configured.
#: pysepal's ``create_download_link`` hardcodes ``https://sepal.io/...``, which
#: is why the app builds its own URLs.
DOWNLOAD_BASE_ENV = "DEFORESTATION_ALERTS_DOWNLOAD_BASE"
#: The directory that base serves. On SEPAL jupyter serves ``$HOME``; a local
#: jupyter serves whatever ``--ServerApp.root_dir`` was given (run_ui.sh).
DOWNLOAD_ROOT_ENV = "DEFORESTATION_ALERTS_DOWNLOAD_ROOT"
DEFAULT_DOWNLOAD_BASE = "/api/sandbox/jupyter/files"


def download_root() -> Path:
    """The directory the download base serves; ``$HOME`` unless configured."""
    env = os.environ.get(DOWNLOAD_ROOT_ENV)
    return (Path(env) if env else Path.home()).expanduser().resolve()


def served_relative(path) -> str:
    """``path`` relative to :func:`download_root`, for a download link.

    Raises ``NotDownloadable`` (a ``ValueError``) for a path outside that root: it cannot be served,
    and a link to it would 404 rather than quietly download the wrong file."""
    root = download_root()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise NotDownloadable(resolved, root)
    return resolved.relative_to(root).as_posix()


def home_relative(path) -> str:
    """``path`` relative to the served root, for a download link.

    Kept as the name the UI already calls; :func:`served_relative` is the same
    function under a name that no longer promises ``$HOME``."""
    return served_relative(path)


def download_url_for(relative: str) -> str:
    """The URL of a path already expressed relative to :func:`download_root`.

    Each segment is percent-escaped, so a project named ``Paraguay Norte``
    gives a link that works."""
    base = (os.environ.get(DOWNLOAD_BASE_ENV) or DEFAULT_DOWNLOAD_BASE).rstrip("/")
    return f"{base}/{quote(str(relative).lstrip('/'))}"


def download_url(path) -> str:
    """The URL a download button points at for the absolute ``path``.

    Raises ``ValueError`` as :func:`served_relative` does. Callers that must
    surface that failure at a particular moment (the Analysis export fails its
    task rather than showing a dead button) call :func:`served_relative` where
    they want the error and pass the result to :func:`download_url_for`."""
    return download_url_for(served_relative(path))
