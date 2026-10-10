from pysepal import color
from pysepal.scripts import utils as su
from pysepal.frontend.resize_trigger import rt
import ipyvuetify as v
from pysepal.sepalwidgets.sepalwidget import SepalWidget
from configparser import ConfigParser
from pathlib import Path
from typing import List, Tuple, Union
from typing import Callable, List
import time

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

    def set_url(self, path: Union[str, Path] = "#"):
        """Set the URL of the download btn. and unable it.

        If nothing is provided the btn is disabled.

        Args:
            path: the absolute path to a downloadable content
        """
        # set the url
        url = su.create_download_link(path)
        self.href = url

        # unable or disable the btn
        self.disabled = str(path) == "#"

        # set the download attribute
        name = None if str(path) == "#" else Path(path).name
        self.attributes = {"download": name}

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
