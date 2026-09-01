"""Shell wiring guards (the spatial_risk scaffold's getsource technique).

``gui.solara_app`` calls ``init_ee()`` at import time, so these assertions
read the source text instead of importing the module — same contract lock,
credential-free.
"""

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


def test_steps_data_derives_from_the_registry():
    src = _src("gui/solara_app.py")
    assert "from gui.store.workflow_steps import STEPS" in src
    assert '"id": s.step_id' in src, "steps_data must be built from STEPS"
    # No hand-maintained drawer ids may creep back into the shell.
    for literal in ('"id": 1', '"id": 2', "'id': 1"):
        assert literal not in src, f"hardcoded drawer id found: {literal}"


def test_panel_and_map_step_ids_derive_from_the_registry():
    src = _src("gui/solara_app.py")
    assert "s.step_id for s in STEPS if s.owns_map" in src
    assert "s.step_id for s in STEPS if s.hides_shared_map" in src
    assert "(4, 5, 6)" not in src, "hardcoded panel step ids found"


def test_navigation_map_derives_from_the_registry():
    src = _src("gui/store/navigation.py")
    assert "PAGE_VIEW_TO_STEP = {s.page_view: s.step_id for s in STEPS}" in src


def test_log_console_is_wired():
    src = _src("gui/solara_app.py")
    assert "install_log_console_handler()" in src
    assert "LogConsole()" in src


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


def test_notification_provider_mounted_before_tiles():
    src = _src("gui/solara_app.py")
    # Slice to Page()'s body: _render_detached is *defined* earlier in the
    # file (well before the mount point), so a whole-file index comparison
    # would find that definition and report a false ordering failure even
    # when the mount is correctly placed. Scoping to the function body
    # leaves only the call sites inside Page(), matching what
    # inspect.getsource(Page.f) would give without importing the module
    # (gui.solara_app calls init_ee() at import time — see module docstring).
    page_body = src[src.index("def Page():"):]
    provider_at = page_body.index("NotificationProvider()")
    first_tile_at = min(
        page_body.index("_render_detached("), page_body.index("MapApp.element")
    )
    assert provider_at < first_tile_at, "provider must mount before tiles/MapApp"
