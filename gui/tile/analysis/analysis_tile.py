"""Analysis tile parent — raw ipyvuetify layout builder (Phase-4 Solara port).

Architecture layer: view (raw-widget builder, no ``@solara.component``). This is
the parent slice of porting the 2052-line ipyvuetify ``AnalysisTile`` god-class.
It establishes:

  * the shared heavy widgets (``AnalysisWidgets``, built once),
  * the two-view stage (one ``AlertViews`` under a header row, the timeline
    strip below) + control right panel layout (ports OLD
    ``initialize_layout``), as a raw ipyvuetify ``v.Container`` tree,
  * the per-alert orchestration ``view_actual_alert`` registered as a
    ``app_state.actual_alert_id.subscribe`` (replacing the former ``use_effect``)
    plus one immediate call so a pre-selected alert renders on first build,
  * the right-panel review console (``build_controls``: the head, block A,
    block B, the Questions and Refine sections and the sticky footer that
    holds the Export section and the Save buttons),
  * and the sub-component builders (imagery / review / edit / export).

WHY A RAW BUILDER (not a ``@solara.component``): the analysis screen is built
once per session and stays mounted for the session's lifetime, shown and hidden
with CSS; a raw ipyvuetify widget tree keeps its views (and ``AlertViews`` its
Leaflet maps) across every show and hide. ``build_analysis_layout`` builds the
tree once and RETURNS ``build_screen``'s namespace: ``screen`` (the full-height
root, right of MapApp's rail, the shell displays as a sibling of MapApp), ``controls`` (the review
console in its right column), ``widgets``, ``show(on)`` and ``relabel()``. The
screen's CSS (``SCREEN_CSS``) is its first child, a raw ``v.Html(tag="style")``.
The two former ``use_effect``s are ``.subscribe`` registrations on
``app_state.actual_alert_id`` / ``app_state.alerts_gdf`` (each also invoked
once immediately).

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
* **Heavy widgets are imperative + memoized once.** The views (``AlertViews``)
  and their writer (``PaneLayers``) live on ``AnalysisWidgets`` and are
  mutated in place by ``view_actual_alert``; the per-alert effect is keyed on the alert id
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

import ipyvuetify as v

from deforestation_alerts.engine.fingerprint import Status, set_status
from deforestation_alerts.project import paths
from deforestation_alerts.project.questions import column
from deforestation_alerts.scripts.mosaics_helper import (
    convert_julian_to_date,
    multipolygon_to_geodataframe,
)
from gui.tile.analysis.context import build_context, build_topnav
from gui.tile.analysis.earlier import build_earlier_controls, refresh_in_background, remove_earlier_layer
from gui.tile.analysis.questions_panel import build_questions_section
from gui.tile.analysis.review_panel import build_review_panel
from gui.tile.analysis.edit_tools import build_edit_tools
from gui.tile.analysis.export import REQUIRED_MISSING, build_export
from gui.tile.analysis.footer import build_footer
from gui.tile.analysis.imagery import alert_extent, set_image_area, wire_panes
from gui.tile.analysis.state import IMAGE_AREAS
from gui.tile.analysis.pane_layers import geojson_spec
from gui.tile.analysis.section import build_section
from gui.tile.analysis.timeline import THEME_CSS as timeline_theme_css
from gui.tile.analysis.timeline import build_timeline
from gui.tile.analysis import vis_sync
from gui.i18n import t
from gui.scripts import alert_filter
from gui.scripts import questions
from gui.scripts import set_store
from gui.store.workflow_steps import active_set
from gui.tile.analysis.widgets import AnalysisWidgets, info_button
from gui.widget.set_selector import build_set_selector, set_text

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
#: The spec id of the alert's bounding box on the views.
ALERT_BB_ID = "alert-bb"


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
    shared timeline draws from, the drawing, the alert layers and the earlier
    overlay on the views, the zoom-to-alert bounds, and the ee bbox and
    covering grid an export is built from.

    The visualization layers stay, as they do on an ordinary alert change
    (spec §10, ``vis_sync``). Everything after the state is best-effort: a
    stand-in without views must not cost the invalidation itself.
    """
    analysis.reset_alert()
    try:
        ctx = getattr(widgets, "imagery_ctx", None)
        if ctx is not None:
            ctx.value = None
        panes = getattr(widgets, "panes", None)
        if panes is not None:
            # Out of edit mode and nothing drawn: both belonged to the alert.
            panes.set_edit_mode(False)
            panes.set_drawing([])
            panes.clear("alert")
            panes.set_focus_bounds(None)
        remove_earlier_layer(widgets)
        widgets.actual_bb = None
        widgets.actual_alert_grid = None
        widgets.actual_bb_geojson = None
    except Exception:
        logger.exception("Analysis: clearing the alert view failed")


