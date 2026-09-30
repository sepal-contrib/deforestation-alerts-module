"""Local scratch is swept when the kernel that created it goes away.

``Workspace`` stages full-size rasters on local scratch disk. Before this,
``Workspace.cleanup_scratch()`` had no caller at all, so every analyze run / CNN
run / staged alert store survived the session. The registry in
``gui.scripts.scratch_registry`` is the seam between the per-Page ``Workspace``
and the module-level ``on_kernel_start`` teardown; these tests drive the real
registry against a real ``Workspace``, plus a source contract for the wiring in
``gui/solara_app.py`` (which cannot be imported here — it calls ``init_ee()`` at
import time).
"""

from pathlib import Path

import pytest

from gui.scripts import scratch_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """No leakage between tests (the registry is module state)."""
    scratch_registry._OWNERS.clear()
    yield
    scratch_registry._OWNERS.clear()


def _staged(ws) -> Path:
    """Stage a real file through the Workspace and return its local path."""
    path = ws.scratch_file(".tif")
    path.write_bytes(b"raster")
    return path


def test_kernel_teardown_deletes_the_scratch_the_workspace_staged(ws):
    scratch_registry.register_scratch_owner(ws)
    staged = _staged(ws)
    assert staged.exists()
    root = ws._scratch_root
    assert root is not None and root.is_dir()

    assert scratch_registry.cleanup_kernel_scratch() == 1

    assert not staged.exists()
    assert not root.exists()


def test_the_workspace_still_works_after_a_sweep(ws):
    """A sweep is not a kill switch: a Workspace that outlives its kernel (or a
    double teardown) must still be able to stage a file."""
    scratch_registry.register_scratch_owner(ws)
    _staged(ws)
    scratch_registry.cleanup_kernel_scratch()
    scratch_registry.cleanup_kernel_scratch()  # idempotent, nothing registered

    again = _staged(ws)
    assert again.exists()


def test_the_sweep_only_touches_the_calling_kernels_workspaces(ws, monkeypatch, tmp_path):
    """Several kernels share one container process; a teardown must not delete
    the scratch another, still-running kernel is reading."""
    from deforestation_alerts.scripts.workspace import Workspace

    other = Workspace(root=tmp_path / "other", scratch_parent=tmp_path / "scratch2")

    monkeypatch.setattr(scratch_registry, "_kernel_key", lambda: "kernel-a")
    scratch_registry.register_scratch_owner(ws)
    mine = _staged(ws)

    monkeypatch.setattr(scratch_registry, "_kernel_key", lambda: "kernel-b")
    scratch_registry.register_scratch_owner(other)
    theirs = _staged(other)

    monkeypatch.setattr(scratch_registry, "_kernel_key", lambda: "kernel-a")
    assert scratch_registry.cleanup_kernel_scratch() == 1

    assert not mine.exists()
    assert theirs.exists()
    other.cleanup_scratch()


def test_a_workspace_that_fails_to_sweep_does_not_abort_the_teardown():
    """The sweep runs on a teardown path, so one broken Workspace must not stop
    the others (or the session cleanup that follows it)."""

    class _Boom:
        def cleanup_scratch(self):
            raise OSError("device busy")

    class _Ok:
        def __init__(self):
            self.swept = False

        def cleanup_scratch(self):
            self.swept = True

    ok = _Ok()
    scratch_registry.register_scratch_owner(_Boom())
    scratch_registry.register_scratch_owner(ok)

    assert scratch_registry.cleanup_kernel_scratch() == 1
    assert ok.swept is True


def test_the_app_shell_sweeps_scratch_on_kernel_teardown():
    """Source contract for the wiring: ``gui/solara_app.py`` cannot be imported
    in the suite (it calls ``init_ee()`` at import), so the registration + the
    teardown call are pinned here, the same way ``test_app_wiring`` pins the
    other shell invariants."""
    root = Path(__file__).resolve().parents[1]
    src = (root / "gui" / "solara_app.py").read_text()
    hook = src[src.index("def on_kernel_start():") : src.index("def Page():")]
    assert "cleanup_kernel_scratch()" in hook, "kernel teardown never sweeps scratch"
    page = src[src.index("def Page():") :]
    assert "register_scratch_owner(ws)" in page, "Page never registers its Workspace"
