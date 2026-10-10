"""Shell wiring guards (the spatial_risk scaffold's getsource technique).

``gui.solara_app`` calls ``init_ee()`` at import time, so these assertions
read the source text instead of importing the module — same contract lock,
credential-free.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TILE_FILES = [
    "gui/tile/analysis/edit_tools.py",
    "gui/tile/analysis/export.py",
    "gui/tile/analysis/imagery.py",
]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_steps_data_derives_from_the_rail_registry():
    src = _src("gui/solara_app.py")
    assert "from gui.store.workflow_steps import RAIL_ITEMS, STEPS" in src
    assert '"id": r.step_id' in src, "steps_data must be built from RAIL_ITEMS"
    assert '"display": r.display' in src
    assert '**({"width": r.dialog_width} if r.dialog_width else {})' in src
    for literal in ('"id": 1', '"id": 2', "'id': 1"):
        assert literal not in src, f"hardcoded drawer id found: {literal}"
    assert "hides_shared_map" not in src
    assert "step_display" not in src and "step_actions" not in src


def test_the_app_starts_on_the_project_dialog():
    """Spec A4/D5: the Project dialog (rail step 2) opens at startup, not About."""
    from gui.store.workflow_steps import rail_item_for_step

    src = _src("gui/solara_app.py")
    assert "initial_step=2," in src and "initial_step=1" not in src
    assert rail_item_for_step(2).key == "project"


def test_workflow_panel_is_wired():
    src = _src("gui/solara_app.py")
    assert "WorkflowHeader(app_state=app_state)" in src
    assert '"content": [panel_host]' in src
    assert "children=[header_widget, *panel_wrappers.values()]" in src
    assert "right_panel_open=True" in src
    assert "is_pinned=False" in src
    assert 'WORKFLOW_PANEL_CONFIG = {' in src and 't("app.panel_workflow_title")' in src
    assert "StageHost" not in src
    assert "solara.display(analysis_layout.screen)" in src
    assert "_ScreenBridge(app_state=app_state, layout=analysis_layout)" in src
    # One view: every rail item is a dialog, so the panel holds the steps alone.
    assert "{s.key: _wrap(s.key, s.key == STEPS[0].key) for s in STEPS}" in src


def test_navigation_maps_derive_from_the_registries():
    src = _src("gui/store/navigation.py")
    assert "PAGE_VIEW_TO_WORKFLOW = {s.page_view: i for i, s in enumerate(STEPS)}" in src
    assert "PAGE_VIEW_TO_RAIL = {r.page_view: r.step_id for r in RAIL_ITEMS}" in src


def test_no_custom_log_console():
    """User feedback goes through pysepal's notifications (toasts + task pill);
    the old on-map process-log console is gone and must not come back."""
    src = _src("gui/solara_app.py")
    assert "LogConsole" not in src
    assert "log_bridge" not in src
    assert not (ROOT / "gui/widget/log_console.py").exists()


def test_no_bare_daemon_threads_in_tiles():
    """Background work must go through spawn_in_context (kernel-context bridge),
    never a bare threading.Thread."""
    for rel in TILE_FILES:
        src = _src(rel)
        assert "threading.Thread(" not in src, f"bare Thread in {rel}"
        assert "spawn_in_context" in src, f"{rel} lost its spawn_in_context import"


def test_geedim_exports_are_serialized_in_one_place():
    """geedim has ONE process-wide asyncio runner, so overlapping exports raise
    "This event loop is already running". The lock therefore belongs at the single
    entry point every caller shares, not at one call site."""
    src = _src("deforestation_alerts/scripts/raster_export.py")
    assert "_DOWNLOAD_LOCK = threading.Lock()" in src
    assert "_DOWNLOAD_LOCK.acquire(timeout=" in src, "must not block forever"
    assert "_DOWNLOAD_LOCK.release()" in src
    # and no UI module may keep a private one that other callers bypass
    for path in (ROOT / "gui").rglob("*.py"):
        assert "_DOWNLOAD_LOCK" not in path.read_text(), f"{path} has a private export lock"


def test_notification_provider_mounted_before_tiles():
    """pysepal 4's use_notifications() raises when no bus exists yet, and the
    provider only creates the bus when it renders -- after its parent's body.
    The tiles are built with render_fixed inside a body, so Page must mount
    the provider as an earlier sibling of _AppShell, which builds the tiles."""
    src = _src("gui/solara_app.py")
    # Source slices, not inspect.getsource: gui.solara_app calls init_ee() at
    # import time (see module docstring).
    page_body = src[src.index("def Page():"):src.index("def _AppShell():")]
    shell_body = src[src.index("def _AppShell():"):]
    assert page_body.index("NotificationProvider()") < page_body.index("_AppShell()")
    assert "render_detached(" not in page_body and "MapApp.element" not in page_body
    assert "NotificationProvider()" not in shell_body
    assert "render_detached(" in shell_body and "MapApp.element" in shell_body


def test_the_enter_listener_is_mounted_once_in_page():
    """Enter presses the open dialog's default button (gui/widget/enter_key):
    one listener, on document, mounted by Page."""
    src = _src("gui/solara_app.py")
    page_body = src[src.index("def Page():"):src.index("def _AppShell():")]
    assert page_body.count("EnterKeyListener()") == 1
    assert "EnterKeyListener()" not in src[src.index("def _AppShell():"):]


def test_render_fixed_tile_needs_the_provider_rendered_first():
    """The mechanism the shell split relies on, against real pysepal 4: a tile
    render_fixed inside the provider's parent body finds no bus (and would
    raise NotificationProviderError); inside a later sibling it finds one."""
    import reacton
    import solara
    from pysepal.solara.notifications import NotificationProvider
    from pysepal.solara.notifications.bus import get_current_bus

    seen = []

    @solara.component
    def Tile():
        seen.append(get_current_bus() is not None)
        return solara.Text("tile")

    @solara.component
    def Shell():
        solara.render_fixed(Tile())
        solara.Text("shell")

    @solara.component
    def SplitPage():
        NotificationProvider()
        Shell()

    @solara.component
    def FlatPage():
        NotificationProvider()
        solara.render_fixed(Tile())  # runs before the provider renders

    for page, expected in ((FlatPage, False), (SplitPage, True)):
        seen.clear()
        _, rc = reacton.render(page(), handle_error=False)
        try:
            assert seen[0] is expected, page.__name__
        finally:
            rc.close()


def test_the_overview_dialog_reviews_through_a_dismiss():
    """A dialog has its own close button, so there is no "back to the
    workflow"; "Review alerts" must close itself first, or the dashboard
    would stay over the Analysis step the user asked for."""
    src = _src("gui/tile/overview_tile.py")
    assert 't("overview_tile.review_button")' in src
    assert "back_to_workflow" not in src
    review = src[src.index("def _review_alerts():"):]
    assert review.index("dismiss()") < review.index('app_state.current_page_view.value = "analysis_tile"')
    assert "rv.Btn(" not in src


def test_panel_bridge_drives_views_and_title():
    src = _src("gui/solara_app.py")
    body = src[src.index("def _PanelBridge("):]
    body = body[: body.index("\ninit_ee()")]
    assert "app_state.active_step.value" in body
    assert "panel_view" not in body, "the panel has one view: the rail items are dialogs"
    assert 'wrap.style_ = "" if key == spec.key else "display: none;"' in body
    assert "stage_host" not in body and "defo-map-hidden" not in body
    assert "mapapp.right_panel[0].config = {**workflow_config}" in body
    # The mirror runs on the workflow screen only (tests/test_panel_bridge.py).
    assert 'app_state.screen.value == "workflow"' in body
    assert "app_state.current_page_view.value = " in body


def _shell_css() -> str:
    """The stylesheet ``_AppShell`` mounts (its ``solara.Style`` argument)."""
    tree = ast.parse(_src("gui/solara_app.py"))
    shell = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_AppShell")
    call = next(n for n in ast.walk(shell) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "Style")
    return call.args[0].value


def _z_index(css: str, selector: str) -> int:
    rules = {sel.strip(): body for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)}
    return int(re.search(r"z-index:\s*(\d+)", rules[selector]).group(1))


def test_dialogs_menus_and_the_task_pill_stack_above_the_analysis_screen():
    """Vuetify stacks every overlay from small z-indexes, measured live in the
    app: the rail and the right panel at 6, menus from 8, pysepal's task pill
    at 170, the dialog scrim at 201 and dialogs from 202. The stage at 801
    covered the About and Project dialogs (elementFromPoint on their content
    hit the stage's map). The analysis screen sits right of the rail, which
    stays usable on it (R30): it must stay above the map (0) and below all of
    them, with the rail's collapse arrow, which sits on its left edge, above
    it."""
    from gui.tile.analysis.analysis_tile import SCREEN_CSS

    css = _shell_css()
    screen = _z_index(SCREEN_CSS, ".defo-analysis-screen")
    arrow = _z_index(css, ".v-application .sidebar-controls")
    assert 0 < screen < arrow < 6
    assert ".defo-stage" not in css and "defo-map-hidden" not in css


def test_the_analysis_footer_is_not_white_on_the_dark_theme():
    """The footer is pinned over the scrolling panel, so it paints its own
    background. Its inline ``var(--v-background-base, #ffffff)`` is the light
    fallback: no theme defines ``background``, so on the dark theme it would
    be a white bar. The shell restates it for the dark theme -- with
    ``!important``, since an inline style beats any stylesheet rule -- on
    pysepal's dark drawer surface (``.theme--dark.v-navigation-drawer``)."""
    css = _shell_css()
    rules = {sel.strip(): body for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css)}
    body = rules[".theme--dark .defo-analysis-footer"]
    colour = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6})\s*!important", body).group(1)
    assert colour.lower() not in ("#fff", "#ffffff")


