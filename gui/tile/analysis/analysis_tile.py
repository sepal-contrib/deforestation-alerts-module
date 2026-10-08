"""Analysis tile parent — raw ipyvuetify layout builder (Phase-4 Solara port).

Architecture layer: view (raw-widget builder, no ``@solara.component``). This is
the parent slice of porting the 2052-line ipyvuetify ``AnalysisTile`` god-class.
It establishes:

  * the shared heavy widgets (``AnalysisWidgets``, built once),
  * the two-map left panel + control right panel layout (ports OLD
    ``initialize_layout``), as a raw ipyvuetify ``v.Container`` tree,
  * the per-alert orchestration ``view_actual_alert`` registered as a
    ``app_state.actual_alert_id.subscribe`` (replacing the former ``use_effect``)
    plus one immediate call so a pre-selected alert renders on first build,
  * the right-panel review console (``build_controls``: the head, block A,
    block B, the Questions and Refine sections and the sticky footer that
    holds the Export section and the Save buttons),
  * and the sub-component builders (imagery / review / edit / export).

WHY A RAW BUILDER (not a ``@solara.component`` + ``render_fixed`` /
``solara.display``): MapApp renders the Analysis step content with ``v-if``, so it
DESTROYS the step DOM on close and REBUILDS it on reopen. A ``@solara.component``
rendered via ``render_fixed`` + ``solara.display`` goes blank on reopen, but a raw
ipyvuetify widget tree is re-viewed from its model on reopen, so it survives.
``build_analysis_layout`` builds the tree once and RETURNS ``(stage, controls)``
— the step content container and the right-panel controls — same pattern
as the sibling builders (``context`` / ``review_panel`` / ``build_edit_tools`` /
``build_export`` / ``build_timeline``) and ``recipe_tile.build_recipe_layout`` /
``aux_tile.build_aux_layout``. The CSS the OLD component injected via
``solara.Style`` (a hook) is now the first child of the returned container as a raw
``v.Html(tag="style", ...)`` element. The two former ``use_effect``s become
``.subscribe`` registrations on ``app_state.actual_alert_id`` /
``app_state.alerts_gdf`` (each also invoked once immediately).

Key architectural decisions (FIXED by the migration contract):

* **``app_state`` (AppState) is the source of truth** for the alerts table +
  navigation, NOT the traitlets model. The Filter tile publishes the assembled
  alerts GeoDataFrame to ``app_state.alerts_gdf.value`` and the user index lives
  in ``app_state.actual_alert_id.value``. So this tile reads alerts from
  ``app_state.alerts_gdf.value``, the current index from
  ``app_state.actual_alert_id.value``, and navigates via
  ``app_state.go_to/go_next/go_prev``. The OLD ``create_gdf_partial/full`` (which
  built ``analyzed_alerts_model.alerts_gdf`` from the selected-alerts traits) is
  superseded by that publish and is NOT ported (see TODO(recipe-load) below for
  the deferred re-discovery/merge path).
* **``analysis`` (``AnalysisState``, spec §10)** holds the per-session review
  state as Solara reactives: the chosen images and their info, the drawn defo
  layer, the CNN predictions, ``last_save_time``. ``view_actual_alert`` resets
  it on every alert change (``reset_alert``); the shared timeline then publishes
  the chosen images into it and ``imagery.wire_panes`` draws them.
* **Heavy widgets are imperative + memoized once.** The two maps / draw controls /
  AOI control live on ``AnalysisWidgets`` and are mutated in
  place by ``view_actual_alert``; the per-alert effect is keyed on the alert id
  reactive so it re-runs on navigation. Guarded to no-op when no alerts are loaded
  (safe at smoke time + idempotent under render_fixed double-mount).

Deferred (TODO-marked): the recipe-LOAD re-discovery/merge path for previously
reviewed alerts. The imagery is no longer deferred: the two per-pane selectors
and their per-alert Earth Engine fetches are retired, replaced by the one shared
timeline (``gui/tile/analysis/timeline.py``) over the project's cached scene
index, with ``imagery.wire_panes`` rebuilding a pane from the picked row's
granule keys.
"""

