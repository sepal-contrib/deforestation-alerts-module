"""Overview tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara and ipyvuetify (via reacton) for UI only.
No GEE calls here; map-layer updates use pure ipyleaflet/pysepal APIs on a memoized
SepalMap widget.

Scope note: the old tile also drew the AOI / forest-mask / aux / drawn-selection
layers, which were `ee.*` calls in the view. Those are intentionally omitted from
this map (GEE in the view layer is disallowed); the Overview map shows only the
colored alert-status layers. The alert-DB download button is likewise deferred
(see the TODO in the render layout).

Known service-layer concern: overview_helper.py (a *service* module) still imports
ipyvuetify for its `create_table_rows` and `add_colored_layers` helpers.
- `calculate_alert_classes` is pure (no UI imports) — used directly here.
- `create_table_rows` uses `v.Html` — reproduced locally as `_make_summary_rows` to
  avoid pulling ipyvuetify widgets through the service layer into this component.
  The helper version is left untouched; refactoring overview_helper is deferred to a
  later task.
- `add_colored_layers` uses `v.Html`/`v.Btn`/`v.Card` for ipyleaflet popup children
  only (they are never Solara reacton children), so calling it here is safe.
"""

import logging
import os

import ipyvuetify as v
import reacton.ipyvuetify as rv
import solara
from pysepal import sepalwidgets as sw
from pysepal.mapping import SepalMap

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.overview_helper import (
    add_colored_layers,
    calculate_alert_classes,
)

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")

# Alert status → map dot colour (same as old tile)
_COLOR_DICT = {
    "Not reviewed": "lightgrey",
    "Confirmed": "red",
    "Need revision": "orange",
    "False Positive": "green",
}

# Summary-table row labels (matches old tile order)
_ALERT_LABELS = [
    cm.overview_tile.total,
    cm.overview_tile.reviewed,
    cm.overview_tile.confirmed,
    cm.overview_tile.false_positives,
    cm.overview_tile.revision,
]


def _make_summary_rows(counts: list[int], labels: list[str]) -> list:
    """Return ipyvuetify <tr> widgets for the summary table.

    Local copy of the pure UI part of overview_helper.create_table_rows, kept
    here so the service module's ipyvuetify dependency does not leak into this
    component through a function call.  The service version is left unchanged.
    """
    return [
        v.Html(
            tag="tr",
            children=[
                v.Html(tag="td", children=[label]),
                v.Html(tag="td", children=[str(value)]),
            ],
        )
        for label, value in zip(labels, counts)
    ]