def test_the_project_dialog_closes_itself_through_the_shell():
    shell = _component_body(_src("gui/solara_app.py"), "_AppShell")
    assert 'dismiss=lambda: dismiss_dialog(mapapp_ref.get("widget"), nav_model)' in shell
    for holder in ("nav_model = solara.use_memo(", "mapapp_ref = solara.use_memo("):
        assert shell.count(holder) == 1 and shell.index(holder) < shell.index("project_widget = "), holder


def test_the_rail_mirror_only_mirrors_the_page_view():
    """Every rail item is a dialog MapApp opens itself, so a rail click has
    nothing to apply to the right panel: the observer only keeps
    ``current_page_view`` faithful, which is what makes a repeated trigger
    (clicking the same item twice) fire the navigation bridge again."""
    src = _src("gui/solara_app.py")
    page_body = src[src.index("def Page():"):]
    assert 'nav_model.observe(_on_rail_step, "current_step")' in page_body
    assert "view = page_view_for_rail_step(sid)" in page_body
    assert "panel_view" not in page_body
    assert 'nav_model.unobserve(_on_rail_step, "current_step")' in page_body


STEP_FILES = {
    "aoi": "gui/tile/aoi_step.py",
    "alerts": "gui/tile/alerts_step.py",
    "layers": "gui/tile/layers_step.py",
    "period": "gui/tile/period_step.py",
    "prepare": "gui/tile/prepare_step.py",
    "integrate": "gui/tile/integrate_step.py",
    "questionnaire": "gui/tile/questionnaire_step.py",
}