import logging
from datetime import date
from types import SimpleNamespace

import ee
import ipyvuetify as v
from ipyleaflet import GeoJSON

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.project import paths
from deforestation_alerts.project.questions import column
from deforestation_alerts.scripts.mosaics_helper import (
    convert_julian_to_date,
    multipolygon_to_geodataframe,
)
from gui.tile.analysis.context import build_context
from gui.tile.analysis.earlier import build_earlier_controls, refresh_earlier_reviews
from gui.tile.analysis.questions_panel import build_questions_section
from gui.tile.analysis.review_panel import build_review_panel
from gui.tile.analysis.edit_tools import build_edit_tools
from gui.tile.analysis.export import REQUIRED_MISSING, build_export
from gui.tile.analysis.footer import build_footer
from gui.tile.analysis.imagery import wire_panes
from gui.tile.analysis.section import build_section
from gui.tile.analysis.timeline import build_timeline
from gui.tile.analysis import vis_sync
from gui.i18n import t
from gui.scripts import questions
from gui.scripts import set_store
from gui.store.workflow_steps import active_set
from gui.tile.analysis.widgets import AnalysisWidgets, info_button
from gui.widget.set_selector import build_set_selector

logger = logging.getLogger("SEPALUI.deforestation_alerts.analysis")

# Alert bounding-box overlay style (OLD view_actual_alert geojson_layer).
_ALERT_BB_STYLE = {
    "color": "yellow",
    "fillColor": "#3366cc",
    "opacity": 0.5,
    "weight": 1,
    "dashArray": "2",
    "fillOpacity": 0,
}


def _as_date(julian):
    """An alert's Julian date as a ``date``, or ``None``.

    ``vectorize`` writes NaN when a polygon's date pixels are all nodata, and
    ``convert_julian_to_date`` renders that "N/A"; the timeline needs the
    absence itself so it can fall back to the period's window.
    """
    text = convert_julian_to_date(julian)
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _saved_pick(value):
    """A stored ``before_img`` / ``after_img`` as text, or ``None`` (NaN, a
    missing column): the timeline parses it (``scene_query.saved_pick_id``)."""
    return value if isinstance(value, str) and value.strip() else None


def _invalidate_alert_view(widgets, analysis) -> None:
    """Forget the alert that was under review, without a new one to show.

    What ``view_actual_alert`` does for an alert change, less the part that
    needs an alert: the per-alert review state (``reset_alert``, whose token
    bump also makes every worker in flight drop its result and takes the
    export section back to "available after save"), the imagery context the
    shared timeline draws from, the drawn-polygon controls, the alert layers
    on both maps, and the ee bbox and covering grid an export is built from.

    The visualization layers stay, as they do on an ordinary alert change
    (spec §10, ``vis_sync``). Everything after the state is best-effort: a
    map stand-in without layers must not cost the invalidation itself.
    """
    analysis.reset_alert()
    try:
        ctx = getattr(widgets, "imagery_ctx", None)
        if ctx is not None:
            ctx.value = None
        for name in ("draw_before", "draw_after"):
            control = getattr(widgets, name, None)
            if control is not None:
                control.hide()
        for name in ("map_before", "map_after"):
            a_map = getattr(widgets, name, None)
            if a_map is not None:
                vis_sync.clear_alert_layers(a_map)
        widgets.actual_bb = None
        widgets.actual_alert_grid = None
    except Exception:
        logger.exception("Analysis: clearing the alert view failed")


