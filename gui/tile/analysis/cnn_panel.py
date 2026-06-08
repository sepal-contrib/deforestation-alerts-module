"""AlertCnnPanel sub-component STUB (Task 9.4).

Architecture layer: view. Ports the OLD ``AnalysisTile`` confirmation + driver +
edit-DL question card area (``card35`` in the OLD ``initialize_layout``): the
"Is this a true alert?" select (``boton_confirmacion``) and the loss-driver
combobox (``comments_input``). The OLD tile also ran the CNN models from the
edit toolbar (``run_dl_model_1/2`` -> ``cnn_worker.apply_dl_model``); that model
execution is owned by Task 9.4 and is NOT wired here.

For the FOUNDATION this renders the *static* confirmation card with the question
select + driver combobox present but with NO behavior wired (the OLD save flow
read ``boton_confirmacion.v_model`` / ``comments_input.v_model`` — that lives in
AlertExport / Task 9.5).

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch (the CNN run reads selected images from here).
app_state : AppState
    Navigation + alerts source of truth.
analyzed_alerts_model : AlertAnalysisModel
    Holds ``model1_prediction_file`` / ``model2_prediction_file`` (CNN outputs).
app_tile_model : AppTileModel
    ``recipe_folder_path`` for prediction output paths.
"""

import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm


@solara.component
def AlertCnnPanel(
    widgets,
    app_state,
    analyzed_alerts_model,
    app_tile_model,
):
    """STUB. Renders the static confirmation + loss-driver card (no behavior)."""
    confirmation_select = solara.use_memo(
        lambda: sw.Select(
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
        ),
        dependencies=[],
    )
    driver_combobox = solara.use_memo(
        lambda: sw.Combobox(
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
        ),
        dependencies=[],
    )

    # Stash on widgets so the future AlertExport (Task 9.5) can read the user's
    # confirmation + driver selections when saving the alert attributes.
    def _publish():
        widgets.confirmation_select = confirmation_select
        widgets.driver_combobox = driver_combobox

    solara.use_effect(_publish, dependencies=[])

    card = solara.use_memo(
        lambda: v.Card(
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
        ),
        dependencies=[],
    )
    # TODO(Task 9.4 AlertCnnPanel): wire run_dl_model_1/2 (cnn_worker.apply_dl_model
    # with a FILE-PATH model arg, e.g. "/tmp/Model1.h5") + verify/add/remove
    # prediction handlers.
    solara.display(card)
