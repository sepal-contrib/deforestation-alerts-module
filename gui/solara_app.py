"""Solara entry for the Forest Cover Loss Alerts app.

Session bootstrap (pysepal solara pattern) + MapApp.element() shell: the rail
(About, Project, Overview) and the project workflow in the right panel (AOI →
Alerts → Period → Download & harmonize → Integrate & vectorize → Analysis).
Run: ./run_ui.sh (Voila, as on SEPAL) or
solara run gui/solara_app.py --port 8900 --no-open
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

from pysepal.i18n import current_locale
from pysepal.scripts.utils import init_ee
from pysepal.mapping import SepalMap
from pysepal.sepalwidgets.vue_app import MapApp
from pysepal.solara import (
    get_current_gee_interface,
    get_current_theme_state,
    prime_dev_auth,
    setup_sessions,
    setup_solara_server,
    setup_theme_colors,
    with_sepal_sessions,
)
from pysepal.solara.notifications import NotificationProvider, use_notifications

from deforestation_alerts.gdal_env import (
    configure_gdal_tmpdir,
    keep_gdal_sidecars_visible,
    pin_gdal_num_threads,
)
from gui.scripts.tile_proxy import borrow_localtileserver_prefix, prefer_http_proxy
from gui.store.navigation import (
    apply_navigation,
    dismiss_dialog,
    page_view_for_rail_step,
    resolve_navigation,
)
from gui.i18n import app_available_locales, t
from gui.scripts import set_store
from gui.scripts.map_layers import MapLayers
from gui.scripts.remote_facts import RemoteFactsLoader
from gui.scripts.set_context import SetContext
from gui.scripts.solara_threads import spawn_in_context
from gui.store.autosave import install_autosave
from gui.store.state_manager import AppState
from gui.scripts.detached import render_detached
from gui.scripts.notify_bridge import install_server_log_handler, install_task_log_handler
from gui.scripts.reacton_widgets import find_rendered_widget
from gui.store.workflow_steps import RAIL_ITEMS, STEPS, active_set, rail_item_for_step
from gui.tile.about_tile import AboutTile
from gui.tile.alerts_step import AlertsStep
from gui.tile.analysis import build_analysis_layout
from gui.tile.analysis.state import AnalysisState
from gui.tile.aoi_step import AoiStep
from gui.tile.integrate_step import IntegrateStep
from gui.tile.layers_step import LayersStep
from gui.tile.overview_tile import OverviewTile
from gui.tile.period_step import PeriodStep
from gui.tile.prepare_step import PrepareStep
from gui.tile.project_dialog import ProjectDialog
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
# WARNING+ records (a failed worker, with its traceback) also go to the
# server's own stderr: the kernel's sys.stderr is the browser's, where Voila
# shows nothing, and the task-pill handler above would otherwise be the only
# handler, which silences Python's last-resort stderr fallback.
install_server_log_handler()

# Point GDAL's temp-file allocator at a directory proven writable, ONCE per
# process. This used to ride along on the import of the (now deleted)
# ``deforestation_alerts.parameter`` package, whose only other job was a
# container-local ``module_dir`` that no longer exists. It lives here
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


@solara.component
def _NavBridge(app_state, nav_model, mapapp_ref):
    """Programmatic navigation: tiles' ``current_page_view`` writes → shell state.

    Tiles signal navigation by writing ``app_state.current_page_view``
    (a finished Integrate → Analysis, Overview "Review"/"Go" → Analysis,
    Overview "Workflow" → the active step, a new or opened project → AOI).
    This invisible leaf component resolves the string to a workflow index or
    a rail step id and applies it. It is a *leaf* on purpose: only this
    component re-renders on a nav event, so ``Page`` / ``MapApp`` are not
    rebuilt.

    Threading: the render and the effect run in whichever thread set the
    reactive. Integrate sets it from its worker thread, so ``_drive`` then
    runs on that worker, not on the kernel's event loop (reacton renders
    synchronously in the setting thread; see ``_ActiveSetLoader``). It may
    only set traits and reactives, as the workers themselves do; it must
    never start loop-only work (``use_task``, ``asyncio.create_task``).
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
    panel title/icon, and the ``current_page_view`` mirror. (Layers on the
    shared map belong to the session's ``MapLayers`` registry, not to the
    showing step, spec §4.3.)

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
        # Order matters on leave: the shared map is displayed again BEFORE
        # the stage hides. Leaflet measured it as 0x0 while it was hidden, and
        # the stage's hide nudge (a window resize, see StageHost) must find it
        # displayed to re-measure it; the frontend applies these two messages
        # in the order they are sent. The stage also always covers the map
        # while it hides or comes back, so nothing flashes.
        if workflow and spec.full_stage:
            stage_host.visible = True
            map_.add_class("defo-map-hidden")
        else:
            map_.remove_class("defo-map-hidden")
            stage_host.visible = False
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
    """Per-kernel session bootstrap (pysepal's sessions; its cleanup is returned)."""
    return setup_sessions()