def view_actual_alert(
    widgets, app_state, analysis, app_tile_model, id_field
):
    """Per-alert orchestration owned by the foundation.

    Ports the foundation-relevant parts of the OLD ``view_actual_alert``: select
    the alert row, refresh the nav id field, forget the
    previous alert's review state (``analysis.reset_alert``), rebuild the bbox overlay on both maps, center/zoom, compute
    the ee bbox + covering grid (stored on ``widgets``), register the AOI, set the
    default image-collection flags, and remember the position on the active
    alert set (``SetContext.remember_position``; spec §10 replaces the recipe
    json's ``actual_alert_id``).

    Guards: returns early when no alerts are loaded, when they are another
    set's than the active one (``context.facts_of`` applies the same guard to
    the facts grid), or the id is out of range — so it is safe at smoke time
    (no EE/imagery work runs) and idempotent under the render_fixed
    double-mount. Early, but not silently: each of those cases means there is
    no alert under review, and ``_invalidate_alert_view`` says so to the
    state, the maps and the timeline. The heavy imagery loading + slide-group rebuilds are
    deferred to Task 9.2 (AlertImagery).
    """
    # Both guards below leave the panel's NUMBERS alone — the jump field and
    # block A's facts grid keep what they show, the same shape as
    # ``context.facts_of`` returning None (there is a test for it) — rather
    # than blanking and re-filling on every set switch. What they no longer
    # leave alone is the alert-scoped STATE: an earlier round of this branch
    # returned here outright, and that kept the previous alert's token
    # current, so a CNN run or an export still in flight passed
    # ``is_current`` and applied onto a panel whose alert was gone; the
    # imagery context still described that alert, and the ee bbox and
    # covering grid an export builds from were still its own.
    # ``_invalidate_alert_view`` is what an alert change does minus the
    # arrival of a new alert, so the window shrinks to "the panel's numbers",
    # which the next publish replaces.
    gdf = app_state.alerts_gdf.value
    s = active_set(app_state.project.value)
    if s is not None and not set_store.alerts_belong_to(gdf, s):
        # Another set's alerts, until the new set's are published.
        _invalidate_alert_view(widgets, analysis)
        return None
    alert_id = app_state.actual_alert_id.value

    if gdf is None or alert_id is None or alert_id < 0 or alert_id >= len(gdf):
        # No valid selection at all: a project switched or closed (both drop
        # the alerts and select nothing, ``AppState._install``), a context
        # with no alerts, or the moment before ``select_start_alert`` picks
        # one.
        _invalidate_alert_view(widgets, analysis)
        return

    alerta = gdf.iloc[alert_id]

    # --- sync the jump field with the 1-based number the queue counter shows
    # (block A's ``_build_nav`` converts it back on the way out) ---
    id_field.v_model = alert_id + 1

    # --- forget the previous alert's drawn layer, chosen images and CNN
    # predictions: none of them may survive an alert change (the images are
    # chosen again by the imagery panes below) ---
    analysis.reset_alert()

    # --- a reviewed alert opens on its stored verdict; an unreviewed one on no
    # answer at all, so a default is never saved. After reset_alert, which
    # clears it; the seeding is not a change, so ``dirty`` stays False. ---
    stored = alerta.get("status")
    analysis.verdict.set(stored if isinstance(stored, str) and stored != "Not reviewed" else None)
    # --- a reviewed alert opens on its stored answers (codes, read back; a
    # re-save stores them again as codes), an unreviewed one on none (spec
    # U13: no default, so "required" means something). A store without a
    # question's column -- one added after the set was made -- reads as no
    # answer (``alerta.get`` gives None, ``codes`` of None is []). ---
    p = app_state.project.value
    reviewed = analysis.verdict.value is not None
    analysis.answers.set({q.key: questions.codes(q, alerta.get(column(q))) for q in p.questions}
                         if reviewed and p is not None else {})
    analysis.dirty.set(False)

    # --- remember the position on the alert set (auto-saved with the project) ---
    remember = getattr(app_tile_model, "remember_position", None)
    if remember is not None:
        try:
            remember(alert_id)
        except Exception:
            logger.exception("Analysis: failed to remember the alert position")

    # ------------------------------------------------------------------
    # EE-touching map work. Wrapped so a transient EE failure (or a malformed
    # geometry) doesn't break navigation; with no alerts loaded we already
    # returned above, so this only runs for a real alert.
    # ------------------------------------------------------------------
    try:
        alerta_bb_geojson = alerta["bounding_box"].__geo_interface__

        # hide the draw controls between alerts (Task 9.3 shows them on edit)
        widgets.draw_before.hide()
        widgets.draw_after.hide()

        # clear the previous alert's layers (the visualization layers stay:
        # vis_sync keeps them, spec §10) BEFORE the view moves: a layer left
        # on would fetch tiles at the new view, from the previous set's
        # raster, which a period delete is removing (500s from the tile server)
        vis_sync.clear_alert_layers(widgets.map_before)
        vis_sync.clear_alert_layers(widgets.map_after)

        # center/zoom (the two maps are jslink'd; write both explicitly)
        widgets.set_synced_view((alerta.point.y, alerta.point.x), 16)

        # rebuild the bbox overlay on both maps
        geojson_layer = GeoJSON(
            data=alerta_bb_geojson, style=_ALERT_BB_STYLE, name="Alert BB"
        )
        widgets.map_before.add_layer(geojson_layer)
        widgets.map_after.add_layer(geojson_layer)

        # re-add the saved defo polygon if this alert was already reviewed
        if (
            alerta["status"] in {"Confirmed", "Need revision"}
            and alerta["alert_polygon"] is not None
        ):
            analysis.defo_layer.set(multipolygon_to_geodataframe(
                alerta["alert_polygon"]
            ).set_crs(epsg="4326", allow_override=True, inplace=True))

        # ee bbox (buffer + bounds) + covering grid for downloads
        alerta_bb_geojson_ee = ee.Feature(alerta_bb_geojson).buffer(100, 1).bounds(1)
        widgets.aoi_control.add_aoi("AOI", alerta_bb_geojson_ee)
        widgets.actual_bb = alerta_bb_geojson_ee

        grid_descarga = alerta_bb_geojson_ee.geometry().coveringGrid(
            "EPSG:3857", 1 * 256 * 4.77
        )
        widgets.actual_alert_grid = grid_descarga.geometry().bounds(1)

        # Publish the per-alert imagery context: the one thing an alert
        # change has to do for the shared timeline, which answers the alert
        # from the project-level scene index with no EE round trip
        # (``timeline.render``). The bbox and dates computed above are passed
        # through so the timeline never re-touches this thread's state. The
        # Planet-first / S2-fallback load order the retired per-pane selectors
        # branched on is gone with them: the timeline offers every collection
        # at once and ``default_pick`` chooses by acquisition date.
        widgets.imagery_ctx.value = {
            "alert_id": alert_id,
            # the alert the imagery is fetched for: a rebuild that ends after
            # the next alert change drops its result (``AnalysisState.alert_token``)
            "token": analysis.alert_token.value,
            # the alert's own bounding box, in EPSG:4326, as the timeline's
            # local tile-overlap test needs it: a shapely geometry, no ee call
            "bbox": alerta["bounding_box"],
            # the acquisition-date window, as dates rather than the "N/A"-able
            # strings ``fecha1``/``fecha2`` the info card shows
            "date_min": _as_date(alerta["alert_date_min"]),
            "date_max": _as_date(alerta["alert_date_max"]),
            # the pair this alert was last saved with (spec D1): the timeline
            # restores it instead of picking, and falls back when it is gone
            "saved_before": _saved_pick(alerta.get("before_img")),
            "saved_after": _saved_pick(alerta.get("after_img")),
        }
    except Exception:
        logger.exception("Analysis: view_actual_alert map update failed")
        return

    # the earlier periods' reviewed alerts near this one (a no-op while the
    # switch is off); its own guard, so a failed read never blames the maps
    try:
        refresh_earlier_reviews(widgets, analysis, app_state)
    except Exception:
        logger.exception("Analysis: the earlier-reviews overlay failed")


