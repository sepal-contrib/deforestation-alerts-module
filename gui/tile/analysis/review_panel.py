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

from gui.i18n import t
from gui.scripts import loss_drivers as drivers

#: The review statuses a Save stores, with the key of each one's label. The
#: select shows the labels in the language the widgets were built in; the
#: store always gets the status (Overview's counts and the PMTiles styles read it).
REVIEW_CHOICES = (
    ("Confirmed", "analysis_tile.questionarie.confirmation_yes"),
    ("False Positive", "analysis_tile.questionarie.confirmation_no"),
    ("Need revision", "analysis_tile.questionarie.confirmation_revision"),
)


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
        items=[{"text": t(key), "value": status} for status, key in REVIEW_CHOICES],
        v_model=REVIEW_CHOICES[0][0],
        small=True,
        multiple=False,
        clearable=True,
        chips=True,
    )
    # Codes as values (``loss_drivers.DRIVERS``), labels as text: the store gets
    # the codes, whatever the language the widgets were built in. Objects in
    # and out: Vuetify 2's combobox draws a chip from the selected value itself
    # (a bare code would read "agriculture_expansion"); typed text stays a string.
    driver_combobox = sw.Combobox(
        items=drivers.items(),
        v_model=drivers.selection(drivers.DEFAULT),
        return_object=True,
        label=t("analysis_tile.questionarie.loss_driver_hint"),
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
                children=[t("analysis_tile.questionarie.confirmation_q")],
            ),
            confirmation_select,
            v.CardTitle(
                class_="pa-1 ma-1",
                children=[t("analysis_tile.questionarie.loss_driver_label")],
            ),
            driver_combobox,
        ],
    )
    # CNN run + verify/add/remove prediction handlers live in AlertEditTools
    # (edit_tools.py), since the DL toolbars share the draw controls.
    return card
