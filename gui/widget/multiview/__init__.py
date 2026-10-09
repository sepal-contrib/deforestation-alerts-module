"""``leaflet-multiview`` for Python: synced Leaflet views as an anywidget.

A self-contained package: it imports nothing from ``gui.*`` or ``pysepal``
(``tests/test_multiview_boundaries.py``), so it can move to its own repo.
"""

from .draw_state import DrawState
from .layer_store import LayerStore
from .plugins import Draw, FitBounds, Holes, LayerMenu, PluginMixin, TileErrors, plugin_order
from .specs import ALL, geojson_spec, set_view, tile_spec
from .widget import MultiView

__all__ = [
    "ALL",
    "Draw",
    "DrawState",
    "FitBounds",
    "Holes",
    "LayerMenu",
    "LayerStore",
    "MultiView",
    "PluginMixin",
    "TileErrors",
    "geojson_spec",
    "plugin_order",
    "set_view",
    "tile_spec",
]