def select_start_alert(app_state, app_tile_model):
    """Select an alert when alerts arrive with none selected: the active set's
    saved position (``go_to`` clamps it), else the first alert.

    Starting from the saved position matters because a set load publishes its
    alerts before it reads ``last_alert_index``: going to 0 here would make
    ``view_actual_alert`` save 0 over that position first. The recipe-era
    models carry no ``alert_set``, so they start at 0. Only auto-selects from
    the unset (-1) state, so it never fights manual navigation.
    """
    gdf = app_state.alerts_gdf.value
    if gdf is not None and len(gdf) > 0 and app_state.actual_alert_id.value < 0:
        start = getattr(getattr(app_tile_model, "alert_set", None), "last_alert_index", 0)
        app_state.go_to(start)


def wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field) -> None:
    """The subscriptions that drive the per-alert view (``build_analysis_layout``
    registers them once; the layout is built once per session).

    - ``actual_alert_id``: the per-alert orchestration re-runs whenever the
      current alert changes (navigation drives it), plus one immediate call so
      a pre-selected alert renders on first build. ``view_actual_alert`` no-ops
      with no alerts loaded, so this is safe at mount/smoke time and idempotent.
    - ``alerts_gdf``: an alert is selected when a set first arrives (a publish
      leaves ``actual_alert_id`` at -1, and the tile would otherwise stay empty
      until "next"), plus one immediate call. Block A subscribes for itself, so
      a review Save's rebind refreshes its facts grid without the tile's help.
    """

    def _on_alert_change(*_):
        view_actual_alert(widgets, app_state, analysis, app_tile_model, id_field)

    app_state.actual_alert_id.subscribe(_on_alert_change)
    view_actual_alert(widgets, app_state, analysis, app_tile_model, id_field)

    def _select_first_alert_cb(*_):
        select_start_alert(app_state, app_tile_model)

    app_state.alerts_gdf.subscribe(_select_first_alert_cb)
    _select_first_alert_cb()


