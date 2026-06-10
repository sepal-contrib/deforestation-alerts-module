"""Analysis tile package (Phase-4 Solara port of the OLD ipyvuetify god-class).

Exports the parent ``build_analysis_layout`` raw-widget builder. See
``analysis_tile.py`` for the parent architecture + the inter-component contract
handed to the sub-component builders in ``imagery.py`` / ``review_panel.py`` /
``edit_tools.py`` / ``export.py`` / ``navigator.py``.
"""

from gui.tile.analysis.analysis_tile import build_analysis_layout

__all__ = ["build_analysis_layout"]
