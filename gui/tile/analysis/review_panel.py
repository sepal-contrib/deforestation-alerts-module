"""AlertReviewPanel: the review questionnaire (confirmation + loss-driver).

Architecture layer: view (raw-widget builder, no ``@solara.component``). Ports
the OLD ``AnalysisTile`` confirmation + driver question card area (``card35`` in
the OLD ``initialize_layout``): the "Is this a true alert?" select
(``boton_confirmacion``) and the loss-driver combobox (``comments_input``).
This panel owns ONLY the review questionnaire — the confirmation select and the
loss-driver combobox.

The CNN model execution (``run_dl_model_1/2`` -> ``cnn_worker.apply_dl_model``)
and the polygon draw/edit live in ``edit_tools.py`` (``AlertEditTools``), since the
DL toolbars share the draw controls and are only shown during edit mode — they are
NOT wired here. This panel only renders the static confirmation card with the
question select + driver combobox; the save flow that reads
``confirmation_select.v_model`` / ``driver_combobox.v_model`` lives in AlertExport
(Task 9.5), so the selections are stashed on ``widgets`` for it to read.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch; the confirmation/driver selections are stashed
    here for AlertExport to read.
app_state : AppState
    Navigation + alerts source of truth.
analysis : AnalysisState
    Reserved per-alert state (unused by this questionnaire panel).
app_tile_model : SetContext
    Reserved (output paths) — unused by this questionnaire panel.
"""

import ipyvuetify as v
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm


def build_review_panel(
    widgets,
    app_state,
    analysis,
    app_tile_model,
):
    """Build and return the review questionnaire card (confirmation + loss-driver).

    CNN model execution and polygon draw/edit are owned by ``AlertEditTools``
    (``edit_tools.py``), not this panel.
    """
    confirmation_select = sw.Select(
        items=[
            cm.analysis_tile.questionarie.confirmation_yes,
            cm.analysis_tile.questionarie.confirmation_no,
            cm.analysis_tile.questionarie.confirmation_revision,
        ],
        v_model=cm.analysis_tile.questionarie.confirmation_yes,
        small=True,
        multiple=False,
        clearable=True,
        chips=True,
    )
    driver_combobox = sw.Combobox(
        items=[
            cm.analysis_tile.questionarie.loss_driver1,
            cm.analysis_tile.questionarie.loss_driver2,
            cm.analysis_tile.questionarie.loss_driver3,
            cm.analysis_tile.questionarie.loss_driver4,
            cm.analysis_tile.questionarie.loss_driver5,
            cm.analysis_tile.questionarie.loss_driver6,
            cm.analysis_tile.questionarie.loss_driver7,
            cm.analysis_tile.questionarie.loss_driver8,
            cm.analysis_tile.questionarie.loss_driver9,
        ],
        v_model=[cm.analysis_tile.questionarie.loss_driver9],
        label=cm.analysis_tile.questionarie.loss_driver_hint,
        multiple=True,
        clearable=True,
        chips=True,
    )

    # Stash on widgets so the future AlertExport (Task 9.5) can read the user's
    # confirmation + driver selections when saving the alert attributes.
    widgets.confirmation_select = confirmation_select
    widgets.driver_combobox = driver_combobox

    card = v.Card(
        class_="pa-2 ma-2",
        hover=True,
        children=[
            v.CardTitle(
                class_="pa-0 ma-1",
                children=[cm.analysis_tile.questionarie.confirmation_q],
            ),
            confirmation_select,
            v.CardTitle(
                class_="pa-1 ma-1",
                children=[cm.analysis_tile.questionarie.loss_driver_label],
            ),
            driver_combobox,
        ],
    )
    # CNN run + verify/add/remove prediction handlers live in AlertEditTools
    # (edit_tools.py), since the DL toolbars share the draw controls.
    return card