def compute_title(app_state) -> str:
    """The app bar title: the app's name, then the open project's (spec §4.1).

    Peeks: it subscribes its caller to nothing (see ``_AppShell``'s title).
    """
    p = app_state.project.peek()
    return f"{t('app.title')} · {p.name}" if p is not None else t("app.title")


def use_app_title(app_state) -> str:
    """The title, recomputed only when a project is installed (opened, created
    or closed) or the language changes: never on a commit (ruling #91).
    Reading the locale here subscribes the shell, so a switch re-renders it."""
    locale = current_locale()
    return solara.use_memo(
        lambda: compute_title(app_state), dependencies=[app_state.project_loaded_signal.value, locale]
    )


@solara.component
def _ActiveSetLoader(app_state, layers, map_):
    """Load the active alert set into ``alerts_gdf`` and reset the map when a
    project is installed. A leaf component so only it re-renders on these changes.

    The read runs on a worker started from an effect, not in a
    ``prefer_threaded=False`` task: Integrate records a project's first set
    through ``update_project`` on its worker thread, so this component
    re-renders there, and solara starts such a task with a bare
    ``asyncio.create_task``, which raises without a running event loop.

    A new active set in the same project is left alone when ``alerts_gdf``
    already holds it (Integrate publishes the set it records). A newly
    installed project is always read from disk: set ids repeat across
    projects, so alerts on hand can never stand in for its set.
    """
    notifications = use_notifications()
    p = app_state.project.value
    signal = app_state.project_loaded_signal.value
    s = active_set(p)
    name = p.name if p is not None else None
    key = (signal, name, s.id if s is not None else None)
    # The install signal and project of the last load started, and a counter
    # that drops a load superseded by a newer one.
    loads = solara.use_ref({"signal": None, "name": None, "run": 0})

    def _on_install():
        layers.clear()
        if p is not None and p.aoi is not None:
            try:
                map_.zoom_bounds(p.aoi.bounds)
            except Exception:
                logger.exception("could not zoom to the AOI")

    solara.use_effect(_on_install, [signal])

    def _start_load():
        last = loads.current
        installed = last["signal"] != signal
        if not installed and last["name"] != name:
            # AppState._install publishes the project before it bumps the
            # signal: that bump follows within the same install, and loads.
            return
        last.update(signal=signal, name=name, run=last["run"] + 1)
        run = last["run"]
        if s is None:
            app_state.alerts_gdf.set(None)
            return

        def _kept():
            return not installed and set_store.holds_set(app_state.alerts_gdf.value, s)

        def _load():
            if _kept():
                return
            store = set_store.active_store_path(p)
            if store is not None and not store.exists() and loads.current["run"] == run:
                # Review focus 4: the set's files were deleted or moved. The
                # set is left unloaded (nothing to review) and the user is
                # told where the store was and what rebuilds it.
                notifications.warning(t("steps.project.set_missing", path=str(store)))
            try:
                gdf = set_store.load_active_alerts(p)
            except Exception as exc:  # noqa: BLE001 - reported, the set stays unloaded
                logger.exception("could not load the alerts of set %s", s.id)
                if loads.current["run"] == run:
                    # A Go into this set (Overview) will not land: forgotten,
                    # so a later publish of the set opens at its saved position.
                    set_store.drop_go_wish(app_state, s.id)
                    notifications.error(t("steps.project.set_load_failed", error=str(exc) or exc.__class__.__name__))
                return
            if loads.current["run"] != run or _kept():
                return  # a newer load runs, or Integrate published the set meanwhile
            # Checked again with the publish, atomically: Integrate may publish right now.
            set_store.publish_set_alerts(app_state, p.name, s, gdf, unless_held=not installed)

        spawn_in_context(_load)

    solara.use_effect(_start_load, [key])


