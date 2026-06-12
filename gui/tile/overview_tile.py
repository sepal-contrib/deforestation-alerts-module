"""Overview tile as a Solara component (Phase 4 port of the old ipyvuetify class).

Architecture layer: view — imports solara and ipyvuetify (via reacton) for UI only.
No GEE calls here; map-layer updates use pure ipyleaflet/pysepal APIs on the
**single shared** ``SepalMap`` passed in from ``solara_app.Page`` (the same map
``MapApp`` renders fullscreen). This tile does NOT create its own map: per the
pysepal single-map model every tile draws on the one shared map and renders its
controls as a right-panel section / map popups (see the migration memory).

Alert rendering matches the ORIGINAL Voila tile: one ``MarkerCluster`` per alert
status (``AwesomeIcon`` markers with Start/End/ID + "Go" popups) plus a
``MenuControl`` of per-status toggle checkboxes — built by
``overview_helper.build_status_marker_clusters`` and drawn on the single shared
map.

Layer ownership on the shared map: the tile only adds/removes its OWN layers — the
status ``MarkerCluster`` layers (named ``overview-cluster-<status>``) and its
``MenuControl``. It must never call ``map_.remove_all()`` (that would wipe other
tiles' layers + the basemap); ``_clear_overview_layers`` removes just this tile's
clusters (tracked handles + a name-prefix sweep) and its menu control before each
refresh and whenever the tile is deactivated (single-map per-tile isolation).

AOI context layer: the AOI tile draws its AOI outline (layer name ``"aoi"``) only
while it is active and REMOVES it on deactivation, so the AOI is gone by the time
the user reaches the Overview. To show alerts in their AOI context, this tile
draws the AOI too — but on its OWN layer name (``_OVERVIEW_AOI_LAYER`` =
``"overview-aoi"``), never the AOI tile's ``"aoi"``, so the two tiles can't race
on a shared layer (same per-tile ownership rule as the ``overview-cluster-*``
clusters). It reuses the AOI tile's ``map_.add_ee_layer`` rendering call (a map
draw of an already-built ``feature_collection`` — no new GEE *computation* in the
view; AoiView is built ``gee=True`` throughout this app).

Scope note: the old tile also drew the forest-mask / aux / drawn-selection layers,
which were `ee.*` calls in the view; those remain omitted. The alert-DB download
button is rendered in the right-panel card.

Known service-layer concern: overview_helper.py (a *service* module) still imports
ipyvuetify for its `create_table_rows` / `create_markers_ipyvuetify` /
`build_status_marker_clusters` helpers.
- `calculate_alert_classes` is pure (no UI imports) — used directly here.
- `create_table_rows` uses `v.Html` — reproduced locally as `_make_summary_rows` to
  avoid pulling ipyvuetify widgets through the service layer into this component.
  The helper version is left untouched; refactoring overview_helper is deferred to a
  later task.
- `create_markers_ipyvuetify` / `build_status_marker_clusters` use `v.*` for
  ipyleaflet marker popups + the menu-control card only (never Solara reacton
  children), so calling them here is safe.
"""

import logging
import os

import ipyvuetify as v
import reacton.ipyvuetify as rv
import solara
from ipyleaflet import MarkerCluster, Popup
from pysepal import sepalwidgets as sw

from deforestation_alerts.message import cm
from deforestation_alerts.scripts.overview_helper import (
    build_status_marker_clusters,
    calculate_alert_classes,
    create_markers_ipyvuetify,
)
from deforestation_alerts.scripts.alert_export import export_alerts_gdf

# MarkerCluster layers added by this tile are named with this prefix so a
# name-prefix sweep can catch any stale ones (e.g. a render_fixed double-mount)
# that the tracked handles missed.
_CLUSTER_NAME_PREFIX = "overview-cluster"

# AOI outline layer this tile owns on the shared map (distinct from the AOI tile's
# "aoi" layer so the two tiles never fight over a shared name).
_OVERVIEW_AOI_LAYER = "overview-aoi"

logger = logging.getLogger("SEPALUI.deforestation_alerts.overview")


