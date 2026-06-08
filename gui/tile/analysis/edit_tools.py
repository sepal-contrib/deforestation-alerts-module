"""AlertEditTools sub-component STUB (Task 9.3).

Architecture layer: view. Ports the OLD ``AnalysisTile`` edit-tools card
(``card04`` + ``label33``/``toolBarEdition``): the start / save / stop / clear
draw-edit buttons that drive the two ``CustomDrawControl``s (``draw_before`` /
``draw_after`` on ``widgets``) and persist the user-drawn deforestation polygon to
``analyzed_alerts_model.defo_dl_layer``. The OLD handlers were
``start_edition_function`` / ``save_edition_function`` / ``stop_edition_function``
/ ``clear_edition_function``.

For the FOUNDATION this renders the static edit toolbar (buttons present, all
DISABLED / no-op) so the layout slot + contract exist; Task 9.3 wires the draw
control interactions.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets; the edit buttons toggle ``draw_before`` / ``draw_after``
    visibility and read/write their ``.data`` / ``.to_json()``.
app_state : AppState
    Navigation + alerts source of truth.
analyzed_alerts_model : AlertAnalysisModel
    ``defo_dl_layer`` — the user-drawn polygon GeoDataFrame.
"""

import ipyvuetify as v
import solara
from pysepal import color

from deforestation_alerts.message import cm


class _EditionButton(v.Btn):
    """Small disabled-by-default icon button (OLD ``EditionButtons``)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("small", True)
        kwargs.setdefault("class_", "pa-1 ma-1")
        kwargs.setdefault("disabled", True)
        super().__init__(**kwargs)


@solara.component
def AlertEditTools(
    widgets,
    app_state,
    analyzed_alerts_model,
):
    """STUB. Renders the static edit toolbar card (buttons present, no behavior)."""

    def _build():
        start_btn = _EditionButton(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-pen"])],
        )
        save_btn = _EditionButton(
            color=color.primary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-floppy-disk"])],
        )
        stop_btn = _EditionButton(
            color=color.secondary,
            children=[v.Icon(color=color.bg, children=["fa-solid fa-x"])],
        )
        toolbar = v.Toolbar(
            class_="px-1 d-flex justify-space-around",
            children=[start_btn, save_btn, stop_btn],
        )
        return v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.edition_labels.edit_title],
                ),
                toolbar,
            ],
        )

    card = solara.use_memo(_build, dependencies=[])
    # TODO(Task 9.3 AlertEditTools): wire start/save/stop/clear edition handlers
    # driving widgets.draw_before/draw_after and analyzed_alerts_model.defo_dl_layer.
    solara.display(card)
