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

from deforestation_alerts import gdal_env


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


def home_relative(path) -> str:
    """``path`` relative to the user's home, for a SEPAL download link.

    Raises ``ValueError`` for a path outside the home directory (a data
    directory outside ``$HOME`` cannot be served by the sandbox)."""
    return Path(path).resolve().relative_to(Path.home().resolve()).as_posix()