@solara.component
def OverviewTile(app_state):
    """Solara port of the old OverviewTile ipyvuetify widget.

    Parameters
    ----------
    app_state : AppState
        The shared ephemeral reactive store.  The component reacts to
        ``app_state.alerts_gdf`` via a ``use_effect``.
    """
    # --------------------------------------------------------------------------
    # Memoized map widget — one instance per kernel, stable across re-renders.
    # --------------------------------------------------------------------------
    map_widget = solara.use_memo(
        lambda: SepalMap(),
        dependencies=[],
    )

    # --------------------------------------------------------------------------
    # Summary table body — held in a Ref so use_effect can mutate it in place.
    # --------------------------------------------------------------------------
    tbody_ref = solara.use_memo(
        lambda: v.Html(
            tag="tbody",
            children=_make_summary_rows([0, 0, 0, 0, 0], _ALERT_LABELS),
        ),
        dependencies=[],
    )

    # --------------------------------------------------------------------------
    # Reactive effect: refresh map layers + summary table whenever alerts_gdf
    # changes.  Replaces the old `analyzed_alerts_model.observe(…, "alerts_gdf")`
    # and `analyzed_alerts_model.observe(…, "last_save_time")` observers.
    # --------------------------------------------------------------------------
    alerts_gdf = app_state.alerts_gdf.value
    # Folder of the loaded recipe (set by RecipeTile load). Read reactively so
    # the download button appears once a recipe is loaded.
    recipe_folder = app_state.recipe_folder_path.value

    # Callback: "Go" button in a map popup → navigate to that alert. Thin: read
    # widget value → write reactive state. Defined before _sync_overview because
    # the effect hands it to add_colored_layers.
    def _on_go_click(widget, event, data):
        try:
            app_state.actual_alert_id.value = int(widget.value)
            app_state.current_page_view.value = "analysis_tile"
        except Exception:
            logger.exception("Overview: on_go_click failed")

    def _sync_overview():
        """Called on mount and every time alerts_gdf changes."""
        if alerts_gdf is not None and len(alerts_gdf) > 0:
            # Update summary counts
            counts = calculate_alert_classes(
                alerts_gdf, "Confirmed", "False Positive", "Need revision"
            )
            tbody_ref.children = _make_summary_rows(counts, _ALERT_LABELS)

            # Refresh map layers
            try:
                map_widget.remove_all()
                map_widget.add_basemap("SATELLITE")
                gdf_points = alerts_gdf.drop(
                    columns=[
                        c
                        for c in ["bounding_box", "alert_polygon"]
                        if c in alerts_gdf.columns
                    ]
                ).set_geometry("point")
                add_colored_layers(
                    gdf_points,
                    "status",
                    _COLOR_DICT,
                    map_widget,
                    _on_go_click,
                )
            except Exception:
                logger.exception("Overview: failed to update map layers")
        else:
            # No data: reset table to zeros, clear map
            tbody_ref.children = _make_summary_rows([0, 0, 0, 0, 0], _ALERT_LABELS)
            try:
                map_widget.remove_all()
                map_widget.add_basemap("SATELLITE")
            except Exception:
                logger.exception("Overview: failed to clear map")

    solara.use_effect(_sync_overview, [alerts_gdf])

    # --------------------------------------------------------------------------
    # Info table (stable ipyvuetify widget; tbody_ref is mutated by use_effect)
    # --------------------------------------------------------------------------
    # NOTE: raw ipyvuetify `v.SimpleTable` (not reacton `rv.*`) on purpose — the
    # effect mutates `tbody_ref.children` in place, which requires a concrete
    # widget instance. Do not "simplify" this to `rv.SimpleTable`: a reacton
    # element is rebuilt each render and can't be imperatively mutated.
    info_table = solara.use_memo(
        lambda: v.SimpleTable(
            dense=False,
            children=[tbody_ref],
        ),
        dependencies=[],
    )

    # --------------------------------------------------------------------------
    # Render layout (declarative, no .show()/.hide())
    # --------------------------------------------------------------------------
    section_title = rv.CardTitle(
        class_="pa-1 ma-1",
        children=[cm.overview_tile.title],
    )

    info_card = rv.Card(
        class_="pa-3 ma-3",
        hover=True,
        children=[section_title, info_table],
    )

    with solara.Row(style="height: 100%; gap: 0;"):
        # Left: map (fills remaining space)
        with solara.Column(style="flex: 1 1 auto; min-width: 0; overflow: hidden;"):
            solara.display(map_widget)

        # Right: stats panel
        with solara.Column(
            style="flex: 0 0 18rem; overflow: auto; padding: 0;"
        ):
            solara.display(info_card)
            # Download-alerts-DB button (old tile's dwn_all_btn). Shown only when a
            # recipe is loaded (recipe_folder_path set by RecipeTile) AND the saved
            # alert_db.csv exists. recipe_folder_path is now wired through app_state
            # by the Recipe load task. Built declaratively (not memoized) so its URL
            # reflects the current folder; sw.DownloadBtn.set_url() builds the link.
            alert_db_path = (
                os.path.join(recipe_folder, "alert_db.csv") if recipe_folder else ""
            )
            if alert_db_path and os.path.exists(alert_db_path):
                download_btn = sw.DownloadBtn(
                    text=cm.overview_tile.alerts_db_label, small=True
                )
                download_btn.set_url(path=alert_db_path)
                download_card = v.Card(
                    class_="pa-3 ma-3 d-flex justify-center",
                    hover=True,
                    children=[download_btn],
                )
                solara.display(download_card)
