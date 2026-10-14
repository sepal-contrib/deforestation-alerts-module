from pysepal import color, sepalwidgets as sw
from pysepal.frontend.resize_trigger import rt
import ipyvuetify as v
from pysepal.sepalwidgets.sepalwidget import SepalWidget
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Union
from typing import Callable, List
import time

from deforestation_alerts.scripts import local_files

# ``#`` is pysepal's "nothing to download yet" sentinel: a button built with it
# starts disabled, and a real path enables it.
NOTHING = "#"


def _apply_url(btn, path) -> None:
    """Point ``btn`` at ``path``, or disable it when there is nothing to offer.

    Replaces pysepal's ``create_download_link``, which hardcodes ``sepal.io``
    and so sends a test.sepal.io (or local) user to production. The URL comes
    from ``local_files``, root-relative by default, so it resolves against
    whatever host is serving the app.

    ``path`` is relative to the served root — callers relativize it themselves,
    inside whatever error handling should see a path that cannot be served. An
    absolute path is relativized here instead, and raises ``ValueError`` when
    it lies outside the root.
    """
    if str(path) == NOTHING:
        btn.href = NOTHING
        btn.disabled = True
        btn.attributes = {"download": None}
        return
    relative = local_files.served_relative(path) if Path(path).is_absolute() else str(path)
    btn.href = local_files.download_url_for(relative)
    btn.disabled = False
    btn.attributes = {"download": Path(path).name}


class DownloadBtn(sw.DownloadBtn):
    """``sw.DownloadBtn`` whose URL follows the SEPAL the app runs on.

    Takes the file's absolute path; the base URL and the directory it serves
    are ``local_files``' two environment variables.
    """

    def set_url(self, path: Union[str, Path] = NOTHING):
        _apply_url(self, path)
        return self


class ShareBtn(v.Btn, SepalWidget):
    def __init__(
        self, icon="fa-solid fa-share-nodes", path: Union[str, Path] = "#", **kwargs
    ) -> None:
        # set the btn parameters
        kwargs.setdefault("x_small", True)
        kwargs.setdefault("fab", True)
        kwargs.setdefault("class_", "mx-2 px-2")
        kwargs["children"] = [v.Icon(children=[icon])]
        super().__init__(**kwargs)

        self.set_url(path)

    def set_url(self, path: Union[str, Path] = NOTHING):
        """Point the button at ``path``; disabled when there is nothing yet.

        Args:
            path: the absolute path to a downloadable content
        """
        _apply_url(self, path)
        return self


# ``RecipeBtn`` (the "zip this recipe and hand it to a DownloadBtn" widget) was
# REMOVED here, not migrated. It was the last importer of the deleted
# ``parameter.module_dir`` and it was dead code: the Phase-4 port replaced the
# header RecipeBtn/ShareBtn pair with the Recipe tile's Save card
# (``gui/tile/recipe_tile.py``), and nothing has constructed it since. Every line
# of it assumed a container-local recipe folder (``module_dir / name``,
# ``.exists()``, ``.iterdir()``, a local ``zipfile.ZipFile``, and a ``set_url``
# with a container-absolute path). If exporting a folder as a zip is wanted
# again, build the zip in a ``local_files.scratch_dir()``, ``local_files.copy_into``
# the set folder, then ``set_url(local_files.home_relative(...))`` — the same
# shape as ``gui/tile/analysis/export.py::_download_data`` (inventory row a36).