def test_workflow_tiles_open_with_description_and_info_button():
    """Spec §14 slice 6: every step shows its description and an info button."""
    from gui.store.workflow_steps import STEPS

    assert set(STEP_FILES) == {s.key for s in STEPS}
    for key, rel in STEP_FILES.items():
        src = _src(rel)
        assert src.count("StepIntro(") == 1, rel
        for part in ("description", "info_header", "info_md"):
            assert f'"steps.{key}.{part}"' in src, (rel, part)
    analysis = _src("gui/tile/analysis/analysis_tile.py")
    assert "info_button(" in analysis and "analysis_tile.description" in analysis


def test_pipeline_header_accent_follows_the_app_theme_not_solara():
    """The app's theme is pysepal's ThemeState (MapApp's toggle), not Solara's
    own dark mode. Reading solara.lab.use_dark_effective() here always returned
    True, so the strip and the step badge stayed gold in the light theme, and a
    detached-rendered header never re-rendered on a toggle. Page() mirrors the
    trait into app_state.dark and the header prefers it."""
    page_body = _src("gui/solara_app.py")
    page_body = page_body[page_body.index("def Page():"):]
    assert 'theme_state.observe(_on_theme, names=["dark"])' in page_body
    assert "app_state.dark.set(bool(theme_state.dark))" in page_body
    assert "solara.use_effect(_mirror_theme, dependencies=[])" in page_body
    assert 'theme_state.unobserve(_on_theme, names=["dark"])' in page_body

    header = _src("gui/widget/pipeline_header.py")
    assert "is_dark = solara_dark if dark is None else dark.value" in header
    assert "primary = themes.dark.primary if is_dark else themes.light.primary" in header
    assert "dark=app_state.dark" in _src("gui/widget/workflow_header.py")


