"""S1 file tiles of the Export section (spec D6): one row per file the build
makes -- a format badge, its name, what is inside and its size, a download
icon -- the whole tile a download link.

A ``sw.DownloadBtn`` underneath, so ``set_url`` keeps pysepal's URL logic
(``su.create_download_link``: the sandbox file route of a ``$HOME``-relative
path, the ``download`` attribute, disabled on "#"). ``set_file`` is what the
export section calls: the set-folder path of the file, or None to clear.
"""

from pathlib import Path
from typing import Optional

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.scripts import local_files
from gui.widget.text_style import MUTED

_BADGE = ("width:30px;height:30px;border-radius:5px;display:flex;align-items:center;justify-content:center;"
          "flex:0 0 auto;font-size:10px;font-weight:700;background:rgba(91,182,36,0.15);"
          "color:var(--v-primary-base,#5bb624);")
TILE_STYLE = ("height:auto;min-height:52px;justify-content:flex-start;text-transform:none;letter-spacing:normal;"
              "padding:6px 10px;margin:0 0 6px 0;")


def human_size(n: int) -> str:
    """``n`` bytes for a person: whole B and KB, one decimal from MB up. A
    value that ROUNDS to 1024 of a unit reads in the next one ("1.0 MB", never
    "1024 KB")."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        decimals = 0 if unit in ("B", "KB") else 1
        if round(size, decimals) < 1024 or unit == "GB":
            return f"{size:.{decimals}f} {unit}"
        size /= 1024
    return f"{n} B"  # unreachable: the loop returns at "GB"


class FileTile(sw.DownloadBtn):
    """One downloadable file of the build, disabled until ``set_file`` gives
    it one. ``kind`` (``"zip"`` / ``"docx"`` / ``"gif"``) is the badge text and
    what a caller finds the tile by; ``size_text`` is the size shown, ``""``
    while there is no file or it cannot be read."""

    def __init__(self, kind: str, title: str, detail: str, **kwargs):
        self.kind = kind
        self.size_text = ""
        self._detail = detail
        self._meta = v.Html(tag="div", class_="caption", style_=MUTED, children=[detail])
        super().__init__(text=title, block=True, outlined=True, color="primary", class_="defo-file-tile",
                         style_=TILE_STYLE, **kwargs)
        # ``DownloadBtn`` puts its own icon + text in; the tile is the S1 row.
        self.children = [
            v.Html(tag="div", style_=_BADGE, children=[kind.upper()]),
            v.Html(tag="div", class_="ml-3 text-left", style_="flex:1 1 auto;min-width:0;", children=[
                v.Html(tag="div", class_="body-2 font-weight-medium", children=[title]),
                self._meta,
            ]),
            v.Icon(small=True, children=["mdi-download"]),
        ]

    def set_file(self, path: Optional[str]) -> "FileTile":
        """Link ``path`` (a file in the set folder), or clear the tile (None).

        Raises ``ValueError`` (``local_files.home_relative``) for a path the
        sandbox cannot serve, before anything on the tile changes: the export
        worker reports it as a FAILED build, and the tile stays as it was.
        """
        if not path:
            self.size_text = ""
            self._meta.children = [self._detail]
            return self.set_url()
        url_path = local_files.home_relative(path)
        try:
            self.size_text = human_size(Path(path).stat().st_size)
        except OSError:
            self.size_text = ""
        self._meta.children = [f"{self._detail} · {self.size_text}" if self.size_text else self._detail]
        return self.set_url(path=url_path)
