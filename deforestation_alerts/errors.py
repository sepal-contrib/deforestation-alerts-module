"""Errors the UI shows in the user's language (spec §11).

A worker never translates: it raises (or returns) a message key and the values
the message needs, and the UI renders them where the toast, the table note or
the modal error is published (``gui.scripts.error_text.error_text``).
``str(exc)`` stays an English detail for the server log and for tests.
"""

from typing import Optional


class KeyedError(Exception):
    """An exception that names a catalogue message.

    Mix it in before the builtin it refines, so ``except RuntimeError`` and
    ``pytest.raises(ValueError, match=…)`` keep working:
    ``class NotEnoughSpaceError(KeyedError, RuntimeError): key = "errors.not_enough_space"``.
    """

    key: str = ""

    def __init__(self, detail: str = "", /, *, key: Optional[str] = None, **values):
        super().__init__(detail or key or type(self).key)
        if key is not None:
            self.key = key
        self.values = values


class ProjectRuleError(KeyedError, ValueError):
    """A project rule refused an action (a name, the source cap, an edit of a
    used source or mask, a missing input). ``str(exc)`` is the key when no
    detail is given, as the ``ValueError(<key>)`` it replaces."""


class UserFileMissing(KeyedError, FileNotFoundError):
    """A file the project points at (a GeoTIFF source or mask, a layer, a store) is gone."""

    key = "errors.file_not_found"


class FolderNotWritable(KeyedError, PermissionError):
    """A folder the app has to write next to a user file is read-only."""

    key = "errors.folder_not_writable"


class NotDownloadable(KeyedError, ValueError):
    """A file the app wrote is outside the folder the server offers for
    download (``local_files.download_root``), so no link to it would work."""

    key = "errors.not_downloadable"

    def __init__(self, path, root):
        super().__init__(f"{path} is not inside the download root {root}", path=str(path), root=str(root))