#: set status -> the banner's message key (the same text Overview shows)
_SET_STATUS_KEYS = {Status.STALE: "steps.overview.stale", Status.MISSING: "steps.overview.missing"}


def build_set_status_banner(app_state) -> v.Html:
    """The spec §5.3 banner over the Analysis controls: the active set was
    built from inputs that have changed since (out of date), or its files are
    gone (missing), in the words Overview uses. It never blocks the review
    (rebuilding discards it). A raw widget, like the rest of the layout,
    updated on every project publish: the status is the manifest plus one
    ``os.stat``, so the subscriber is cheap and calls nothing back."""
    banner = v.Html(tag="div", class_="warning--text px-4 py-2", children=[], style_="display: none;")

    def _sync(*_):
        p = app_state.project.value
        s = active_set(p)
        status = set_status(p, paths.project_dir(p.name), s) if s is not None else None
        key = _SET_STATUS_KEYS.get(status)
        banner.children = [t(key)] if key else []
        banner.style_ = "" if key else "display: none;"

    app_state.project.subscribe(_sync)
    _sync()
    return banner


#: The three sections, the export parts and the footer, kept on the controls
#: div so the tests (and nothing else) can reach them without re-deriving the
#: tree.
_PANEL = "_defo_panel_sections"


def panel_sections(controls):
    """``SimpleNamespace(questions, refine, export, export_parts, footer)`` of
    a panel built by ``build_controls``: what ``build_questions_section``
    returned, the two ``section.build_section`` namespaces, what
    ``build_export`` returned and what ``footer.build_footer`` returned."""
    return getattr(controls, _PANEL)


def _open_questions_for(questions):
    """The save runner's ``on_refused``: a save refused for a required answer
    left empty opens the Questions section, where that answer is given (spec
    D5). Any other refusal has nothing to show there."""
    def _on_refused(refusal):
        if getattr(refusal, "key", "") == REQUIRED_MISSING:
            questions.set_open(True)
    return _on_refused


