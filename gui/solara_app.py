"""Solara entry for the Forest Cover Loss Alerts app (Phase 4).

Session bootstrap (pysepal solara pattern) + MapApp.element() shell. Tiles are
added to steps_data as they are ported (Tasks 8-9); About is first.
Run: solara run gui/solara_app.py --port 8900 --no-open
"""

import logging
import os
import sys
from pathlib import Path

# `solara run gui/solara_app.py` exec's this file without putting the repo root
# on sys.path, so the first-party imports below (deforestation_alerts, gui)
# would fail. Add the repo root (gui/solara_app.py -> parents[1]) up front so
# the app loads regardless of how solara is invoked or the process CWD.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import ipyvuetify as v
import solara
from traitlets import HasTraits, Int

from pysepal.scripts.utils import init_ee
from pysepal.mapping import SepalMap
from pysepal.sepalwidgets.vue_app import MapApp
from pysepal.solara import (
    get_current_gee_interface,
    get_current_sepal_client,
    get_current_theme_state,
    prime_dev_auth,
    setup_sessions,
    setup_solara_server,
    setup_theme_colors,
    with_sepal_sessions,
)
from pysepal.solara.notifications import NotificationProvider

from deforestation_alerts.gdal_env import (
    configure_gdal_tmpdir,
    keep_gdal_sidecars_visible,
    pin_gdal_num_threads,
)
from gui.scripts.tile_proxy import borrow_localtileserver_prefix, prefer_http_proxy
from deforestation_alerts.message import cm
from deforestation_alerts.model.aoi_date_model import AoiDateModel
from deforestation_alerts.model.alerts_model import AlertFilterModel, SelectedAlertsModel
from deforestation_alerts.model.analysis_model import AlertAnalysisModel
from deforestation_alerts.model.app_model import AppTileModel
from deforestation_alerts.model.aux_model import AuxModel
from deforestation_alerts.scripts.workspace import Workspace
from gui.store.navigation import (
    apply_navigation,
    page_view_for_rail_step,
    resolve_navigation,
)
from gui.i18n import app_available_locales, t
from gui.scripts.scratch_registry import cleanup_kernel_scratch, register_scratch_owner
from gui.store.state_manager import AppState, aoi_name_from_model
from gui.scripts.notify_bridge import install_task_log_handler
from gui.scripts.reacton_widgets import find_rendered_widget
from gui.store.workflow_steps import RAIL_ITEMS, STEPS, rail_item_for_step
from gui.tile.about_tile import AboutTile
from gui.tile.aoi_tile import AoiTile
from gui.tile.alerts_filter_tile import AlertsFilterTile
from gui.tile.analysis import build_analysis_layout
from gui.tile.aux_tile import build_aux_layout
from gui.tile.overview_tile import OverviewTile
from gui.tile.recipe_tile import build_recipe_layout
from gui.widget.stage_host import StageHost
from gui.widget.workflow_header import WorkflowHeader

logger = logging.getLogger("SEPALUI.deforestation_alerts")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Forward INFO+ app log records of tracked jobs into pysepal's task pill (gui/scripts/notify_bridge.py).
install_task_log_handler()
# The bridge only sees records the app logger lets through. Nothing else
# configures it, so under a bare process, `solara run` and Voila its effective
# level is the root's WARNING and every INFO milestone would be dropped before
# reaching the handler. Raise it to INFO — but never raise a level someone
# deliberately set lower (e.g. DEBUG while troubleshooting).
if logger.level == logging.NOTSET or logger.level > logging.INFO:
    logger.setLevel(logging.INFO)

# Point GDAL's temp-file allocator at a directory proven writable, ONCE per
# process. This used to ride along on the import of the (now deleted)
# ``deforestation_alerts.parameter`` package, whose only other job was the
# container-local ``module_dir`` the Workspace adapter replaced. It lives here
# because this module is the app's single entry point (``solara run
# gui/solara_app.py`` and ``ui.ipynb`` both import it), and it is import-time,
# not render-time: it is a local mkdir + write probe, never a network call.
configure_gdal_tmpdir()

