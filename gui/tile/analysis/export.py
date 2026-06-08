"""AlertExport sub-component STUB (Task 9.5).

Architecture layer: view. Ports the OLD ``AnalysisTile`` export card (``card06`` +
``label35``/``toolBarSaveExport``/``toolBarDownloads``): the Save button (which in
the OLD tile ran ``save_attributes_to_gdf`` — writing the user's confirmation /
driver / image-info / admin attributes onto ``alerts_gdf`` and persisting
``alert_db.csv``), the "Download alert data" button (``download_data`` — builds
the gpkg/zip + the Word report), and the two download buttons.

For the FOUNDATION this renders the static export card (Save + Download buttons
present, DISABLED / no-op) so the layout slot + contract exist; Task 9.5 wires the
save + report/download behavior.

Props
-----
widgets : AnalysisWidgets
    Shared heavy widgets + scratch (selected image info lists, actual_bb).
app_state : AppState
    Navigation + alerts source of truth (``alerts_gdf`` / ``actual_alert_id``).
aux_model : AuxModel
    ``custom_report_template`` for the report generation.
analyzed_alerts_model : AlertAnalysisModel
    ``defo_dl_layer`` / ``last_save_time``.
app_tile_model : AppTileModel
    ``recipe_folder_path`` for output paths + ``alert_db.csv``.
"""

import ipyvuetify as v
import solara
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm


@solara.component
def AlertExport(
    widgets,
    app_state,
    aux_model,
    analyzed_alerts_model,
    app_tile_model,
):
    """STUB. Renders the static export card (Save + Download buttons, no behavior)."""

    def _build():
        save_btn = sw.Btn(
            msg=cm.analysis_tile.export_labels.save_btn,
            color="primary",
            outlined=True,
            disabled=True,
            class_="pa-1 ma-1",
        )
        download_btn = sw.Btn(
            msg=cm.analysis_tile.export_labels.download_alert_data_btn,
            color="primary",
            outlined=True,
            disabled=True,
            class_="pa-1 ma-1",
        )
        toolbar = v.Toolbar(
            class_="pa-1 ma-1 d-flex justify-space-between",
            children=[save_btn, download_btn],
        )
        return v.Card(
            class_="pa-2 ma-2",
            hover=True,
            children=[
                v.CardTitle(
                    class_="pa-1 ma-1",
                    children=[cm.analysis_tile.export_labels.export_title],
                ),
                toolbar,
            ],
        )

    card = solara.use_memo(_build, dependencies=[])
    # TODO(Task 9.5 AlertExport): wire save_attributes_to_gdf (status/driver/
    # image-info/admin -> alerts_gdf + alert_db.csv) and download_data (gpkg/zip +
    # Word report via report_builder).
    solara.display(card)
