"""Analysis tile package (Phase-4 Solara port of the OLD ipyvuetify god-class).

Exports the parent ``AnalysisTile`` component. See ``analysis_tile.py`` for the
foundation architecture + the inter-component contract handed to the (currently
stub) sub-components in ``imagery.py`` / ``cnn_panel.py`` / ``edit_tools.py`` /
``export.py``.
"""

from gui.tile.analysis.analysis_tile import AnalysisTile

__all__ = ["AnalysisTile"]
