"""A real ``PaneLayers`` on a real (headless) ``AlertViews``, for the analysis tests."""

import copy

from gui.tile.analysis.pane_layers import PaneLayers
from gui.widget.alert_views import AlertViews


def make_panes():
    """``PaneLayers`` on a fresh ``AlertViews``, without Earth Engine."""
    return PaneLayers(AlertViews())


def reviewer_edits(panes, features):
    """A finished edit in the browser: the frontend's *user* write of the
    ``draw`` envelope (spec §5.2), with the ``gen`` it last saw and the next
    ``seq``. ``set_state`` is how ipywidgets applies a frontend message, so
    every observer runs as it does live."""
    current = dict(panes.widget.draw or {})
    panes.widget.set_state({"draw": {
        "fc": {"type": "FeatureCollection", "features": copy.deepcopy(list(features))},
        "gen": int(current.get("gen", 0)),
        "seq": int(current.get("seq", 0)) + 1,
        "origin": "user",
    }})