def _bind_export(btn, fmt, gdf, folder, sources, start, end):
    """Return an ipyvuetify click handler that writes ``fmt`` and points ``btn``
    at the produced file. Generates the export on demand from the in-memory gdf."""
    def _on_click(widget, event, data):
        try:
            path = export_alerts_gdf(gdf, folder, fmt, sources, start, end)
            btn.set_url(path=path)
        except Exception:
            logger.exception("Overview: export %s failed", fmt)
    return _on_click

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


def _clear_overview_layers(map_, holder) -> None:
    """Remove only THIS tile's layers + control from the shared map.

    The overview owns the status ``MarkerCluster`` layers and its ``MenuControl``.
    Everything else on the shared map (basemap, AOI, alert rasters from other
    tiles) is left untouched. Never use ``map_.remove_all()`` on the shared map.

    ``holder`` is a session-stable dict (``{"clusters": [...], "menu": ...}``)
    tracking the widgets this tile added; we remove those plus any stale cluster
    found by the ``_CLUSTER_NAME_PREFIX`` name sweep (covers a render_fixed
    double-mount), then reset the holder.
    """
    menu = holder.get("menu")
    if menu is not None:
        removed = False
        for remover in ("remove_control", "remove"):
            try:
                getattr(map_, remover)(menu)
                removed = True
                break
            except Exception:
                continue
        if not removed:
            logger.warning("Overview: could not remove the marker menu control")
        holder["menu"] = None

    tracked = list(holder.get("clusters", []))
    holder["clusters"] = []
    by_name = [
        layer
        for layer in list(map_.layers)
        if isinstance(layer, MarkerCluster)
        and str(getattr(layer, "name", "")).startswith(_CLUSTER_NAME_PREFIX)
    ]
    # also drop any stray click Popups the markers may have left on the map
    stray_popups = [layer for layer in list(map_.layers) if isinstance(layer, Popup)]
    for layer in set(tracked) | set(by_name) | set(stray_popups):
        try:
            if layer in map_.layers:
                map_.remove_layer(layer)
        except Exception:
            logger.exception("Overview: failed to remove a layer during clear")


def sync_overview_aoi_layer(map_, feature_collection, *, show) -> None:
    """Add/remove the Overview's own AOI outline on the shared map (idempotent).

    Mirrors the AOI tile's ``_redraw_aoi_layer`` / ``_clear_aoi_layer`` but on the
    tile-owned ``_OVERVIEW_AOI_LAYER`` name so it never touches the AOI tile's
    ``"aoi"`` layer. Always removes first (so a stale outline / render_fixed
    double-mount can't leave two), then re-adds when ``show`` is True and an AOI is
    selected. ``feature_collection`` is the AOI ``ee`` object (AoiView is
    ``gee=True`` here), drawn with ``add_ee_layer`` exactly as the AOI tile does.
    """
    try:
        map_.remove_layer(_OVERVIEW_AOI_LAYER, none_ok=True)
    except Exception:
        logger.exception("Overview: failed to remove the AOI layer")
    if not show or feature_collection is None:
        return
    try:
        map_.add_ee_layer(feature_collection, {}, _OVERVIEW_AOI_LAYER)
    except Exception:
        logger.exception("Overview: failed to add the AOI layer")