def view_actual_alert(
    widgets, app_state, analysis, app_tile_model, id_field
):
    """Per-alert orchestration owned by the foundation.

    Ports the foundation-relevant parts of the OLD ``view_actual_alert``: select
    the alert row, refresh the nav id field, forget the
    previous alert's review state (``analysis.reset_alert``), rebuild the bbox overlay on the views, center/zoom
    and the zoom-to-alert bounds, compute the ee bbox + covering grid (stored on
    ``widgets``), set the default image-collection flags, and remember the position on the active
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

        panes = widgets.panes
        # out of edit mode, nothing drawn: the drawing belonged to the
        # previous alert (the edit tools open it again on Start Drawing)
        panes.set_edit_mode(False)
        panes.set_drawing([])

        # clear the previous alert's layers (the visualization layers stay:
        # vis_sync keeps them, spec §10) BEFORE the view moves: a layer left
        # on would fetch tiles at the new view, from the previous set's
        # raster, which a period delete is removing (500s from the tile server)
        panes.clear("alert")
        # the earlier-reviews overlay is the previous alert's neighbourhood:
        # ``refresh_in_background`` below draws this alert's
        remove_earlier_layer(widgets)

        # center/zoom: one write, every view follows (``AlertViews``)
        panes.set_view((alerta.point.y, alerta.point.x), 16)

        # the bbox overlay on every view, hidden while the reviewer edits
        panes.add(geojson_spec(ALERT_BB_ID, "all", alerta_bb_geojson, name="Alert BB", group="alert",
                               style=_ALERT_BB_STYLE, hide_while_editing=True))
        # the after view's zoom-to-alert button goes back to this box
        panes.set_focus_bounds(alerta["bounding_box"].bounds)

        # re-add the saved defo polygon if this alert was already reviewed
        if (
            alerta["status"] in {"Confirmed", "Need revision"}
            and alerta["alert_polygon"] is not None
        ):
            analysis.defo_layer.set(multipolygon_to_geodataframe(
                alerta["alert_polygon"]
            ).set_crs(epsg="4326", allow_override=True, inplace=True))

        # ee bbox (buffer + bounds) + covering grid for downloads
        # (the normal image area: ``reset_alert`` put ``image_area`` back to
        # it, and ``set_image_area`` recomputes both for a larger one)
        widgets.actual_bb_geojson = alerta_bb_geojson
        widgets.actual_bb, widgets.actual_alert_grid = alert_extent(
            alerta_bb_geojson, IMAGE_AREAS["normal"])

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
        refresh_in_background(widgets, analysis, app_state)
    except Exception:
        logger.exception("Analysis: the earlier-reviews overlay failed")


def select_start_alert(app_state, app_tile_model):
    """Select an alert when alerts arrive with none selected, by the one
    start-position resolver (spec §8.4, ``alert_filter.start_position``, as
    ``set_store.publish_set_alerts``): the active set's saved position
    clamped, or under the Analysis filter its first unreviewed match.

    Starting from the saved position matters because a set load publishes its
    alerts before it reads ``last_alert_index``: going to 0 here would make
    ``view_actual_alert`` save 0 over that position first. The recipe-era
    models carry no ``alert_set``, so they start at 0. Only auto-selects from
    the unset (-1) state, so it never fights manual navigation.
    """
    gdf = app_state.alerts_gdf.value
    # Before the filter is read: a filter made on another set goes now, not
    # whenever ``AppState``'s own ``alerts_gdf`` listener happens to run.
    app_state.forget_filter_of_another_set(gdf)
    if gdf is not None and len(gdf) > 0 and app_state.actual_alert_id.value < 0:
        saved = getattr(getattr(app_tile_model, "alert_set", None), "last_alert_index", 0)
        p = app_state.project.value
        app_state.go_to(alert_filter.start_position(gdf, app_state.alert_filter, saved,
                                                    p.questions if p is not None else []))


def follow_filter(app_state, f) -> None:
    """A filter applied while an alert is on screen moves to the filter's
    start (spec §8.4): that alert when it matches, else the first unreviewed
    match, else the first match; when nothing matches, the alert stays.
    Clearing the filter moves nothing. With no alert on screen the next
    publish applies the filter (``set_store.publish_set_alerts``)."""
    gdf = app_state.alerts_gdf.value
    # As ``select_start_alert``: a filter made on another set goes first (its
    # clear re-enters here with None), and the one left in force is followed.
    app_state.forget_filter_of_another_set(gdf)
    if app_state.alert_filter_reactive is not None:
        f = app_state.alert_filter
    index = app_state.actual_alert_id.value
    if f is None or gdf is None or not len(gdf) or index < 0:
        return
    p = app_state.project.value
    s = active_set(p)
    if s is not None and not set_store.alerts_belong_to(gdf, s):
        return  # another set's alerts, until the new set's are published
    target = alert_filter.start_position(gdf, f, index, p.questions if p is not None else [])
    if target != index:
        app_state.go_to(target)


def wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field) -> None:
    """The subscriptions that drive the per-alert view (``build_analysis_layout``
    registers them once; the layout is built once per session).

    - ``actual_alert_id``: the per-alert orchestration re-runs whenever the
      current alert changes (navigation drives it), plus one immediate call so
      a pre-selected alert renders on first build. ``view_actual_alert`` no-ops
      with no alerts loaded, so this is safe at mount/smoke time and idempotent.
    - ``analysis.reload_alert``: the same re-run when a merge rewrote the
      current alert's row (spec §7.4).
    - ``alerts_gdf``: an alert is selected when a set first arrives (a publish
      leaves ``actual_alert_id`` at -1, and the tile would otherwise stay empty
      until "next"), plus one immediate call. Block A subscribes for itself, so
      a review Save's rebind refreshes its facts grid without the tile's help.
    - ``analysis.alert_filter``: bound into ``app_state``, where the
      navigation reads it, and a new filter moves to its start
      (``follow_filter``).
    """
    # Bound before anything below reads the filter. A filter made on another
    # set is dropped by ``select_start_alert`` / ``follow_filter`` themselves
    # (``forget_filter_of_another_set``), not by listener order.
    app_state.bind_alert_filter(analysis.alert_filter)
    analysis.alert_filter.subscribe(lambda f: follow_filter(app_state, f))
    # A start filter set before this layout was wired (``apply_start_filter``
    # on a session whose alerts were already published) is resolved now.
    follow_filter(app_state, analysis.alert_filter.value)

    def _on_alert_change(*_):
        view_actual_alert(widgets, app_state, analysis, app_tile_model, id_field)

    app_state.actual_alert_id.subscribe(_on_alert_change)
    analysis.reload_alert.subscribe(_on_alert_change)
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
    exports = build_section(t("analysis_tile.sections.export"), v.Html(tag="div"), divided=False)
    export_parts = build_export(
        widgets=widgets, app_state=app_state, aux_model=aux_model, analysis=analysis,
        app_tile_model=app_tile_model, set_badge=exports.set_badge,
        on_refused=_open_questions_for(questions),
    )
    exports.body_box.children = [export_parts.body]
    footer = build_footer(export_parts.run_save, exports.root, app_state, analysis)

    # The queue, the navigation and the set selector go to the screen's top
    # bar (``build_screen``); built here with the card whose subscriptions
    # keep them current.
    # Bound before block A is built, which subscribes to the bound filter
    # (``wire_alert_navigation`` binds the same reactive: a no-op twice).
    app_state.bind_alert_filter(analysis.alert_filter)
    context = build_context(app_state, id_field)
    topnav = build_topnav(context, build_set_selector(app_state))
    earlier = build_earlier_controls(widgets, app_state, analysis)
    controls = v.Html(
        tag="div",
        class_="pa-0 ma-0",
        children=[
            build_set_status_banner(app_state),
            context,
            build_review_panel(widgets=widgets, app_state=app_state, analysis=analysis),
            questions.root,
            refine.root,
            earlier.root,
            footer.root,
        ],
    )
    setattr(controls, _PANEL, SimpleNamespace(questions=questions, refine=refine, export=exports,
                                              export_parts=export_parts, footer=footer,
                                              earlier=earlier, topnav=topnav))
    return controls


#: The analysis screen's stylesheet (spec 2026-10-08 §2.2): a full-height flex
#: layout right of MapApp's rail, no height computed from the viewport. The
#: left column holds the view headers, the views (whatever height is left) and
#: the timeline strip; the right column (the review console) is the only part
#: that scrolls. The root keeps the ``analysis-stage`` /
#: ``analysis-stage--dark`` classes the timeline's colours key on
#: (``timeline.THEME_CSS``, ``handle_strip.py``).
SCREEN_CSS = (
    # Right of MapApp's rail, which stays usable on the screen (user,
    # 2026-10-09): ``left`` follows the rail's live width (MapApp sets
    # --sepal-drawer-width on :root, 60 px mini, 320 px expanded), as HEAD's
    # stage did. ``bottom`` and ``right`` are 0: --sepal-bottom-reserved and
    # --sepal-right-panel-width belong to the right panel, which is hidden here.
    # z-index 4, HEAD's stage level: above MapApp's map (0); below the rail's
    # collapse arrow (5), the rail and the right panel (6), menus (8+),
    # pysepal's task pill (170), the dialog scrim (201), dialogs (202+) and
    # toasts (9999).
    ".defo-analysis-screen { position: fixed; top: 0; bottom: 0; left: var(--sepal-drawer-width, 60px); right: 0;"
    " z-index: 4; display: flex; flex-direction: column; transition: left 0.3s ease;"
    " background-color: #ffffff; color: rgba(0, 0, 0, 0.87); font-size: 14px; }\n"
    ".defo-analysis-screen.theme--dark { background-color: #121212; color: #ffffff; }\n"
    # ipyvuetify wraps a standalone display in a v-application of its own,
    # whose wrap reserves a full viewport height.
    ".defo-analysis-screen .v-application--wrap { min-height: 0 !important; }\n"
    ".defo-analysis-topbar { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; height: 56px;"
    " padding: 0 12px; border-bottom: 1px solid rgba(128, 128, 128, 0.25); }\n"
    ".defo-analysis-title { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }\n"
    # the top bar's navigation (``context.build_topnav``)
    ".defo-analysis-screen .defo-topnav { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 8px; }\n"
    ".defo-analysis-screen .defo-topnav-set { flex: 0 1 380px; min-width: 180px; }\n"
    ".defo-analysis-screen .defo-topnav-set .v-select { padding: 0 !important; margin: 0; }\n"
    ".defo-analysis-screen .defo-jump-btn { min-width: 0 !important; }\n"
    # the filter's funnel and chip (``filter_menu``), left of the set
    # selector. Neither shrinks: in a review copy the chip is the only
    # statement of whose alerts these are (at 1400 px it was cut to "Eval…"),
    # so the title and the set selector give way first. Only a chip past
    # 220 px ends in an ellipsis; its tooltip names the filter in full.
    ".defo-analysis-screen .defo-topnav-filter { flex: 0 0 auto; display: flex; align-items: center;"
    " gap: 4px; }\n"
    ".defo-analysis-screen .defo-filter-chip { flex-shrink: 0; max-width: 220px; }\n"
    ".defo-analysis-screen .defo-filter-chip .v-chip__content { display: block; min-width: 0; overflow: hidden;"
    " text-overflow: ellipsis; white-space: nowrap; }\n"
    # The progress is as wide as its tally (one line, at least 150 px): a
    # filtered tally ("… · filtered · 860 total") is longer, and a fixed width
    # let it run over the help button. The spacer, then the set selector,
    # give way instead, so the filter and the set move left.
    ".defo-analysis-screen .defo-topnav-progress { flex: 0 0 auto; min-width: 150px; display: flex;"
    " flex-direction: column; gap: 4px; margin-left: 4px; }\n"
    # Over the review panel, at its width and padding: Previous lines up with
    # the panel's start, Next with its end. The negative margin cancels the
    # top bar's right padding so the block reaches the screen edge as the
    # panel does.
    ".defo-analysis-screen .defo-topnav-nav { flex: 0 0 480px; align-self: stretch; display: flex; align-items: center;"
    " justify-content: space-between; gap: 8px; padding: 0 16px; margin-right: -12px;"
    " border-left: 1px solid rgba(128, 128, 128, 0.25); }\n"
    ".defo-analysis-screen .defo-topnav-pos { display: flex; align-items: center; gap: 8px; }\n"
    ".defo-analysis-body { flex: 1 1 auto; min-height: 0; display: flex; }\n"
    ".defo-analysis-left { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 4px;"
    " padding: 8px; }\n"
    # The review console, at the workflow panel's width (480 px) it was laid out at.
    ".defo-analysis-right { flex: 0 0 480px; min-height: 0; overflow-y: auto; padding: 8px 16px 0 16px;"
    " border-left: 1px solid rgba(128, 128, 128, 0.25); }\n"
    # One header cell per view, in a row over the views: equal halves, as the
    # views split the width (``.mv-layout-row``). min-width:0 keeps a long
    # title from widening its half.
    ".analysis-heads { display: flex; gap: 8px; flex: 0 0 auto; }\n"
    ".analysis-heads > .analysis-mapbox { flex: 1 1 0; min-width: 0; }\n"
    ".analysis-mapbox { display: flex; flex-direction: column; }\n"
    # The views take whatever height is left. The widget sits in an
    # ipywidgets wrapper that breaks a flex height chain, so the slot is the
    # positioned box and the widget fills it.
    ".analysis-panes-slot { position: relative; flex: 1 1 auto; min-height: 240px; }\n"
    ".analysis-panes-slot > *, .analysis-panes-slot .analysis-views { position: absolute; inset: 0; }\n"
    ".analysis-strip { flex: 0 0 auto; padding: 0 4px 4px 4px; }\n"
    # The strip's outline follows the theme. The views are not bordered: they
    # draw their own limit with the basemap.
    ".defo-analysis-screen .analysis-timeline { border: 1px solid rgba(0, 0, 0, 0.18); border-radius: 4px; }\n"
    ".defo-analysis-screen.theme--dark .analysis-timeline { border-color: rgba(255, 255, 255, 0.18); }\n"
    ".defo-analysis-screen .v-text-field .v-input__control .v-input__slot { min-height: auto !important; min-width: 40px;"
    " display: flex !important; align-items: center !important; }\n"
    # The screen's one type scale (``section.TYPE_*``): 14 px titles and body,
    # 12 px labels and badges, sentence case everywhere. Vuetify's spaced
    # capitals on buttons and chips, and its 16 px input labels, are what
    # made the panel read as three different apps.
    ".defo-analysis-screen .v-btn { text-transform: none !important; letter-spacing: normal !important; }\n"
    ".defo-analysis-screen .v-chip { letter-spacing: normal; }\n"
    ".defo-analysis-screen .v-chip.v-size--x-small { font-size: 12px; height: 20px; }\n"
    ".defo-analysis-screen .v-input .v-label { font-size: 14px; }\n"
    ".defo-analysis-screen .v-select__selection, .defo-analysis-screen .v-text-field input { font-size: 14px; }\n"
    ".defo-analysis-screen .dat-title { font-size: 14px; font-weight: 600; line-height: 20px; }\n"
    ".defo-analysis-screen .dat-body { font-size: 14px; font-weight: 400; line-height: 20px; }\n"
    ".defo-analysis-screen .dat-label { font-size: 12px; font-weight: 400; line-height: 16px; opacity: 0.7; }\n"
    ".defo-analysis-screen .dat-caption { font-size: 12px; line-height: 16px; }\n"
    ".defo-analysis-screen .dat-num { font-variant-numeric: tabular-nums; }\n"
    ".defo-analysis-screen .analysis-mapbox .v-card__title { font-size: 16px; font-weight: 600; }\n"
    # The Questions section (``questions_panel``): one outlined block per
    # question, a numbered header, the options as full-width tiles.
    ".defo-analysis-screen .dat-question { border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 8px;"
    " padding: 12px; margin-bottom: 8px; }\n"
    ".defo-analysis-screen .dat-q-num { flex: 0 0 22px; height: 22px; border-radius: 11px; display: inline-flex;"
    " align-items: center; justify-content: center; font-size: 12px; font-weight: 600;"
    " background: rgba(128, 128, 128, 0.18); }\n"
    ".defo-analysis-screen .dat-q-required { font-size: 12px; }\n"
    ".defo-analysis-screen .dat-options .v-radio { border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 6px;"
    " padding: 8px 10px; margin: 0 0 6px 0 !important; cursor: pointer; transition: border-color 0.15s, background 0.15s; }\n"
    # Neutral, not the primary colour: the theme publishes no
    # ``--v-primary-base`` (see ``file_tile``), and the radio's own dot
    # already wears the primary colour.
    ".defo-analysis-screen .dat-options .v-radio:hover { border-color: rgba(128, 128, 128, 0.7); }\n"
    ".defo-analysis-screen .dat-options .v-radio.v-item--active { border-color: currentColor;"
    " background: rgba(128, 128, 128, 0.12); }\n"
    ".defo-analysis-screen .dat-options .v-radio.v-item--active .v-label { font-weight: 600; }\n"
    ".defo-analysis-screen .dat-options .v-label { font-size: 14px; }\n"
    # the timeline's and the view titles' theme colours
    + timeline_theme_css
)


def build_screen(widgets, controls, app_state, theme_state=None) -> SimpleNamespace:
    """The analysis screen: a full-height sibling of MapApp, right of its rail
    (spec 2026-10-08 §2.2, R30), around the analysis widgets and the review
    console.

    A top bar (*← Workflow* and the title: Analysis · project · alert set),
    then the left column (the two view headers, ``widgets.panes_widget`` and
    the timeline strip) beside the right column (``controls``). Built hidden
    (``display: none``); the shell's ``_ScreenBridge`` calls ``show`` and
    ``relabel``. ``widgets`` needs ``panes_widget``, ``panes`` (its
    ``PaneLayers``), ``head_before``, ``head_after`` and ``timeline``.

    Returns ``SimpleNamespace(screen, controls, widgets, show, relabel)``:

    * ``show(on)``: on, the screen is displayed, then it gets the
      ``defo-analysis-screen--on`` class the shell's CSS hides MapApp's main
      map and right panel with (its rail stays). Off, in the other order: the
      class goes first, so the main map and the panel are displayed again
      while the screen still covers the map, and only then the screen (and
      the views in it) is hidden. The views' hide nudge (a window resize,
      ``js/app/resize_nudge.js``) then finds the main map displayed and
      re-measures it; the frontend applies the two writes in this order.
    * ``relabel()``: the back button, the title and the views' ``labels`` in
      the current language.
    """
    back = v.Btn(
        text=True,
        class_="defo-back-to-workflow",
        style_="text-transform: none;",
        children=[v.Icon(left=True, children=["mdi-arrow-left"]), ""],
    )
    back.on_event("click", lambda *_: app_state.screen.set("workflow"))
    title = v.Html(tag="div", class_="defo-analysis-title dat-title", children=[""])
    panel = getattr(controls, _PANEL, None)
    topnav = getattr(panel, "topnav", None)
    # The panel's help, as the top bar's last control: the description is
    # the icon's tooltip (it no longer has a line of its own in the panel).
    info = info_button(t("analysis_tile.description"), t("analysis_tile.info_header"), t("analysis_tile.info_md"))
    _span, info_btn, _dialog = info.children
    info_btn.children = [v.Icon(children=["mdi-help-circle-outline"])]
    info_btn.attributes = {"title": t("analysis_tile.description"), "aria-label": t("analysis_tile.info_header")}
    info.children = [info_btn, _dialog]
    info.class_ = "d-flex align-center"
    if topnav is not None:
        # after the set's progress, before the navigation block
        topnav.children = [*topnav.children[:-1], info, topnav.children[-1]]
    topbar = v.Html(tag="div", class_="defo-analysis-topbar",
                    children=[back, title, *([topnav] if topnav is not None else [info])])
    heads = v.Html(tag="div", class_="analysis-heads", children=[widgets.head_before, widgets.head_after])
    panes_slot = v.Html(tag="div", class_="analysis-panes-slot", children=[widgets.panes_widget])
    strip = v.Html(tag="div", class_="analysis-strip", children=[widgets.timeline])
    left = v.Html(tag="div", class_="defo-analysis-left", children=[heads, panes_slot, strip])
    right = v.Html(tag="div", class_="defo-analysis-right", children=[controls])
    body = v.Html(tag="div", class_="defo-analysis-body", children=[left, right])
    screen = v.Html(
        tag="div",
        style_="display: none;",
        children=[v.Html(tag="style", children=[SCREEN_CSS]), topbar, body],
    )
    state = {"on": False, "dark": bool(getattr(theme_state, "dark", False))}

    def _apply_classes():
        screen.class_ = (
            "defo-analysis-screen analysis-stage"
            + (" defo-analysis-screen--on" if state["on"] else "")
            + (" analysis-stage--dark theme--dark" if state["dark"] else " theme--light")
        )

    def show(on):
        if on:
            screen.style_ = ""
            state["on"] = True
            _apply_classes()
        else:
            state["on"] = False
            _apply_classes()
            screen.style_ = "display: none;"

    def _retitle():
        p = app_state.project.value
        alert_set = active_set(p)
        # The set is the top bar's selector now; the title names the screen
        # and the project, and falls back to the set while there is no bar.
        parts = [
            t("app.drawer_item.analysis"),
            p.name if p is not None else "",
            set_text(p, alert_set) if alert_set is not None and topnav is None else "",
        ]
        title.children = [" · ".join(part for part in parts if part)]

    def relabel():
        back.children = [back.children[0], t("app.panel_workflow_title")]
        _retitle()
        widgets.panes.relabel()

    def _on_dark(change):
        state["dark"] = bool(change["new"])
        _apply_classes()

    # Built once per session, like the rest of the tree: the subscription and
    # the observer live as long as the kernel (their unsubscribers are not kept).
    app_state.project.subscribe(lambda _project: _retitle())
    if theme_state is not None:
        theme_state.observe(_on_dark, "dark")
    relabel()
    _apply_classes()
    return SimpleNamespace(screen=screen, controls=controls, widgets=widgets, show=show, relabel=relabel)


def build_analysis_layout(
    aux_model,
    selected_alerts_model,
    analysis,
    app_tile_model,
    app_state,
    gee_interface,
    theme_state=None,
):
    """Build the analysis screen once per session (the shell memoizes it).

    Returns ``build_screen``'s ``SimpleNamespace(screen, controls, widgets,
    show, relabel)``: the shell displays ``screen`` as a sibling of MapApp,
    and its ``_ScreenBridge`` calls ``show`` and ``relabel``. Built once (no
    re-render) so the sub-builders' handlers + subscriptions are wired exactly
    once and the raw widget tree survives every show and hide.

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
        Source of truth for the alerts table + navigation, and the screen's
        title (the project and its active set).
    gee_interface : GEEInterface
        Session GEE interface, used for the views' Earth Engine tile URLs.
    theme_state : ThemeState, optional
        Shared app theme state: the views and the screen track the app's
        light/dark theme exactly as the shared main map does.
    """
    # Build the shared heavy widgets once.
    widgets = AnalysisWidgets(gee_interface=gee_interface, theme_state=theme_state)
    # The visualization layers marked for Analysis, switched off on every view
    # (spec §10); the layout is built once per session, so wired once and the
    # subscription lives as long as the kernel (its unsubscribe is not kept).
    vis_sync.wire(widgets, app_state)

    # Alert-id field (raw ipyvuetify so view_actual_alert can imperatively set
    # .v_model). Owned here, passed to the counter's jump menu (build_context,
    # through build_controls) and to the per-alert subscription, which writes
    # the 1-based number the queue counter shows. Empty: with no alert
    # selected there is no number to show, and ``_on_go`` ignores a field it
    # cannot read.
    id_field = v.TextField(
        v_model=None,
        outlined=True,
        dense=True,
        single_line=True,
        hide_details=True,
        type="number",
    )

    wire_alert_navigation(widgets, app_state, analysis, app_tile_model, id_field)

    # TODO(recipe-load): the OLD tile rebuilt/merged alerts_gdf from the
    # selected-alerts traits here (create_gdf_partial/create_gdf_full + the
    # alerts_bbs/alerts_total_bbs observers). That is superseded by the Filter
    # tile publishing app_state.alerts_gdf. The recipe-LOAD re-discovery/merge
    # path (re-pointing previously reviewed alerts) is deferred to a later task.

    # The one shared image timeline, under both views. Built HERE because
    # ``build_analysis_layout`` runs once per session
    # (``solara_app``'s ``solara.use_memo(..., dependencies=[])``), and
    # ``build_timeline`` registers five reactive subscriptions and discards
    # their unsubscribers: correct for a builder that lives as long as the
    # kernel, a compounding leak anywhere that re-runs per alert or per screen
    # entry. It needs no alert: an alert change reaches it through
    # ``widgets.imagery_ctx``.
    widgets.timeline = build_timeline(app_state, analysis, widgets)

    # One subscription per view, so a pick on the strip rebuilds that view's
    # image from its granule keys and draws it. Side-effect only (the two
    # unsubscribers are discarded for the same reason as above).
    wire_panes(widgets, analysis, selected_alerts_model)

    # The timeline's image-area control: a new area for the alert on screen.
    def _on_area(_value):
        try:
            set_image_area(widgets, analysis)
        except Exception:
            logger.exception("Analysis: changing the image area failed")

    analysis.image_area.subscribe(_on_area)

    # The review console -- the head, block A, block B, the Questions and
    # Refine sections and the sticky footer (``build_controls``) -- in the
    # screen's right column.
    controls = build_controls(
        widgets=widgets,
        app_state=app_state,
        analysis=analysis,
        aux_model=aux_model,
        app_tile_model=app_tile_model,
        id_field=id_field,
    )
    return build_screen(widgets, controls, app_state, theme_state)
