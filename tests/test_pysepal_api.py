"""Guard tests: pysepal must expose the (semi-private) surface this app relies on.

If any of these fail after the dependency bump, the rename is NOT a drop-in and the
affected code (AoiDateModel / aux_tile / recipe_tile) needs explicit porting.
"""


def test_pysepal_aoi_model_exposes_private_setters():
    from pysepal.aoi.aoi_model import AoiModel

    for attr in ("_from_admin", "_from_vector", "_from_geo_json", "_from_asset"):
        assert hasattr(AoiModel, attr), f"AoiModel is missing {attr}"


def test_pysepal_planetview_importable():
    from pysepal.planetapi import PlanetView  # noqa: F401


def test_pysepal_core_imports():
    from pysepal import model, color  # noqa: F401
    from pysepal import sepalwidgets as sw  # noqa: F401
    from pysepal.scripts import utils as su  # noqa: F401
    from pysepal.scripts.utils import init_ee  # noqa: F401
    from pysepal.mapping import SepalMap  # noqa: F401
    from pysepal.translator import Translator  # noqa: F401
    from pysepal.frontend.resize_trigger import rt  # noqa: F401