def legend_props(app_state, translate) -> dict:
    """Props for pysepal's LegendComponent from the published legends (spec §9.4).

    Translated at every render, so a locale switch re-translates the legend
    on screen. Hidden while the workflow shows a full-stage step (Analysis):
    the legend's z-index (150) would float it over the stage (z-index 4).
    """
    from dataclasses import asdict

    from gui.scripts.legend_data import resolve_label, to_legend_data

    legends = app_state.layer_legends.value
    selected = app_state.selected_legend.value
    current = next((e for e in legends if e.layer_id == selected), None)
    full_stage = app_state.panel_view.value == "workflow" and STEPS[app_state.active_step.value].full_stage
    return {
        "legend_data": asdict(to_legend_data(current.spec, translate)) if current else {},
        "selector_options": [{"value": e.layer_id, "text": resolve_label(e.label, translate)} for e in legends],
        "selected": current.layer_id if current else "",
        "visible": bool(legends) and not full_stage,
    }


@solara.component
def MapLegend(app_state, layers):
    """The floating legend of the shared map: a leaf, so legend churn never
    re-renders the shell (it renders on the thread that published, like the
    other leaves; it starts no task).
    """
    from pysepal.solara.components.legend import LegendComponent

    props = legend_props(app_state, t)
    LegendComponent(
        legend_data=props["legend_data"],
        selector_options=props["selector_options"],
        selected=props["selected"],
        event_set_selected=layers.select,
        visible=props["visible"],
    )


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
        # widths onto :root.
        # Its z-index is the LOWEST that still covers the map, because the
        # layers that must show over it are stacked from small values (measured
        # in the running app): MapApp's `.map-background` (the shared map's
        # stacking context) 0 < stage 4 < the rail collapse arrow 5 < the rail
        # and the right panel 6 < menus 8 < the task pill 170 < the dialog
        # scrim 201 < dialogs 202+ (Vuetify's stack minimum, raised by 2 per
        # open dialog) < toasts 9999. At 801 the stage covered the About and
        # Project dialogs and the task pill.
        # The stage's own maps and menus stack inside it (a fixed, z-indexed
        # element is its own stacking context), and a menu opened from the
        # stage is placed above it (Vuetify starts from the activator's z).
        ".defo-stage {\n"
        "  position: fixed; top: 0; bottom: var(--sepal-bottom-reserved, 0px);\n"
        "  left: var(--sepal-drawer-width, 60px);\n"
        "  right: var(--sepal-right-panel-width, 0px);\n"
        "  z-index: 4; padding: 16px; overflow: auto;\n"
        "  background-color: var(--v-background-base, #ffffff);\n"
        "  transition: left 0.3s ease, right 0.3s ease;\n"
        "}\n"
        # The rail collapse/expand arrow sits on the stage's left edge: keep it
        # above the stage (MapApp's own value, restated here so the whole order
        # above is in one place) and below the dialogs.
        ".v-application .sidebar-controls { z-index: 5 !important; }\n"
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
    theme_state = get_current_theme_state()

    # Session state (the Project, navigation, alert review) — memoized so it
    # is not re-created on every re-render (which would discard every reactive
    # value mid-session). The auto-save hook saves the project after every
    # committed change (spec D11).
    app_state = solara.use_memo(AppState, dependencies=[])
    solara.use_effect(lambda: install_autosave(app_state), dependencies=[])

    # Analysis keeps its review state in Solara reactives, one per session
    # (spec §10), and reads the active alert set through one SetContext, under
    # the recipe-era names it still uses.
    analysis = solara.use_memo(AnalysisState, dependencies=[])
    set_context = solara.use_memo(lambda: SetContext(app_state), dependencies=[])

    # The ONE shared map. Memoized so the SAME instance is handed to MapApp AND
    # to every map-bearing step (AOI, Alerts, Download & harmonize, Integrate,
    # Overview draw their layers + popups on it). Re-creating it per render
    # would desync the steps (which capture it once) from the map MapApp
    # renders. The SATELLITE basemap is added once here.
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
    # The session's one registry of layers toggled on the shared map (spec
    # §4.3); cleared when a project is installed (_ActiveSetLoader). It
    # publishes the legends of its layers into AppState (spec §9.4), which
    # the MapLegend leaf renders.
    layers = solara.use_memo(
        lambda: MapLayers(map_, legends=app_state.layer_legends, selected=app_state.selected_legend),
        dependencies=[],
    )

    # MapApp step/dialog content is a synced traitlets `Instance(DOMWidget)` slot
    # (MapApp.vue binds `:widget="activeStep.content"`), NOT a reacton child — so a
    # raw @solara.component element is not rendered there. Render each Solara tile to
    # a concrete widget (gui/scripts/detached.py) and hand MapApp that single widget.
    # Memoized so the widget is stable across re-renders, and each step is
    # mounted once per session: steps follow project switches themselves.
    # nav_model mirrors MapApp's rail step (see below); mapapp_ref holds the
    # rendered MapApp widget, resolved once it is mounted (_resolve_mapapp).
    # Both exist before the tiles so the Project dialog can close itself.
    nav_model = solara.use_memo(lambda: _NavModel(), dependencies=[])
    mapapp_ref = solara.use_memo(lambda: {}, dependencies=[])
    about_widget = solara.use_memo(
        lambda: render_detached(lambda: AboutTile(), t("app.drawer_item.about"))[0], dependencies=[])
    project_widget = solara.use_memo(
        lambda: render_detached(lambda: ProjectDialog(
            app_state=app_state, dismiss=lambda: dismiss_dialog(mapapp_ref.get("widget"), nav_model)), t("app.drawer_item.project"))[0],
        dependencies=[])
    overview_widget = solara.use_memo(
        lambda: render_detached(lambda: OverviewTile(app_state=app_state, map_=map_,
                                                     layers=layers), t("app.drawer_item.overview"))[0], dependencies=[])
    step_widgets = solara.use_memo(
        lambda: {
            "aoi": render_detached(lambda: AoiStep(app_state=app_state, map_=map_, gee_interface=gee_interface),
                                   t("app.drawer_item.aoi"))[0],
            "alerts": render_detached(lambda: AlertsStep(app_state=app_state, map_=map_,
                                                         gee_interface=gee_interface, layers=layers),
                                      t("app.drawer_item.alerts"))[0],
            "period": render_detached(lambda: PeriodStep(app_state=app_state, layers=layers),
                                      t("app.drawer_item.period"))[0],
            "prepare": render_detached(lambda: PrepareStep(app_state=app_state, map_=map_,
                                                           gee_interface=gee_interface, layers=layers),
                                       t("app.drawer_item.prepare"))[0],
            "integrate": render_detached(lambda: IntegrateStep(app_state=app_state, map_=map_,
                                                               gee_interface=gee_interface),
                                         t("app.drawer_item.integrate"))[0],
            "layers": render_detached(lambda: LayersStep(app_state=app_state, map_=map_,
                                                         gee_interface=gee_interface, layers=layers),
                                      t("app.drawer_item.layers"))[0],
        },
        dependencies=[],
    )

    # Analysis: build the raw stage + controls once. The stage (two-map
    # workspace) renders in the StageHost overlay; the controls render in the
    # docked right panel. Raw widgets survive their container's rebuilds.
    analysis_stage_widget, analysis_controls_widget = solara.use_memo(
        lambda: build_analysis_layout(
            aux_model=set_context,
            selected_alerts_model=set_context,
            analysis=analysis,
            app_tile_model=set_context,
            app_state=app_state,
            gee_interface=gee_interface,
            theme_state=theme_state,
        ),
        dependencies=[],
    )

    # -----------------------------------------------------------------------
    # Two-view right panel (spatial_risk parity + the Overview view).
    #
    # Workflow view: PipelineHeader + the active workflow step's controls.
    # Overview view: the active set's summary, exports and tiles on the shared
    # map, entered from its rail button. Which view shows is
    # app_state.panel_view; which workflow step is active is
    # app_state.active_step. nav_model.current_step tracks the rail (dialogs +
    # the highlighted Overview item).
    # -----------------------------------------------------------------------
    # Throwaway content for the Overview "panel" rail entry: never rendered
    # (display != step/dialog), but a non-empty content list is what makes
    # MapApp set current_step on click.
    panel_sentinel = solara.use_memo(lambda: v.Html(tag="div"), dependencies=[])

    # Every panel view's controls wrapped in a div we SHOW/HIDE. ALL wrappers
    # stay mounted in panel_host at all times; on view/step change we toggle
    # their visibility instead of swapping panel_host.children.
    # WHY (not a children swap): swapping detaches the previous widget, and a
    # tile's `solara.display`-captured controls live in an `Output` whose view
    # is NOT recreated when the widget is reattached — so step cards vanished
    # after navigating away and back. `display:none` keeps the DOM —
    # and the widget views — alive. Keyed by step key / rail key; AOI is the
    # default-visible one (active_step 0, panel_view "workflow").
    panel_controls = {**step_widgets, "analysis": analysis_controls_widget, "overview": overview_widget}

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
            children=[render_detached(lambda: WorkflowHeader(app_state=app_state), t("app.panel_workflow_title"))[0]],
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
    # window resize on show and on hide (the hide nudge re-measures the
    # shared map, see _PanelBridge).
    stage_host = solara.use_memo(
        lambda: StageHost(content=[analysis_stage_widget]), dependencies=[]
    )

    # Rail clicks: a panel item (Overview) switches the right panel to its
    # view; a dialog item leaves the view alone; every item mirrors its page
    # view so repeated triggers keep working.
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
    rail_content = {"about": about_widget, "project": project_widget, "overview": panel_sentinel}
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

    # The title names the open project. Only a new, opened or closed project
    # (or a language switch) changes it, so the shell follows the install
    # signal and the locale, never the project:
    # reading the project would re-render the whole shell on every commit
    # (ruling #91). A leaf cannot set the title on the widget instead: reacton
    # re-applies MapApp's element props whenever this context renders again.
    app_title = use_app_title(app_state)
    # The floating legend, a leaf mounted before MapApp (spec §9.4).
    MapLegend(app_state=app_state, layers=layers)
    app_element = MapApp.element(
        app_title=app_title,
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
    # + dismiss dialogs (the part the Vue current_step watcher skips); the
    # holder itself is created above, with nav_model.

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
    _ActiveSetLoader(app_state=app_state, layers=layers, map_=map_)
    RemoteFactsLoader(app_state=app_state, gee_interface=gee_interface)