def build_controls(widgets, app_state, analysis, aux_model, app_tile_model, id_field) -> v.Html:
    """The right panel: the head, the two blocks, two sections and the footer.

    In order (spec §3-§6, D4, D6): the help text with its info popup, the
    alert-set selector, the set-status banner, **block A** (the queue counter,
    its progress, the navigation row and the facts grid with its status chip),
    **block B** (the verdict buttons), the collapsible *Questions* (one
    control per project question, the loss driver among them) and *Refine
    polygon* sections, then the **sticky footer**: the collapsible *Export
    this alert* section, *Save & next* / *Save* and the save-state line. The
    six always-open cards of the old panel are gone with it, as is the
    separate navigation card (block A absorbed it).

    Built once per session with the rest of the layout, so every subscription
    the sub-builders register is registered once; the section bodies are
    assigned into their ``body_box`` here, once, and stay in the tree for the
    session (a collapse only toggles ``display``, never a ``v-if`` rebuild).
    The Questions body is the one exception: it holds no imperative state, and
    it is rebuilt when the project's questions change.
    """
    refine = build_section(t("analysis_tile.sections.refine"), v.Html(tag="div"))
    refine_body = build_edit_tools(
        widgets=widgets, app_state=app_state, analysis=analysis,
        app_tile_model=app_tile_model, set_badge=refine.set_badge,
    )
    refine.body_box.children = [refine_body]

    # Rebuilt by itself when the project's questions change; the answers it
    # shows and writes are ``analysis.answers``. Built before the export
    # section: a save refused for a required answer opens it.
    questions = build_questions_section(app_state, analysis)

    # Built before the footer so the footer's two Save buttons drive the
    # export section's ONE save runner: a save from either runs its post-save
    # step, which is what enables its build tile and moves its badge on.
    exports = build_section(t("analysis_tile.sections.export"), v.Html(tag="div"))
    export_parts = build_export(
        widgets=widgets, app_state=app_state, aux_model=aux_model, analysis=analysis,
        app_tile_model=app_tile_model, set_badge=exports.set_badge,
        on_refused=_open_questions_for(questions),
    )
    exports.body_box.children = [export_parts.body]
    footer = build_footer(export_parts.run_save, exports.root, app_state, analysis)

    controls = v.Html(
        tag="div",
        class_="pa-0 ma-0",
        children=[
            info_button(t("analysis_tile.description"), t("analysis_tile.info_header"),
                        t("analysis_tile.info_md")),
            build_set_selector(app_state),
            build_set_status_banner(app_state),
            build_context(app_state, id_field),
            build_review_panel(widgets=widgets, app_state=app_state, analysis=analysis),
            build_earlier_controls(widgets, app_state, analysis),
            questions.root,
            refine.root,
            footer.root,
        ],
    )
    setattr(controls, _PANEL, SimpleNamespace(questions=questions, refine=refine, export=exports,
                                              export_parts=export_parts, footer=footer))
    return controls