# Tile transport and GDAL guards, before any TileClient exists. On SEPAL the
# sandbox sets LOCALTILESERVER_CLIENT_PREFIX=/api/sandbox/jupyter/proxy/{port};
# PMTiles borrow it and both tile servers skip the comm bridge (verified on
# test.sepal.io 2026-09-28 in spatial_risk: rasters 200, PMTiles 206).
keep_gdal_sidecars_visible()
pin_gdal_num_threads()
borrow_localtileserver_prefix()
prefer_http_proxy()


class _NavModel(HasTraits):
    """Tiny HasTraits bridge so we can observe MapApp's active step from Python.

    Passed to ``MapApp.element(model=...)``; MapApp links its ``current_step``
    trait to this model's (``_setup_model_binding`` links matching trait names).
    The Vue drawer sets ``MapApp.current_step`` on step activation, which flows
    here, letting the right-panel host swap to the active tile's controls.
    """

    current_step = Int(allow_none=True, default_value=None)


def _render_detached(make_element):
    """Render a Solara tile component to a standalone widget for MapApp.

    WHY THIS EXISTS (the alternative is a duplicate tile stacked over the map):
    reacton auto-parents an ``Element`` into the active render context the moment
    it is *constructed* (``Element.__init__`` calls ``container_adders[-1].add``).
    So the obvious ``solara.render_fixed(SomeTile(...))`` builds ``SomeTile(...)``
    while *Page's* render is active, which adds the tile to Page's OWN output tree
    — every tile then renders twice: once leaked inline into Page (all tiles
    stacked in one tall sheet, bleeding over the fullscreen map — e.g. the Aux
    "Planet Login Data" card floating bottom-right) AND once via render_fixed for
    MapApp's dialog/panel/step slot.

    Building the element with Page's ``container_adders`` temporarily detached
    means the construction does NOT auto-add to Page; only ``render_fixed`` (in
    its own isolated context) renders it, and we hand MapApp that single widget.

    Args:
        make_element: zero-arg callable that constructs the tile Element. Must be
            a thunk (not a pre-built element) so the construction happens here,
            after the adders are detached.
    """
    import reacton.core as _rcore

    rc = _rcore.get_render_context(required=False)
    if rc is None:
        return solara.render_fixed(make_element())[0]
    saved = rc.container_adders[:]
    del rc.container_adders[:]
    try:
        return solara.render_fixed(make_element())[0]
    finally:
        rc.container_adders[:] = saved


@solara.component
def _NavBridge(app_state, nav_model, mapapp_ref):
    """Programmatic navigation: tiles' ``current_page_view`` writes → shell state.

    Tiles signal navigation by writing ``app_state.current_page_view`` (AOI
    search → Filter, Filter analyze → Analysis, Recipe load → Analysis,
    Overview "Go"/"Review alerts" → Analysis, Overview "Workflow" → the active
    step, Recipe new → Optional files). This invisible leaf component resolves
    the string to a workflow index or a rail step id and applies it. It is a
    *leaf* on purpose: only this component re-renders on a nav event, so
    ``Page`` / ``MapApp`` are not rebuilt.

    Threading: search/analyze run in worker threads; they only set the
    reactive. Solara marshals this component's re-render + effect onto the
    main thread, so all widget mutation here stays main-thread.
    """
    view = app_state.current_page_view.value

    def _drive():
        action = resolve_navigation(view)
        if action is None:
            return
        # Loop-safety: the target is already showing (e.g. _PanelBridge just
        # mirrored the showing view back into current_page_view).
        if (
            action.kind == "workflow"
            and app_state.panel_view.value == "workflow"
            and action.target == app_state.active_step.value
        ):
            return
        if action.kind == "rail" and action.target == nav_model.current_step:
            return
        apply_navigation(mapapp_ref.get("widget"), nav_model, app_state, action)

    solara.use_effect(_drive, [view])