def test_task_log_handler_is_installed_at_startup():
    src = _src("gui/solara_app.py")
    assert "install_task_log_handler()" in src


def test_app_logger_is_raised_to_info_next_to_the_log_bridge():
    """The bridge only sees records the app logger lets through.

    Under a bare process, ``solara run`` and Voila the app logger's effective
    level is the root's WARNING, which would make the INFO milestone bridge
    inert; startup must raise it to INFO right where the bridge is installed.
    """
    src = _src("gui/solara_app.py")
    install = src.index("install_task_log_handler()\n")
    window = src[install : install + 800]
    assert "logger.setLevel(logging.INFO)" in window
    assert "logger.level > logging.INFO" in window or "logger.level == logging.NOTSET" in window


# ---------------------------------------------------------------------------
# The project workflow replaces the recipe-era flow (Task 22).
# ---------------------------------------------------------------------------
def _component_body(src: str, name: str) -> str:
    """The source of the module-level function ``name`` in ``src``."""
    body = src[src.index(f"def {name}("):]
    ends = [i for i in (body.find("\n@solara.component"), body.find("\ndef "), body.find("\ninit_ee()")) if i > 0]
    return body[: min(ends)] if ends else body


def test_shell_wires_the_new_tiles():
    src = (ROOT / "gui" / "solara_app.py").read_text()
    for name in ("AoiStep", "AlertsStep", "PeriodStep", "PrepareStep", "IntegrateStep",
                 "ProjectDialog", "OverviewTile", "SetContext", "MapLayers", "install_autosave",
                 "_ActiveSetLoader", "_AlertLayers"):
        assert name in src, name
    for gone in ("AuxModel", "AoiDateModel", "SelectedAlertsModel", "AppTileModel", "AlertFilterModel",
                 "build_recipe_layout", "build_aux_layout", "AlertsFilterTile", "Workspace(",
                 "scratch_registry", "register_scratch_owner", "cleanup_kernel_scratch"):
        assert gone not in src, gone


def test_removed_modules_are_gone():
    for rel in ("gui/tile/recipe_tile.py", "gui/tile/aux_tile.py", "gui/tile/alerts_filter_tile.py",
                "gui/tile/aoi_tile.py", "gui/widget/custom_source_list.py",
                "deforestation_alerts/scripts/aoi_helper.py", "deforestation_alerts/scripts/recipe_helper.py",
                "deforestation_alerts/scripts/alert_filter_helper.py", "deforestation_alerts/scripts/vectorize.py",
                "deforestation_alerts/model/aux_model.py", "deforestation_alerts/model/aoi_date_model.py",
                "deforestation_alerts/model/alerts_model.py", "deforestation_alerts/model/app_model.py",
                "gui/Dockerfile", "gui/supervisord.conf", "docker-compose.yml", ".dockerignore",
                "deforestation_alerts/scripts/workspace.py", "gui/scripts/scratch_registry.py",
                "gui/widget/creation_dialog.py"):
        assert not (ROOT / rel).exists(), rel


def test_no_traitlets_models_in_new_code():
    # Not gui/widget/enter_key.py: its VuetifyTemplate.template trait is the
    # library boundary (Global Constraints).
    for rel in ("deforestation_alerts/project", "deforestation_alerts/engine", "gui/tile/aoi_step.py",
                "gui/tile/alerts_step.py", "gui/tile/period_step.py", "gui/tile/prepare_step.py",
                "gui/tile/integrate_step.py", "gui/tile/project_dialog.py", "gui/store/state_manager.py",
                "gui/store/autosave.py", "gui/widget/source_modal.py", "gui/widget/raster_input.py",
                "gui/widget/grid_form.py", "gui/widget/dialog_nudge.py", "gui/widget/form_dialog.py",
                "gui/widget/attribute_modal.py", "gui/widget/manage_projects.py", "gui/widget/mask_modal.py",
                "gui/widget/vis_layer_modal.py", "gui/widget/period_modal.py", "gui/widget/product_table.py",
                "gui/widget/attribute_form.py", "gui/scripts/aoi_io.py", "gui/scripts/catalogue_meta.py",
                "gui/scripts/inflight.py", "gui/scripts/integrate_jobs.py", "gui/scripts/map_layers.py",
                "gui/scripts/pmtiles_map.py", "gui/scripts/prepare_jobs.py", "gui/scripts/raster_map.py",
                "gui/scripts/remote_facts.py", "gui/scripts/coverage_ui.py",
                "gui/scripts/scene_cache.py", "gui/scripts/scene_loader.py",
                "gui/scripts/set_context.py", "gui/scripts/set_store.py", "gui/scripts/tile_proxy.py",
                "deforestation_alerts/scripts/alert_store.py", "deforestation_alerts/scripts/alert_table.py"):
        path = ROOT / rel
        files = path.rglob("*.py") if path.is_dir() else [path]
        for f in files:
            text = f.read_text()
            for banned in ("pysepal.model", "from pysepal import model", "from traitlets", "import traitlets"):
                assert banned not in text, (f, banned)