def build_analysis_layout(
    aux_model,
    selected_alerts_model,
    analysis,
    app_tile_model,
    app_state,
    gee_interface,
    theme_state=None,
):
    """Build the raw ipyvuetify analysis layout (parent of the analysis tile).

    Returns ``(stage, controls)`` — the two-map workspace ``v.Container`` and the
    right-panel controls ``v.Html``. The shell renders ``stage`` as the MapApp step
    content and ``controls`` in the docked right panel (wrapper 7). Built once (no
    re-render) so the sub-builders' handlers + subscriptions are wired exactly once
    and the raw widget trees survive the step container's ``v-if`` rebuild.

    Parameters
    ----------
    aux_model : SetContext
        ``custom_report_template`` (the project's; used by ``build_export``).
    selected_alerts_model : SetContext
        ``combined_path``, the set's combined raster: the "Original Alert"
        layer (used by ``imagery.image_slider_map_callback``).
    analysis : AnalysisState
        The session's review state (chosen images and their info, drawn defo
        layer, CNN predictions, ``last_save_time``), handed to every
        sub-builder. The parent resets it per alert
        (``view_actual_alert``); the timeline publishes the chosen images into
        it and ``imagery.wire_panes`` draws them.
    app_tile_model : SetContext
        ``recipe_folder_path`` (the active set's folder) for the output paths,
        ``alert_set`` + ``remember_position`` for the review position and
        ``record_review`` for the reviewed count. The shell passes the same
        ``SetContext`` for all three models (spec §10).
    app_state : AppState
        Source of truth for the alerts table + navigation.
    gee_interface : GEEInterface
        Session GEE interface, forwarded to both maps.
    theme_state : ThemeState, optional
        Shared app theme state, forwarded to both maps so they track the app's
        light/dark theme exactly as the shared main map does.
    """
    # Build the shared heavy widgets once.
    widgets = AnalysisWidgets(gee_interface=gee_interface, theme_state=theme_state)
    # The visualization layers marked for Analysis, switched off on both maps
    # (spec §10); the layout is built once per session, so wired once and the
    # subscription lives as long as the kernel (its unsubscribe is not kept).
    vis_sync.wire(widgets, app_state)

    # Alert-id field (raw ipyvuetify so view_actual_alert can imperatively set
    # .v_model). Owned here, passed to block A's jump row (build_context,
    # through build_controls) and to the per-alert subscription, which writes
    # the 1-based number the queue counter shows.
    # Empty, not the 0-based index it used to seed: the field is 1-based now
    # and ``wire_alert_navigation`` below fills it from the selected alert
    # before the panel is built (with none selected there is no number to
    # show, and ``_on_go`` ignores a field it cannot read).
    id_field = v.TextField(
        v_model=None,
        outlined=True,
        single_line=True,
        hide_details=True,
    )

    # Full-stage analysis workspace (Option C / variant 1). The stage fills the
    # MapApp step container; two equal map columns sit side by side, each map
    # fills the stage height (viewport-relative), with a compact internally-scrolling
    # image-selector strip beneath it. Was injected via solara.Style; lives here as the stage's first
    # child so the raw widget tree survives the step container's v-if rebuild.
    style_el = v.Html(
        tag="style",
        children=[
            ".analysis-stage { height: 100%; display: flex; flex-direction: column; } "
            # Stage background follows the app light/dark theme. The default is the
            # light surface; the ``analysis-stage--dark`` marker class (toggled from
            # ``theme_state.dark`` below — the SAME trait the maps bind to) swaps it
            # to the dark surface so the workspace matches the rest of the app.
            ".analysis-stage { background-color: #ffffff; transition: background-color 0.2s; } "
            ".analysis-stage.analysis-stage--dark { background-color: #121212; } "
            # Outline drawing the limits of the timeline strip. The border
            # colour tracks the theme (subtle dark line on light, subtle light line
            # on dark) via the same ``--dark`` marker on the stage. The maps are NOT
            # bordered: a light line framing the near-black map panels reads as a
            # harsh white border, so the maps draw their own limit via the basemap.
            ".analysis-stage .analysis-timeline { border: 1px solid rgba(0, 0, 0, 0.18); "
            "border-radius: 4px; } "
            ".analysis-stage.analysis-stage--dark .analysis-timeline { "
            "border-color: rgba(255, 255, 255, 0.18); } "
            ".analysis-row { display: flex; gap: 8px; flex: 1 1 auto; min-height: 0; "
            "width: 100%; } "
            # min-width:0 is THE fix for the selector overflow: without it the flex
            # column defaults to min-width:auto, so the image slide group stretches
            # the column and resizes the maps. With it the column holds its half and
            # the slide group scrolls inside its strip.
            ".analysis-col { flex: 1 1 0; min-width: 0; overflow: hidden; "
            "display: flex; flex-direction: column; } "
            ".analysis-mapbox { display: flex; flex-direction: column; } "
            # The ipyleaflet map sits inside an auto-generated ipywidgets wrapper
            # (display:block) that breaks the flex-grow chain, so the map cannot be
            # grown to fill the column via flex. Use a viewport-relative fixed
            # height instead (the MapApp step container is full-viewport): tall maps
            # that fill the stage, leaving room for the per-column title plus the
            # one shared strip below both of them. Measured: ~64px of per-column
            # chrome (the map card's title + padding) and ~206px of strip (38px
            # source toolbar + 128px track + ~32px readout + 8px padding).
            ".custom-map-class2 { height: calc(100vh - 270px) !important; min-height: 320px; } "
            # The strip spans both columns, so it is a row of its own under them.
            # flex: 0 0 auto, not the default 0 1 auto: the stage is a flex
            # column and ``.analysis-row`` already absorbs overflow
            # (flex: 1 1 auto), so the strip must not shrink too -- that is
            # what makes the 270px the maps give up self-enforcing.
            ".analysis-strip { width: 100%; flex: 0 0 auto; padding: 0 4px 4px 4px; } "
            ".v-text-field .v-input__control .v-input__slot { min-height: auto !important; "
            "min-width: 40px; display: flex !important; align-items: center !important; }"
        ],
    )

    wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field)

    # TODO(recipe-load): the OLD tile rebuilt/merged alerts_gdf from the
    # selected-alerts traits here (create_gdf_partial/create_gdf_full + the
    # alerts_bbs/alerts_total_bbs observers). That is superseded by the Filter
    # tile publishing app_state.alerts_gdf. The recipe-LOAD re-discovery/merge
    # path (re-pointing previously reviewed alerts) is deferred to a later task.

    # ------------------------------------------------------------------
    # Layout: left two-map panel + right scrolling control panel (ports OLD
    # initialize_layout left_panel / right_panel), as a raw ipyvuetify tree.
    # ------------------------------------------------------------------
    # The one shared image timeline, under both panes. Built HERE because
    # ``build_analysis_layout`` runs once per session
    # (``solara_app``'s ``solara.use_memo(..., dependencies=[])``), and
    # ``build_timeline`` registers five reactive subscriptions and discards
    # their unsubscribers: correct for a builder that lives as long as the
    # kernel, a compounding leak anywhere that re-runs per alert or per step
    # entry. It needs no alert: an alert change reaches it through
    # ``widgets.imagery_ctx``.
    widgets.timeline = build_timeline(app_state, analysis, widgets)

    # One subscription per pane, so a pick on the strip rebuilds that pane's
    # image from its granule keys and draws it. Side-effect only (the two
    # unsubscribers are discarded for the same reason as above).
    wire_panes(widgets, analysis, selected_alerts_model)

    # LEFT/STAGE: the two map columns, now carrying only their map -- the
    # selector that used to sit under each one is replaced by the single strip
    # below both of them. Each column keeps min-width:0 so a wide child
    # scrolls inside the column instead of resizing the maps.
    left_inner_row = v.Html(
        tag="div",
        class_="analysis-row",
        children=[
            v.Html(tag="div", class_="analysis-col", children=[widgets.map31]),
            v.Html(tag="div", class_="analysis-col", children=[widgets.map32]),
        ],
    )
    strip_row = v.Html(tag="div", class_="analysis-strip", children=[widgets.timeline])

    # RIGHT/CONTROLS: the review console -- the head, block A, block B, the
    # Questions and Refine sections and the sticky footer (``build_controls``).
    # Rendered in MapApp's
    # docked right panel (a panel wrapper in the shell), NOT inside the stage
    # -- same chrome as AOI/Filter/Overview.
    controls = build_controls(
        widgets=widgets,
        app_state=app_state,
        analysis=analysis,
        aux_model=aux_model,
        app_tile_model=app_tile_model,
        id_field=id_field,
    )

    # STAGE: the two-map workspace, handed to MapApp as the step content.
    stage = v.Container(
        fluid=True,
        class_="pa-0 ma-0 analysis-stage",
        children=[style_el, left_inner_row, strip_row],
    )

    # Drive the stage background + outline colours off the app theme by toggling
    # the ``analysis-stage--dark`` marker class. We observe the SAME
    # ``theme_state.dark`` trait the two maps bind to, so the workspace chrome and
    # the maps flip together. Built once with the rest of the raw widget tree, so
    # the observer is wired exactly once.
    def _sync_stage_theme(dark):
        stage.class_ = "pa-0 ma-0 analysis-stage" + (
            " analysis-stage--dark" if dark else ""
        )

    if theme_state is not None:
        theme_state.observe(
            lambda change: _sync_stage_theme(bool(change["new"])), "dark"
        )
    _sync_stage_theme(bool(getattr(theme_state, "dark", False)))

    return stage, controls
