"""Shell wiring guards (the spatial_risk scaffold's getsource technique).

``gui.solara_app`` calls ``init_ee()`` at import time, so these assertions
read the source text instead of importing the module — same contract lock,
credential-free.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TILE_FILES = [
    "gui/tile/recipe_tile.py",
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
    for literal in ('"id": 1', '"id": 2', "'id': 1"):
        assert literal not in src, f"hardcoded drawer id found: {literal}"
    assert "hides_shared_map" not in src
    assert "step_display" not in src and "step_actions" not in src


def test_workflow_panel_is_wired():
    src = _src("gui/solara_app.py")
    assert "WorkflowHeader(app_state=app_state)" in src
    assert '"content": [panel_host]' in src
    assert "header_wrapper" in src
    assert "right_panel_open=True" in src
    assert "is_pinned=False" in src
    assert 'WORKFLOW_PANEL_CONFIG = {' in src and 't("app.panel_workflow_title")' in src
    assert "StageHost(" in src
    assert "**panel_step_wrappers, **panel_rail_wrappers" in src


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


GEE_TASK_FILES = [
    "gui/tile/aoi_tile.py",
    "gui/tile/alerts_filter_tile.py",
    "gui/tile/overview_tile.py",
]


def test_gee_tasks_use_kernel_loop():
    """Decision 3: GEE work runs as async tasks on Solara's kernel loop.

    A ``prefer_threaded=True`` task would run the coroutine on its own loop in a
    thread, which is the mixed-loop shape the pysepal audit flagged.
    """
    for rel in GEE_TASK_FILES:
        src = _src(rel)
        assert "prefer_threaded=True" not in src, f"threaded task in {rel}"
        assert "prefer_threaded=False" in src, f"{rel} has no kernel-loop task"


def test_gee_map_work_goes_through_to_thread():
    """Every ``add_ee_layer`` call site is reached from a task, via to_thread."""
    for rel in GEE_TASK_FILES:
        src = _src(rel)
        assert "asyncio.to_thread(" in src, f"{rel} does no work in a thread"


def test_long_running_actions_use_the_task_button():
    """Search/Analyze are TaskButtons (single start/cancel toggle), not Buttons."""
    for rel in ("gui/tile/aoi_tile.py", "gui/tile/alerts_filter_tile.py"):
        src = _src(rel)
        assert "TaskButtonComponent(" in src, f"{rel} lost its TaskButton"
        assert "use_task_button(" in src, f"{rel} lost its task-button wiring"
        assert "solara.Button(" not in src, f"{rel} still has a raw Button"


def test_completion_effects_guard_on_cancelled():
    """A cancelled task must change nothing: the apply effects check .cancelled
    and are keyed on the full task-state dependency set."""
    for rel, apply_fn in (
        ("gui/tile/aoi_tile.py", "_apply_search"),
        ("gui/tile/alerts_filter_tile.py", "_apply_analyze"),
    ):
        src = _src(rel)
        body = src[src.index(f"def {apply_fn}():") :]
        body = body[: body.index("solara.use_effect(")]
        assert ".cancelled" in body, f"{apply_fn} does not guard on cancelled"
        for flag in (".pending", ".finished", ".error", ".cancelled"):
            assert f"_task{flag}," in src, f"{rel} effect deps missing {flag}"


def test_search_and_analyze_have_a_synchronous_pending_guard():
    """A render-time ``disabled=`` cannot stop a double click; the handler must."""
    for rel, start_fn, task in (
        ("gui/tile/aoi_tile.py", "_start_search", "search_task"),
        ("gui/tile/alerts_filter_tile.py", "_start_analyze", "analyze_task"),
    ):
        src = _src(rel)
        body = src[src.index(f"def {start_fn}():") :]
        assert f"if {task}.pending:" in body[:600], f"{rel}: no pending guard"


def test_effect_scheduled_tasks_guard_on_a_running_loop():
    """solara schedules prefer_threaded=False tasks with a bare
    asyncio.create_task, which raises without a running loop in the calling
    thread. An effect body must never raise (reacton masks it behind "you are
    not using a container"), so every task call made from an effect is guarded."""
    for rel in GEE_TASK_FILES:
        src = _src(rel)
        assert "def _loop_is_running()" in src, f"{rel} has no loop guard helper"
        assert "if _loop_is_running():" in src, f"{rel} never uses the loop guard"


def test_geedim_exports_are_serialized_in_one_place():
    """geedim has ONE process-wide asyncio runner, so overlapping exports raise
    "This event loop is already running". The lock therefore belongs at the single
    entry point every caller shares, not at one call site."""
    src = _src("deforestation_alerts/scripts/raster_export.py")
    assert "_DOWNLOAD_LOCK = threading.Lock()" in src
    assert "_DOWNLOAD_LOCK.acquire(timeout=" in src, "must not block forever"
    assert "_DOWNLOAD_LOCK.release()" in src
    # and no tile may keep a private one that other callers bypass
    for rel in GEE_TASK_FILES:
        assert "_DOWNLOAD_LOCK" not in _src(rel), f"{rel} has a private export lock"


def test_alert_store_is_written_off_the_loop_after_a_cancellation_recheck():
    """Two properties at once.

    (1) alert_db.parquet is read back as state on recipe load, so a cancelled run
    must not leave one behind — the write is preceded by an explicit
    cancellation re-check (``await asyncio.sleep(0)``, at which a requested
    cancel raises before anything is written).
    (2) It is a GeoParquet serialize plus an upload of the whole store, so it
    must not run on the kernel loop: it goes through ``asyncio.to_thread`` inside
    the task, never from the completion effect.
    """
    src = _src("gui/tile/alerts_filter_tile.py")
    task_body = src[src.index("async def _analyze("):src.index("def _start_analyze(")]
    recheck_at = task_body.index("await asyncio.sleep(0)")
    persist_at = task_body.index("_persist_alerts_gdf,")
    assert recheck_at < persist_at, "persist must follow the cancellation re-check"
    call = task_body[task_body.rindex("await ", 0, persist_at):persist_at]
    assert "asyncio.to_thread(" in call, "persist must run in a thread"
    apply_body = src[src.index("def _apply_analyze():"):]
    assert "_persist_alerts_gdf" not in apply_body, (
        "persist must not run in the completion effect (that is the kernel loop)"
    )


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
    assert "_render_detached(" not in page_body and "MapApp.element" not in page_body
    assert "NotificationProvider()" not in shell_body
    assert "_render_detached(" in shell_body and "MapApp.element" in shell_body


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


def test_gee_widgets_get_the_session_interface():
    """pysepal 4 raises SepalSessionError when an AoiModel or AssetSelect
    builds its own GEEInterface under a per-connection Solara server, so each
    one must receive the Page's session interface."""
    aux = _src("gui/tile/aux_tile.py")
    calls = aux.split("sw.inputs.AssetSelect(")[1:]
    assert len(calls) == 5
    for call in calls:
        assert "gee_interface=gee_interface" in call[: call.index(")")]
    shell = _src("gui/solara_app.py")
    assert "AoiDateModel(gee_interface=gee_interface)" in shell
    assert "build_aux_layout(aux_model, workspace, gee_interface)" in shell


def test_overview_view_has_back_and_review_buttons():
    src = _src("gui/tile/overview_tile.py")
    assert "cm.overview_tile.back_to_workflow" in src
    assert "cm.overview_tile.review_button" in src
    assert 'app_state.current_page_view.value = STEPS[app_state.active_step.value].page_view' in src
    assert 'app_state.current_page_view.value = "analysis_tile"' in src
    assert "rv.Btn(" not in src


def test_panel_bridge_drives_views_stage_map_owner_and_title():
    src = _src("gui/solara_app.py")
    body = src[src.index("def _PanelBridge("):]
    body = body[: body.index("\ninit_ee()")]
    assert "app_state.panel_view.value" in body and "app_state.active_step.value" in body
    assert 'header_wrapper.style_ = "" if workflow else "display: none;"' in body
    assert "stage_host.visible = workflow and spec.full_stage" in body
    assert 'map_.add_class("defo-map-hidden")' in body
    assert "if spec.owns_map" in body
    assert "rail_item_for_step" in body and "item.owns_map" in body
    assert "panel.config = {**workflow_config" in body
    assert "app_state.current_page_view.value = " in body


def test_panel_bridge_never_clears_the_shared_map_owner():
    """Regression guard for e09d545 ("popups no longer clear the active panel
    tile's map layers").

    Opening a popup/dialog over a right-panel tile must NOT change
    ``app_state.active_map_view``: the map-bearing tiles (AOI / Filter /
    Overview) key their draw/clear effect off it, so clearing it wipes the
    underlying tile's layers and they never come back when the popup closes.
    The old guard was ``navigation.active_map_view_for_step`` (returned None
    for popup steps = "leave unchanged"), deleted in the registry split. The
    behaviour now lives in ``_PanelBridge``'s owner branch, which only ever
    ASSIGNS a resolved owner and never clears one.
    """
    src = _src("gui/solara_app.py")
    body = src[src.index("def _PanelBridge("):]
    body = body[: body.index("\ninit_ee()")]
    writes = [
        line.strip()
        for line in body.splitlines()
        if "active_map_view" in line and not line.strip().startswith("#")
    ]
    assert writes == [
        "if owner is not None and app_state.active_map_view.value != owner:",
        "app_state.active_map_view.value = owner",
    ], f"active_map_view must only be assigned a resolved owner: {writes}"
    for clearing in (
        'active_map_view.value = ""',
        "active_map_view.value = None",
        'active_map_view.set("")',
        "active_map_view.set(None)",
    ):
        assert clearing not in src, f"the shared-map owner must never be cleared: {clearing}"


def test_rail_mirror_switches_panel_view_for_panel_items_only():
    src = _src("gui/solara_app.py")
    page_body = src[src.index("def Page():"):]
    assert 'nav_model.observe(_on_rail_step, "current_step")' in page_body
    assert 'if item is not None and item.display == "panel":' in page_body
    assert "app_state.panel_view.set(item.key)" in page_body


def test_aoi_name_is_mirrored_from_the_aoi_model():
    """The mirror must observe traits that can actually notify.

    Regression: the first cut observed ``feature_collection``, which is a plain
    class attribute on AoiModel, not a traitlet -- ``observe`` accepts the name
    and silently never fires. See
    ``tests/test_app_state.py::test_only_object_set_and_name_can_notify_the_aoi_mirror``
    for the behavioural half of this guard.
    """
    src = _src("gui/solara_app.py")
    page_body = src[src.index("def Page():"):]
    assert 'aoi_date_model.observe(_on_aoi, names=["object_set", "name"])' in page_body
    assert 'aoi_date_model.unobserve(_on_aoi, names=["object_set", "name"])' in page_body
    # feature_collection may appear in prose, but never as an observed name.
    observed = re.findall(r"observe\(_on_aoi, names=\[([^\]]*)\]", page_body)
    assert observed, "the aoi mirror must register an observer"
    for names in observed:
        assert "feature_collection" not in names, (
            "feature_collection is not a traitlet; observing it never fires"
        )
    assert "app_state.aoi_name.set(aoi_name_from_model(aoi_date_model))" in page_body
    assert "solara.use_effect(_mirror_aoi_name, dependencies=[])" in page_body


def test_workflow_tiles_open_with_description_and_info_button():
    for rel in (
        "gui/tile/aoi_tile.py",
        "gui/tile/alerts_filter_tile.py",
        "gui/tile/analysis/analysis_tile.py",
    ):
        src = _src(rel)
        assert "InfoButton(" in src or "info_button(" in src, rel
        assert ".description" in src, rel


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