def test_the_shell_installs_auto_save_and_one_set_context():
    shell = _src("gui/solara_app.py")
    shell = shell[shell.index("def _AppShell():"):]
    assert "solara.use_effect(lambda: install_autosave(app_state), dependencies=[])" in shell
    assert "set_context = solara.use_memo(lambda: SetContext(app_state), dependencies=[])" in shell
    for role in ("aux_model", "selected_alerts_model", "app_tile_model"):
        assert f"{role}=set_context," in shell, role
    assert "MapLayers(map_, legends=app_state.layer_legends, selected=app_state.selected_legend)" in shell
    assert shell.index("MapLegend(app_state=app_state, layers=layers)") < shell.index("app_element = MapApp.element(")
    assert 'rail_content = {"about": about_widget, "project": project_widget, "overview": overview_widget}' in shell
    assert "_ActiveSetLoader(app_state=app_state, layers=layers, map_=map_)" in shell
    assert shell.index("_PanelBridge(") < shell.index("_ActiveSetLoader(app_state=")
    # The alert layers are a leaf of the shell, not of a step: a layer switched
    # on in Integrate stays on while the user moves to Analysis or opens the
    # Overview dashboard, so nothing that unmounts with a view may own it.
    assert "_AlertLayers(app_state=app_state, layers=layers, map_=map_)" in shell
    assert shell.index("_ActiveSetLoader(app_state=") < shell.index("_AlertLayers(app_state=")


def test_only_the_shell_draws_the_alert_layers():
    """Integrate & vectorize switches them with two booleans on ``app_state``
    and the Overview view draws nothing: every ``MapLayers`` call for an alert
    set is in ``_AlertLayers``, which the shell mounts."""
    body = _component_body(_src("gui/solara_app.py"), "_AlertLayers")
    for call in ("layers.hide(alert_layers.SET_LAYER_KEY)", "layers.show(alert_layers.SET_LAYER_KEY",
                 "map_.on_interaction(_on_map)"):
        assert call in body, call
    for rel in ("gui/tile/integrate_step.py", "gui/tile/overview_tile.py"):
        step = _src(rel)
        for call in ("layers.show(", "layers.hide(", "layers.toggle(", "layers.refresh(", "add_centroid_layer(",
                     "add_set_pmtiles("):
            assert call not in step, f"{rel}: {call}"
    # and the switches write nothing but the two booleans
    integrate = _src("gui/tile/integrate_step.py")
    assert "on_v_model=app_state.show_alerts.set" in integrate
    assert "on_v_model=app_state.show_earlier_sets.set" in integrate


def test_the_active_set_loader_reads_the_store_on_a_worker_and_keeps_published_alerts():
    """Integrate records a project's first set through ``update_project`` on its
    worker thread, so the loader re-renders there. A ``prefer_threaded=False``
    ``use_task`` cannot start from that render (solara schedules it with a bare
    ``asyncio.create_task``: no running loop in a worker thread), so the loader
    starts its read with ``spawn_in_context`` from an effect. Rulings: a reload
    resets ``actual_alert_id`` to -1 before it publishes, keeps alerts Integrate
    already published for the same project, and reports a failed read."""
    body = _component_body(_src("gui/solara_app.py"), "_ActiveSetLoader")
    assert "use_task(" not in body
    assert "solara.use_effect(_on_install, [signal])" in body
    assert "layers.clear()" in body and "map_.zoom_bounds(p.aoi.bounds)" in body
    assert "solara.use_effect(_start_load, [key])" in body
    assert "spawn_in_context(_load)" in body
    assert "set_store.load_active_alerts(p)" in body
    assert "set_store.holds_set(app_state.alerts_gdf.value, s)" in body
    # checked before the read and again before the publish
    load = body.index("def _load():")
    assert load < body.index("if _kept():") < body.index("load_active_alerts") < body.index("or _kept():")
    assert "set_store.publish_set_alerts(app_state, p.name, s, gdf, unless_held=not installed)" in body
    assert "notifications.error(" in body and "logger.exception(" in body


