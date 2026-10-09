"""``AlertViews``: the analysis screen's synced before/after maps (spec §5.3).

``MultiView`` (``gui.widget.multiview``) with the plugin mixins this app uses,
pointed at the app bundle that registers the same JS plugins (``js/app``,
built into ``static/`` by ``cd js/app && npm run build``). Mixins are added
as their tasks land. ``gui.tile.analysis.pane_layers.PaneLayers`` is its
only writer.
"""

from pathlib import Path

from gui.widget.multiview import Draw, FitBounds, LayerMenu, MultiView

STATIC = Path(__file__).resolve().parent / "static"


class AlertViews(MultiView, LayerMenu, FitBounds, Draw):
    _esm = STATIC / "multiview.js"
    _css = STATIC / "multiview.css"
