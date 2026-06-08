"""Overview tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara and ipyvuetify (via reacton) for UI only.
No GEE calls here; map-layer updates use pure ipyleaflet/pysepal APIs on the
**single shared** ``SepalMap`` passed in from ``solara_app.Page`` (the same map
``MapApp`` renders fullscreen). This tile does NOT create its own map: per the
pysepal single-map model every tile draws on the one shared map and renders its
controls as a right-panel section / map popups (see the migration memory).

Layer ownership on the shared map: the tile only adds/removes its OWN layers —
the colored alert-status ``GeoData`` layers (named by ``_COLOR_DICT`` key) and the
click ``Popup``s. It must never call ``map_.remove_all()`` (that would wipe other
tiles' layers + the basemap); ``_clear_overview_layers`` removes just this tile's
layers by name before each refresh.

Scope note: the old tile also drew the AOI / forest-mask / aux / drawn-selection
layers, which were `ee.*` calls in the view. Those are intentionally omitted from
this tile (GEE in the view layer is disallowed); the Overview shows only the
colored alert-status layers. The alert-DB download button is rendered in the
right-panel card.

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
from ipyleaflet import Popup
from pysepal import sepalwidgets as sw

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


def _clear_overview_layers(map_) -> None:
    """Remove only THIS tile's layers from the shared map.

    The overview owns the colored alert-status ``GeoData`` layers (named by a
    ``_COLOR_DICT`` key) and any click ``Popup``s. Everything else on the shared
    map (basemap, AOI, alert rasters from other tiles) is left untouched. Never
    use ``map_.remove_all()`` on the shared map.
    """
    for layer in list(map_.layers):
        if getattr(layer, "base", False):
            continue
        if isinstance(layer, Popup) or getattr(layer, "name", None) in _COLOR_DICT:
            try:
                map_.remove_layer(layer)
            except Exception:
                logger.exception("Overview: failed to remove a layer during clear")


@solara.component
def OverviewTile(app_state, map_):
    """Solara port of the old OverviewTile ipyvuetify widget.

    Renders the alert-status summary card (+ download button) as a right-panel
    section; the colored alert-status points + their "Go" popups are drawn on the
    single shared map.

    Parameters
    ----------
    app_state : AppState
        The shared ephemeral reactive store.  The component reacts to
        ``app_state.alerts_gdf`` via a ``use_effect``.
    map_ : SepalMap
        The single shared map (the same instance ``MapApp`` renders fullscreen).
        Layers + popups are added/removed on this map.
    """
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
    # Single shared map -> per-tile layer isolation: this tile draws its alert
    # points only while it is the active tile, and clears them otherwise (so they
    # don't bleed into AOI/Filter). Read reactively so the effect re-runs on
    # navigation.
    active = app_state.current_page_view.value == "overview_tile"

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
        """Refresh the summary table + this tile's map layers.

        Runs on mount and whenever ``alerts_gdf`` changes OR this tile is
        activated/deactivated. The summary table (right-panel only) always tracks
        the data; the colored alert points are drawn on the shared map ONLY while
        this tile is active, and cleared otherwise (single-map isolation).
        """
        has_data = alerts_gdf is not None and len(alerts_gdf) > 0
        if has_data:
            counts = calculate_alert_classes(
                alerts_gdf, "Confirmed", "False Positive", "Need revision"
            )
            tbody_ref.children = _make_summary_rows(counts, _ALERT_LABELS)
        else:
            tbody_ref.children = _make_summary_rows([0, 0, 0, 0, 0], _ALERT_LABELS)

        # Always start by removing only THIS tile's layers (idempotent, by name).
        try:
            _clear_overview_layers(map_)
            if active and has_data:
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
                    map_,
                    _on_go_click,
                )
        except Exception:
            logger.exception("Overview: failed to refresh map layers")

    solara.use_effect(_sync_overview, [alerts_gdf, active])

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
    # Render layout — controls ONLY (this widget IS the right-panel section
    # content). No map here: the single shared map shows the alert points.
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

    with solara.Column(style="padding: 0;"):
        solara.display(info_card)
        # Download-alerts-DB button (old tile's dwn_all_btn). Shown only when a
        # recipe is loaded (recipe_folder_path set by RecipeTile) AND the saved
        # alert_db.csv exists. Built declaratively (not memoized) so its URL
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