@solara.component
def OverviewTile(app_state, map_, aoi_date_model=None, selected_alerts_model=None):
    """Solara port of the old OverviewTile ipyvuetify widget.

    Renders the alert-status summary card (+ download button) as a right-panel
    section; the status marker clusters (+ a toggle menu control) and their "Go"
    popups are drawn on the single shared map.

    Parameters
    ----------
    app_state : AppState
        The shared ephemeral reactive store.  The component reacts to
        ``app_state.alerts_gdf`` via a ``use_effect``.
    map_ : SepalMap
        The single shared map (the same instance ``MapApp`` renders fullscreen).
        Layers + popups are added/removed on this map.
    aoi_date_model : AoiDateModel, optional
        The recipe AOI model; its ``feature_collection`` is drawn as the Overview's
        AOI-context outline while this tile is active. ``None`` (or no AOI selected)
        simply skips the outline.
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

    # Session-stable handles for the cluster layers + menu control this tile adds
    # to the shared map, so the effect can remove exactly its own widgets on
    # refresh / deactivation (never the other tiles' layers).
    overview_layers = solara.use_memo(
        lambda: {"clusters": [], "menu": None}, dependencies=[]
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
    # points only while it is the active map tile, and clears them otherwise (so
    # they don't bleed into AOI/Filter). Keyed on active_map_view (NOT
    # current_page_view) so opening a popup over the map (e.g. clicking a marker's
    # "Go" -> the Analysis dialog) leaves the points drawn underneath. Read
    # reactively so the effect re-runs on navigation between right-panel tiles.
    active = app_state.active_map_view.value == "overview_tile"

    # Callback: "Go" button in a marker popup → navigate to that alert. Thin: read
    # widget value → write reactive state. Defined before _sync_overview because
    # the effect hands it to create_markers_ipyvuetify.
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
        the data; the status marker clusters + their menu control are drawn on the
        shared map ONLY while this tile is active, and cleared otherwise
        (single-map isolation).
        """
        has_data = alerts_gdf is not None and len(alerts_gdf) > 0
        if has_data:
            counts = calculate_alert_classes(
                alerts_gdf, "Confirmed", "False Positive", "Need revision"
            )
            tbody_ref.children = _make_summary_rows(counts, _ALERT_LABELS)
        else:
            tbody_ref.children = _make_summary_rows([0, 0, 0, 0, 0], _ALERT_LABELS)

        # Always start by removing only THIS tile's layers + control (idempotent).
        try:
            _clear_overview_layers(map_, overview_layers)
            if active and has_data:
                # One MarkerCluster per status (AwesomeIcon markers + Start/End/ID
                # "Go" popups) + a MenuControl status toggle — the original tile's
                # UX, on the shared map. Markers carry point locations + their own
                # popups, so (unlike GeoData) no geometry column is JSON-serialized.
                markers = create_markers_ipyvuetify(
                    alerts_gdf,
                    "point",
                    ["alert_date_min", "alert_date_max"],
                    "status",
                    _on_go_click,
                )
                clusters, menu = build_status_marker_clusters(
                    map_, markers, name_prefix=_CLUSTER_NAME_PREFIX
                )
                overview_layers["clusters"] = clusters
                overview_layers["menu"] = menu
        except Exception:
            logger.exception("Overview: failed to refresh map layers")

    solara.use_effect(_sync_overview, [alerts_gdf, active])

    # AOI-context outline: draw the AOI on the shared map while the Overview is
    # active, remove it otherwise. Keyed on `active` ONLY (not alerts_gdf) so a
    # filter run elsewhere can't make this effect strip the outline while another
    # tile is showing — the AOI is read at activation time (it is selected long
    # before the user reaches the Overview), mirroring the AOI tile's _sync_aoi.
    aoi_fc = getattr(aoi_date_model, "feature_collection", None)

    def _sync_overview_aoi():
        sync_overview_aoi_layer(map_, aoi_fc, show=active)

    solara.use_effect(_sync_overview_aoi, [active])

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
        # Export menu (replaces the old single-CSV download button). Shown only
        # when a recipe is loaded AND there are alerts. Each format is generated
        # on demand from the in-memory gdf into the recipe folder, named with
        # source + date range. The canonical store is parquet; these are exports.
        gdf = app_state.alerts_gdf.value
        if recipe_folder and gdf is not None and len(gdf) > 0:
            sources = getattr(selected_alerts_model, "selected_alert_sources", None) or []
            start = getattr(aoi_date_model, "start_date", "") or ""
            end = getattr(aoi_date_model, "end_date", "") or ""
            export_buttons = []
            for fmt in ("csv", "gpkg", "geojson"):
                btn = sw.DownloadBtn(
                    text=getattr(cm.overview_tile, f"export_{fmt}"), small=True
                )
                btn.on_event(
                    "click",
                    _bind_export(btn, fmt, gdf, recipe_folder, sources, start, end),
                )
                export_buttons.append(btn)
            export_card = v.Card(
                class_="pa-3 ma-3",
                hover=True,
                children=[
                    v.CardTitle(class_="pa-1", children=[cm.overview_tile.export_label]),
                    v.Row(class_="d-flex justify-center", children=export_buttons),
                ],
            )
            solara.display(export_card)
