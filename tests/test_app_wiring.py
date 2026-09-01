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