def test_the_title_follows_the_project_without_re_rendering_the_shell_on_commits():
    """Ruling #91: the shell body must not read ``app_state.project.value``,
    or every commit (each auto-save) re-renders it. Only a new, opened or
    closed project changes the title, so the shell follows the install
    signal. (A leaf setting ``app_title`` on the widget does not work:
    reacton re-applies MapApp's element props on the shell context's next
    render, seen in the browser walk.)"""
    src = _src("gui/solara_app.py")
    shell = _component_body(src, "_AppShell")
    assert "app_state.project.value" not in shell
    flat = " ".join(shell.split())
    assert "app_title = use_app_title(app_state)" in flat
    hook = " ".join(_component_body(src, "use_app_title").split())
    assert ("solara.use_memo( lambda: compute_title(app_state), "
            "dependencies=[app_state.project_loaded_signal.value, locale] )") in hook
    assert "app_title=app_title," in shell
    assert "app_state.project.peek()" in _component_body(src, "compute_title")
    assert "_TitleBridge" not in src


def test_a_detached_tile_may_remount_its_root():
    """Steps remount their root element on a project switch: AoiStep keys its
    Column on the install signal, Period and Integrate key their form (the
    direct-child ruling). ``render_fixed`` has no container, so a new root
    widget raises "You are not using a container" out of whatever reactive set
    triggered that render: in the browser walk, opening a project failed half
    way through ``AppState._install``. The shell therefore renders every tile
    into a container (``gui/scripts/detached.py``), where a remount is fine."""
    import reacton
    import solara

    from gui.scripts.detached import render_detached

    signal = solara.reactive(0)

    @solara.component
    def Tile():
        with solara.Column().key(f"tile-{signal.value}"):
            solara.Text(str(signal.value))

    widget, rc = reacton.render_fixed(Tile(), handle_error=False)
    try:
        with pytest.raises(ValueError, match="not using a container"):
            signal.set(1)
    finally:
        rc.close()

    container, rc = render_detached(lambda: Tile(), "tile", notify=False)
    try:
        first = container.children[0]
        signal.set(2)
        assert container.children[0] is not first  # remounted, inside the same container
    finally:
        rc.close()

    src = _src("gui/solara_app.py")
    assert "render_fixed" not in _component_body(src, "_AppShell")
    assert src.count("render_detached(lambda: ") == 11  # About, Project, Overview, seven steps, the header


def test_nothing_imports_the_workspace_module():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for base in ("gui", "deforestation_alerts", "scripts", "tools", "tests"):
        for py in (root / base).rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                if any(n.endswith("scripts.workspace") or n.endswith("scratch_registry") for n in names):
                    offenders.append(str(py.relative_to(root)))
    assert offenders == []
    assert not (root / "deforestation_alerts" / "scripts" / "workspace.py").exists()


def test_the_title_follows_a_language_switch():
    """``use_app_title`` is compiled from gui/solara_app.py (which cannot be
    imported here) and rendered: a ``set_locale`` re-renders the title in the
    new language without a project install."""
    from types import SimpleNamespace

    import reacton
    import solara
    from pysepal.i18n import current_locale, set_locale

    from gui.i18n import t

    tree = ast.parse(_src("gui/solara_app.py"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("compute_title", "use_app_title")]
    ns = {"solara": solara, "t": t, "current_locale": current_locale}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "gui/solara_app.py", "exec"), ns)

    app_state = SimpleNamespace(project=solara.reactive(SimpleNamespace(name="demo")),
                                project_loaded_signal=solara.reactive(0))

    @solara.component
    def Shell():
        solara.Text(ns["use_app_title"](app_state))

    def strings(widget):
        out = []
        for child in getattr(widget, "children", None) or []:
            out.extend([child] if isinstance(child, str) else strings(child))
        return out

    def shown():
        [text] = strings(rc.container)
        return text

    set_locale("en")
    _, rc = reacton.render(Shell(), handle_error=False)
    try:
        english = shown()
        assert english == f"{t('app.title')} · demo"
        set_locale("fr")
        assert shown() == f"{t('app.title')} · demo" != english
    finally:
        set_locale("en")
        rc.close()