@solara.component
def _PanelBridge(
    app_state, panel_wrappers, header_wrapper, stage_host, map_, mapapp_ref, workflow_config
):
    """Apply the panel view + workflow position to the widgets: wrapper and
    header visibility, the Analysis stage overlay + shared-map hiding, the
    shared-map owner, the panel title/icon, and the ``current_page_view``
    mirror.

    A leaf component (same reason as ``_NavBridge``). Runs whenever
    ``panel_view`` or ``active_step`` change, whoever set them (header click,
    rail click, navigation bridge).
    """
    view = app_state.panel_view.value
    active = app_state.active_step.value

    def _apply():
        workflow = view == "workflow"
        spec = STEPS[active]
        showing_key = spec.key if workflow else view
        header_wrapper.style_ = "" if workflow else "display: none;"
        for key, wrap in panel_wrappers.items():
            wrap.style_ = "" if key == showing_key else "display: none;"
        # Full-stage step (Analysis, Workflow view only): show the overlay and
        # hide the shared map so its leaflet layer-control menu can't bleed
        # over the panel. Other steps' layers stay mounted and return on leave.
        stage_host.visible = workflow and spec.full_stage
        if stage_host.visible:
            map_.add_class("defo-map-hidden")
        else:
            map_.remove_class("defo-map-hidden")
        # Shared-map owner. Workflow steps that own layers (AOI/Filter) claim
        # it; Analysis owns its own maps and must NOT change it. The Overview
        # view claims it too — that is what makes OverviewTile draw its status
        # clusters (its effects key on active_map_view == "overview_tile").
        owner = None
        if workflow:
            if spec.owns_map:
                owner = spec.page_view
        else:
            item = next((r for r in RAIL_ITEMS if r.key == view), None)
            if item is not None and item.owns_map:
                owner = item.page_view
        if owner is not None and app_state.active_map_view.value != owner:
            app_state.active_map_view.value = owner
        # Panel title/icon follow the view (pysepal RightPanel.config is a
        # synced trait; keep the width so the config observer is a no-op).
        mapapp = mapapp_ref.get("widget")
        if mapapp is not None and mapapp.right_panel:
            panel = mapapp.right_panel[0]
            if workflow:
                panel.config = {**workflow_config}
            else:
                item = rail_item_for_step(next(r.step_id for r in RAIL_ITEMS if r.key == view))
                panel.config = {**workflow_config, "title": t(item.label_key), "icon": item.icon}
        # Mirror: keep current_page_view faithful to what is showing so a
        # *repeated* trigger (e.g. searching twice) still changes the value and
        # re-fires _NavBridge, whose guard then no-ops.
        showing_view = spec.page_view if workflow else next(
            r.page_view for r in RAIL_ITEMS if r.key == view
        )
        if app_state.current_page_view.value != showing_view:
            app_state.current_page_view.value = showing_view

    solara.use_effect(_apply, [view, active])


init_ee()
setup_solara_server()

