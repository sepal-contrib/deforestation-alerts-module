"""S1 file tiles of the Export section (spec D6): one row per file the build
makes -- a format badge, its name, what is inside and its size, a download
icon -- the whole tile a download link.

A ``custom_sw.DownloadBtn`` underneath, so ``set_url`` builds the link from
``local_files`` (the served-root-relative path under the download base, which
follows whichever SEPAL serves the app), sets the ``download`` attribute and
disables the tile on "#". ``set_file`` is what the
export section calls: the set-folder path of the file, or None to clear.
``set_action`` gives a tile with no file a second face, for the GIF tile:
a plain button (no link) that makes the file when clicked, its detail saying
whether it can.
"""

from pathlib import Path
from typing import Optional

import ipyvuetify as v

from deforestation_alerts.scripts import local_files
from gui.widget import custom_sw

#: The format badge (mockup S1 ``.ic``): primary text on a translucent primary
#: layer. Both come from the theme's ``primary`` / ``primary--text`` classes,
#: not a colour here: the theme does not publish ``--v-primary-base``, so a
#: ``var()`` fell back to the light green on the dark (gold) theme too.
_BADGE = ("position:relative;overflow:hidden;width:30px;height:30px;border-radius:5px;display:flex;"
          "align-items:center;justify-content:center;flex:0 0 auto;font-size:10px;font-weight:700;")
_BADGE_LAYER = "position:absolute;top:0;right:0;bottom:0;left:0;opacity:0.15;"
#: The tile itself (mockup S1 ``.tile``): a neutral border that reads on both
#: themes, the theme's own text colour, never underlined (it is an ``<a>``).
TILE_STYLE = ("height:auto;min-height:52px;justify-content:flex-start;text-transform:none;letter-spacing:normal;"
              "text-decoration:none;border-color:rgba(128,128,128,0.35);border-radius:6px;"
              "padding:8px 10px;margin:0 0 6px 0;")
_TITLE = "font-size:13px;font-weight:600;line-height:1.3;text-decoration:none;"
_DETAIL = "opacity:0.6;text-decoration:none;white-space:normal;"


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


class FileTile(custom_sw.DownloadBtn):
    """One downloadable file of the build, disabled until ``set_file`` gives
    it one. ``kind`` (``"zip"`` / ``"docx"`` / ``"gif"``) is the badge text and
    what a caller finds the tile by; ``size_text`` is the size shown, ``""``
    while there is no file or it cannot be read."""

    def __init__(self, kind: str, title: str, detail: str, **kwargs):
        self.kind = kind
        self.size_text = ""
        self.path: Optional[str] = None
        self._detail = detail
        self._meta = v.Html(tag="div", class_="caption", style_=_DETAIL, children=[detail])
        self._icon = v.Icon(small=True, class_="primary--text", children=["mdi-download"])
        # No ``color``: an outlined primary button underlined and tinted the
        # whole row; only the badge and the download icon carry the primary.
        super().__init__(text=title, block=True, outlined=True, color=None, class_="defo-file-tile",
                         style_=TILE_STYLE, **kwargs)
        # ``DownloadBtn`` puts its own icon + text in; the tile is the S1 row.
        self.children = [
            v.Html(tag="div", class_="primary--text", style_=_BADGE, children=[
                v.Html(tag="span", class_="primary", style_=_BADGE_LAYER),
                v.Html(tag="span", style_="position:relative;", children=[kind.upper()]),
            ]),
            v.Html(tag="div", class_="ml-3 text-left", style_="flex:1 1 auto;min-width:0;", children=[
                v.Html(tag="div", style_=_TITLE, children=[title]),
                self._meta,
            ]),
            self._icon,
        ]

    def set_file(self, path: Optional[str]) -> "FileTile":
        """Link ``path`` (a file in the set folder), or clear the tile (None).

        Raises ``ValueError`` (``local_files.home_relative``) for a path the
        sandbox cannot serve, before anything on the tile changes: the export
        worker reports it as a FAILED build, and the tile stays as it was.
        """
        if not path:
            self.size_text = ""
            self.path = None
            self._meta.children = [self._detail]
            self._icon.children = ["mdi-download"]
            return self.set_url()
        url_path = local_files.home_relative(path)
        self.path = path
        self._icon.children = ["mdi-download"]
        try:
            self.size_text = human_size(Path(path).stat().st_size)
        except OSError:
            self.size_text = ""
        self._meta.children = [f"{self._detail} · {self.size_text}" if self.size_text else self._detail]
        return self.set_url(path=url_path)

    def set_action(self, enabled: bool, detail: str, icon: str = "mdi-filmstrip") -> "FileTile":
        """The tile while it has no file: a button that makes one. No ``href``,
        so the click reaches the handler instead of opening "#" in a new tab;
        ``enabled`` says whether it can be clicked, ``detail`` why (or what a
        click does). A tile that has a file is left as it is."""
        if self.path is not None:
            return self
        self.href = None
        self.disabled = not enabled
        self._meta.children = [detail]
        self._icon.children = [icon]
        return self