# PYSEPAL_DEV_AUTH (local dev against a real SEPAL) logs in with a blocking
# POST; prime it once here so it stays off the render path. prime_dev_auth()
# raises when the flag is not armed, hence the guard.
if os.getenv("PYSEPAL_DEV_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}:
    prime_dev_auth()


@solara.lab.on_kernel_start
def on_kernel_start():
    """Per-kernel session bootstrap + the teardown that sweeps its scratch.

    ``setup_sessions()`` returns pysepal's own cleanup; we wrap it so the
    container-local scratch this kernel's ``Workspace`` staged (analyze rasters,
    staged alert stores, the CNN composite + prediction) is deleted when the
    kernel dies. Without it those files outlive the session and the container's
    tmp grows for every run. The sweep is kernel-scoped — see
    ``gui.scripts.scratch_registry`` — so it can never touch another live
    kernel's scratch, and it runs FIRST so a failure in pysepal's cleanup cannot
    skip it (it swallows its own errors either way).
    """
    session_cleanup = setup_sessions()

    def _cleanup():
        try:
            cleanup_kernel_scratch()
        except Exception:  # pragma: no cover — cleanup_kernel_scratch never raises
            logger.exception("Kernel teardown: scratch sweep failed")
        if callable(session_cleanup):
            session_cleanup()

    return _cleanup


@solara.component
@with_sepal_sessions(module_name="deforestation_alerts")
def Page():
    setup_theme_colors()

    # Centralized user feedback, mounted ABOVE the shell. pysepal 4's
    # use_notifications() raises when no bus exists, and the provider creates
    # the bus when it renders -- after its parent's body has run. The tiles
    # are built with solara.render_fixed inside the shell's body, so the
    # provider must render first, as an earlier sibling of _AppShell. MapApp
    # publishes --sepal-notification-right-offset so the pill tracks the
    # right panel.
    NotificationProvider()
    _AppShell()


@solara.component
def _AppShell():
    solara.Style(
        # Embedded-widget height fix: a tile renders raw ipyvuetify widgets via
        # `solara.display(card)`, and ipyvuetify wraps each standalone display in
        # its OWN Vuetify `<v-application>` whose `.v-application--wrap` carries
        # `min-height: 100vh`, so every card would reserve a full viewport height.
        ".dialog-card .v-application--wrap,\n"
        ".right-panel .v-application--wrap { min-height: 0 !important; }\n"
        # Hide the shared map while the Analysis stage owns the screen, so its
        # leaflet layer-control menu can't bleed over the panel.
        ".defo-map-hidden { display: none !important; }\n"
        # The Analysis stage: an app-owned fixed overlay between the left rail
        # and the right panel (StageHost). MapApp pushes the live rail and panel
        # widths onto :root; z-index 801 sits above the fullscreen map (800).
        ".defo-stage {\n"
        "  position: fixed; top: 0; bottom: var(--sepal-bottom-reserved, 0px);\n"
        "  left: var(--sepal-drawer-width, 60px);\n"
        "  right: var(--sepal-right-panel-width, 0px);\n"
        "  z-index: 801; padding: 16px; overflow: auto;\n"
        "  background-color: var(--v-background-base, #ffffff);\n"
        "  transition: left 0.3s ease, right 0.3s ease;\n"
        "}\n"
        # Keep the rail collapse/expand arrow (`.sidebar-controls`, z-index 5)
        # clickable over the stage; still below the dialog scrim (~1005).
        ".v-application .sidebar-controls { z-index: 802 !important; }\n"
        # Center modal dialogs over the MAP area (viewport minus the left rail):
        # `.v-dialog__content` is a fixed full-viewport flex box; shrink it by the
        # live rail width so a wide dialog (About is 1300px) is not tucked under
        # the rail's collapse handle. Scoped with :has to MapApp's dialogs.
        ".v-dialog__content:has(> .dialog-container) {\n"
        "  left: var(--sepal-drawer-width, 0px) !important;\n"
        "  width: calc(100% - var(--sepal-drawer-width, 0px)) !important;\n"
        "}\n"
        ".about-content { text-align: justify; }"
    )

    gee_interface = get_current_gee_interface()
    sepal_client = get_current_sepal_client()
    theme_state = get_current_theme_state()

    # The ONE boundary every user file goes through. Built here from the session
    # client and threaded into the tiles exactly like ``gee_interface`` — never
    # fetched at module import, where no session exists yet. Construction is
    # PURE: no ``ensure_results_dir()`` (or any other network call) here — this
    # body runs on the render path, which the adapter forbids blocking I/O on.
    # First run needs no mkdir anyway: every Workspace write creates its own
    # parents, and a missing results root lists as [] / exists as False.
    # Workspace() itself DOES raise on a missing/module-less client — that is a
    # broken session, and failing at startup beats failing mid-save.
    # Registered with the kernel-scoped scratch registry as it is built, so the
    # on_kernel_start cleanup above can delete everything it staged locally when
    # this kernel goes away (registration is a dict insert — no I/O on the render
    # path).
    def _make_workspace():
        ws = Workspace(sepal_client)
        register_scratch_owner(ws)
        return ws

    workspace = solara.use_memo(_make_workspace, dependencies=[])

    # Ephemeral UI/nav state — memoized so it is not re-created on every
    # Page re-render (which would discard all reactive values mid-session).
    app_state = solara.use_memo(lambda: AppState(), dependencies=[])

    # -----------------------------------------------------------------------
    # Domain model creation — session-stable, shared across tiles.
    # This is the shared-domain-model creation point: later tiles (AOI,
    # Filter, Recipe) will add their own domain models here in the same
    # use_memo pattern.
    # -----------------------------------------------------------------------
    aux_model = solara.use_memo(lambda: AuxModel(), dependencies=[])
    aoi_date_model = solara.use_memo(
        lambda: AoiDateModel(gee_interface=gee_interface), dependencies=[]
    )
    alert_filter_model = solara.use_memo(lambda: AlertFilterModel(), dependencies=[])
    selected_alerts_model = solara.use_memo(
        lambda: SelectedAlertsModel(), dependencies=[]
    )
    analyzed_alerts_model = solara.use_memo(
        lambda: AlertAnalysisModel(), dependencies=[]
    )
    app_tile_model = solara.use_memo(lambda: AppTileModel(), dependencies=[])

    # The ONE shared map. Memoized so the SAME instance is handed to MapApp AND
    # to every map-bearing tile (Overview/AOI/Filter draw their layers + popups
    # on it). Re-creating it per render would desync the tiles (which capture it
    # once via use_memo) from the map MapApp renders. The SATELLITE basemap is
    # added once here (the OLD per-tile maps each added it) so every tile shares
    # the satellite context.
    def _make_shared_map():
        m = SepalMap(
            gee_interface=gee_interface, fullscreen=True, theme_state=theme_state
        )
        try:
            m.add_basemap("SATELLITE")
        except Exception:
            logger.exception("Failed to add SATELLITE basemap to the shared map")
        return m

    map_ = solara.use_memo(_make_shared_map, dependencies=[])

    # MapApp step/dialog content is a synced traitlets `Instance(DOMWidget)` slot
    # (MapApp.vue binds `:widget="activeStep.content"`), NOT a reacton child — so a
    # raw @solara.component element is not rendered there. Render each Solara tile to
    # a concrete widget with solara.render_fixed and hand MapApp that single widget.
    # Memoized so the widget is stable across re-renders.
    about_widget = solara.use_memo(
        lambda: _render_detached(lambda: AboutTile()), dependencies=[]
    )

    # Overview tile: render_fixed so reactivity (use_effect on alerts_gdf) is
    # preserved while handing MapApp a concrete DOMWidget.  render_fixed leaves a
    # stale duplicate widget in the DOM — use_effect side-effects are idempotent so
    # double-mount is safe.
    overview_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: OverviewTile(
                app_state=app_state, map_=map_, workspace=workspace,
                aoi_date_model=aoi_date_model,
                selected_alerts_model=selected_alerts_model,
            )
        ),
        dependencies=[],
    )

    # Aux tile: build the RAW ipyvuetify layout (memoized; .bind() chain created
    # once) and hand it straight to MapApp. NOT via render_fixed + solara.display:
    # a solara.display-captured widget lives in an Output whose view is not
    # recreated when MapApp destroys the dialog DOM on close and rebuilds it on
    # reopen, so the cards vanish on the 2nd open. A raw widget survives reopen.
    aux_widget = solara.use_memo(
        lambda: build_aux_layout(aux_model, workspace, gee_interface),
        dependencies=[],
    )

    # AOI tile: render_fixed so its hooks (memoized map/AoiView, ccdc observer,
    # search use_task) are preserved while MapApp gets a concrete DOMWidget.
    aoi_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: AoiTile(
                aoi_date_model=aoi_date_model,
                alert_filter_model=alert_filter_model,
                aux_model=aux_model,
                app_state=app_state,
                gee_interface=gee_interface,
                map_=map_,
            )
        ),
        dependencies=[],
    )

    # Filter tile: render_fixed so its hooks (memoized map/draw control/form
    # widgets, the available_alerts_raster_list observer, the analyze use_task)
    # are preserved while MapApp gets a concrete DOMWidget.
    filter_widget = solara.use_memo(
        lambda: _render_detached(
            lambda: AlertsFilterTile(
                aoi_date_model=aoi_date_model,
                aux_model=aux_model,
                alert_filter_model=alert_filter_model,
                selected_alerts_model=selected_alerts_model,
                analyzed_alerts_model=analyzed_alerts_model,
                app_state=app_state,
                gee_interface=gee_interface,
                workspace=workspace,
                map_=map_,
            )
        ),
        dependencies=[],
    )

    # Recipe tile: build the RAW ipyvuetify layout (memoized; .bind() chain +
    # button handlers wired once) and hand it straight to MapApp. NOT via
    # render_fixed + solara.display — the Recipes dialog is the case the user hit:
    # a solara.display-captured widget's view is not recreated when MapApp
    # rebuilds the dialog DOM on reopen, so the New/Load cards vanished on the 2nd
    # open (only the reacton-managed buttons survived). A raw widget survives
    # reopen; the load runs on a worker thread inside build_recipe_layout.
    recipe_widget = solara.use_memo(
        lambda: build_recipe_layout(
            aux_model=aux_model,
            aoi_date_model=aoi_date_model,
            alert_filter_model=alert_filter_model,
            selected_alerts_model=selected_alerts_model,
            analyzed_alerts_model=analyzed_alerts_model,
            app_tile_model=app_tile_model,
            app_state=app_state,
            workspace=workspace,
            gee_interface=gee_interface,
        ),
        dependencies=[],
    )

    # Analysis tile: build the raw stage + controls once. The stage (two-map
    # workspace) renders in MapApp's step content; the controls render in the
    # docked right panel (panel wrapper 7). Raw widgets survive the step
    # container's v-if rebuild, same as the other raw-built tiles.
    analysis_stage_widget, analysis_controls_widget = solara.use_memo(
        lambda: build_analysis_layout(
            aux_model=aux_model,
            selected_alerts_model=selected_alerts_model,
            analyzed_alerts_model=analyzed_alerts_model,
            app_tile_model=app_tile_model,
            app_state=app_state,
            gee_interface=gee_interface,
            workspace=workspace,
            theme_state=theme_state,
        ),
        dependencies=[],
    )

    # -----------------------------------------------------------------------
    # Two-view right panel (spatial_risk parity + the Overview view).
    #
    # Workflow view: PipelineHeader + the active workflow step's controls
    # (AOI/Date, Filter, Analysis). Overview view: today's Overview tile
    # (summary table, exports, status clusters on the shared map), entered
    # from its rail button. Which view shows is app_state.panel_view; which
    # workflow step is active is app_state.active_step. nav_model.current_step
    # tracks the rail (dialogs + the highlighted Overview item).
    # -----------------------------------------------------------------------
    nav_model = solara.use_memo(lambda: _NavModel(), dependencies=[])
    # Throwaway content for the Overview "panel" rail entry: never rendered
    # (display != step/dialog), but a non-empty content list is what makes
    # MapApp set current_step on click.
    panel_sentinel = solara.use_memo(lambda: v.Html(tag="div"), dependencies=[])

    # Every panel view's controls wrapped in a div we SHOW/HIDE. ALL wrappers
    # stay mounted in panel_host at all times; on view/step change we toggle
    # their visibility instead of swapping panel_host.children.
    # WHY (not a children swap): swapping detaches the previous widget, and a
    # tile's `solara.display`-captured controls live in an `Output` whose view
    # is NOT recreated when the widget is reattached — so AOI/Filter cards
    # vanished after navigating away and back. `display:none` keeps the DOM —
    # and the widget views — alive. Keyed by step key / rail key; AOI is the
    # default-visible one (active_step 0, panel_view "workflow").
    panel_controls = {
        "aoi": aoi_widget,
        "filter": filter_widget,
        "analysis": analysis_controls_widget,
        "overview": overview_widget,
    }

    def _wrap(key, visible):
        return v.Html(
            tag="div",
            children=[panel_controls[key]],
            style_="" if visible else "display: none;",
        )

    panel_step_wrappers = solara.use_memo(
        lambda: {s.key: _wrap(s.key, s.key == STEPS[0].key) for s in STEPS},
        dependencies=[],
    )
    panel_rail_wrappers = solara.use_memo(
        lambda: {r.key: _wrap(r.key, False) for r in RAIL_ITEMS if r.display == "panel"},
        dependencies=[],
    )
    panel_wrappers = {**panel_step_wrappers, **panel_rail_wrappers}
    # The pipeline header: rendered detached (MapApp's right_panel_content is a
    # widget slot, not a reacton child), memoized so it is stable across Page
    # re-renders, and wrapped so the Overview view can hide it. It re-renders
    # on its own when the AppState reactives it reads change.
    header_wrapper = solara.use_memo(
        lambda: v.Html(
            tag="div",
            children=[_render_detached(lambda: WorkflowHeader(app_state=app_state))],
        ),
        dependencies=[],
    )
    panel_host = solara.use_memo(
        lambda: v.Container(
            fluid=True,
            class_="pa-0 ma-0",
            children=[header_wrapper, *panel_wrappers.values()],
        ),
        dependencies=[],
    )

    # The Analysis two-map stage, hosted by the app (not MapApp's step slot) in
    # a fixed overlay between the rail and the panel. Hidden until the Analysis
    # step is active in the Workflow view; the host nudges leaflet with a
    # window resize on show.
    stage_host = solara.use_memo(
        lambda: StageHost(content=[analysis_stage_widget]), dependencies=[]
    )

    # Rail clicks: a panel item (Overview) switches the right panel to its
    # view; a dialog item leaves the view alone; every item mirrors its page
    # view so Recipe → Optional files and repeated triggers keep working.
    # None (dialog closed, "Map" clicked) changes nothing — see spec D13.
    def _wire_rail_mirror():
        def _on_rail_step(change):
            sid = change["new"]
            item = rail_item_for_step(sid)
            if item is not None and item.display == "panel":
                app_state.panel_view.set(item.key)
            view = page_view_for_rail_step(sid)
            if view is not None and app_state.current_page_view.value != view:
                app_state.current_page_view.value = view

        nav_model.observe(_on_rail_step, "current_step")
        return lambda: nav_model.unobserve(_on_rail_step, "current_step")

    solara.use_effect(_wire_rail_mirror, dependencies=[])

    # AOI-name mirror for the pipeline header gates (a solara reactive cannot
    # subscribe to a traitlets model). None when no AOI geometry is selected.
    #
    # Observe ``object_set``, NOT ``feature_collection``: the latter is a plain
    # class attribute on AoiModel, not a traitlet, so ``observe`` accepts the
    # name and then silently never fires. ``object_set`` is a real Int trait
    # bumped on the LAST line of AoiModel.set_object(), i.e. after the _from_*
    # loader has assigned both the geometry and the name -- the only moment both
    # are guaranteed materialised, since 4 of the 5 loaders assign ``name``
    # *before* ``feature_collection``. ``name`` stays in the list so a
    # clear_attributes() reset (which nulls ``name`` but deliberately leaves
    # ``object_set`` alone) still clears the header.
    def _mirror_aoi_name():
        def _on_aoi(change=None):
            app_state.aoi_name.set(aoi_name_from_model(aoi_date_model))

        aoi_date_model.observe(_on_aoi, names=["object_set", "name"])
        _on_aoi()
        return lambda: aoi_date_model.unobserve(_on_aoi, names=["object_set", "name"])

    solara.use_effect(_mirror_aoi_name, dependencies=[])

    # Theme mirror for the pipeline header's "primary" accent. The app's theme
    # is owned by pysepal's ThemeState (MapApp's toggle), not by Solara's own
    # dark mode, so solara.lab.use_dark_effective() reports the wrong value in
    # this shell; and the header is rendered detached, so a toggle would not
    # re-render it anyway. Mirroring the trait into a reactive fixes both.
    def _mirror_theme():
        def _on_theme(change=None):
            app_state.dark.set(bool(theme_state.dark))

        theme_state.observe(_on_theme, names=["dark"])
        _on_theme()
        return lambda: theme_state.unobserve(_on_theme, names=["dark"])

    solara.use_effect(_mirror_theme, dependencies=[])

    WORKFLOW_PANEL_CONFIG = {
        "title": t("app.panel_workflow_title"),
        "icon": "mdi-tune",
        "width": 480,
        "toggle_icon": "mdi-chevron-right",
    }
    right_panel_content = [{"content": [panel_host]}]

    # MapApp drawer entries derive from RAIL_ITEMS (gui/store/workflow_steps.py).
    # Dialogs get their rendered tile; the Overview panel item gets the
    # sentinel (its controls live in panel_host) and opens the right panel.
    # Dialogs carry no right_panel_action — the panel stays as the user left
    # it behind a dialog, as in spatial_risk.
    rail_content = {
        "about": about_widget,
        "recipes": recipe_widget,
        "overview": panel_sentinel,
        "aux": aux_widget,
    }
    steps_data = [
        {
            "id": r.step_id,
            "name": t(r.label_key),
            "icon": r.icon,
            "display": r.display,
            "content": rail_content[r.key],
            **({"width": r.dialog_width} if r.dialog_width else {}),
            **({"right_panel_action": "open"} if r.display == "panel" else {}),
        }
        for r in RAIL_ITEMS
    ]

    app_element = MapApp.element(
        app_title=cm.app.title,
        app_icon="mdi-tree",
        repo_url="https://github.com/sepal-contrib/deforestation-alerts-module",
        main_map=[map_],
        steps_data=steps_data,
        initial_step=1,  # auto-open About at startup (rail step id 1)
        theme_state=theme_state,
        model=nav_model,
        right_panel_config=WORKFLOW_PANEL_CONFIG,
        right_panel_content=right_panel_content,
        right_panel_open=True,
        is_pinned=False,  # start as the 60px icon rail
        # Offer every catalog we ship (en, es-ES, fr); without it the default
        # LocaleSelect is English-only. The selector writes pysepal's Solara
        # locale, which gui.i18n.t() reads.
        locales=app_available_locales(),
    )

    # The stage overlay is a sibling of MapApp: a fixed
    # element inside the right drawer would be re-rooted by its transform.
    solara.display(stage_host)

    # Resolve the rendered MapApp widget ONCE here, in Page's own context — where
    # ``app_element`` is valid (``solara.get_widget`` must be called from the
    # component that created the element; calling it from _NavBridge raises
    # KeyError). _NavBridge reads it from this holder to open/close the right panel
    # + dismiss dialogs (the part the Vue current_step watcher skips).
    mapapp_ref = solara.use_memo(lambda: {}, dependencies=[])

    def _resolve_mapapp():
        try:
            mapapp_ref["widget"] = solara.get_widget(app_element)
        except KeyError:
            # A re-render before the mount effects run (a reactive set while a
            # child renders) leaves reacton's element->widget map keyed by the
            # FIRST pass's MapApp element, so get_widget() misses the element
            # this closure holds. The widget itself is the same one.
            widget = find_rendered_widget(MapApp)
            if widget is None:
                logger.error("Navigation: could not resolve the MapApp widget")
            mapapp_ref["widget"] = widget

    solara.use_effect(_resolve_mapapp, dependencies=[])

    _NavBridge(app_state=app_state, nav_model=nav_model, mapapp_ref=mapapp_ref)
    _PanelBridge(
        app_state=app_state,
        panel_wrappers=panel_wrappers,
        header_wrapper=header_wrapper,
        stage_host=stage_host,
        map_=map_,
        mapapp_ref=mapapp_ref,
        workflow_config=WORKFLOW_PANEL_CONFIG,
    )
