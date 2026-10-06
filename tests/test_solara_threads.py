"""A ``use_task`` whose dependencies can change in a worker thread's render.

A worker publish re-renders the components it touches in the worker thread
itself, which has no event loop. If a ``prefer_threaded=False`` ``use_task``
sees its dependencies change in that render, its effect starts the task there
and ``asyncio.create_task`` raises "no running event loop", which takes down
the whole app. The slice5 walk hit this when a period was deleted:
SceneIndexLoader's widener depends on the foreground load's own status, and
the active-set loader's worker published the next set's alerts while that
load was restarting.

``use_loop_dependencies`` stops that: in a render without an event loop it
returns the dependencies of the last render that had one, so the task's
effect does not fire there. The next render on the loop brings them up to
date and starts the task.
"""

import asyncio
import gc
import inspect
import threading
import time
import traceback

import pytest
import solara

from gui.scripts import scene_loader
from gui.scripts.solara_threads import use_loop_dependencies


def _leaf(*, guarded, slow_on=None, record_held=False):
    """A leaf whose ``use_task`` depends on ``status`` (and, for the plain
    hazard, on ``value``). ``slow_on``: a ``value`` whose render takes half a
    second, so the loop can move ``status`` while a worker is rendering.
    ``record_held``: a run records the dependencies the hook handed the task,
    not the values the latest render saw (a task already started runs the
    latest render's function, so those can be newer than what started it)."""
    value, status, runs = solara.reactive(0), solara.reactive(0), []

    @solara.component
    def Slow():
        if value.value == slow_on:
            time.sleep(0.5)

    @solara.component
    def Leaf():
        seen, moved = value.value, status.value
        raw = [seen, moved]
        held = use_loop_dependencies(raw)  # a hook: called on every render

        async def _work():
            runs.append(tuple(held) if record_held else (seen, moved))

        solara.lab.use_task(_work, dependencies=held if guarded else raw, prefer_threaded=False,
                            raise_error=False)
        Slow()

    return value, status, runs, Leaf


def _worker(fn, errors):
    def body():
        try:
            fn()
        except BaseException:  # noqa: BLE001 - reported by the test
            errors.append(traceback.format_exc())

    thread = threading.Thread(target=body, name="worker")
    thread.start()
    return thread


async def _join(thread):
    while thread.is_alive():
        await asyncio.sleep(0.01)


def _drive(guarded):
    """A worker publish changes the task's dependencies; nothing else renders."""
    value, _status, runs, Leaf = _leaf(guarded=guarded)
    out = {"errors": []}

    async def main():
        _box, rc = solara.render(Leaf(), handle_error=False)
        await asyncio.sleep(0.05)
        out["mounted"] = list(runs)
        await _join(_worker(lambda: value.set(1), out["errors"]))
        await asyncio.sleep(0.2)
        out["after"] = list(runs)
        try:
            rc.close()
        except RuntimeError as exc:  # the unguarded leaf's failed render, re-raised on close
            out["close_error"] = str(exc)

    asyncio.run(main())
    return out


@pytest.mark.filterwarnings("ignore:coroutine 'TaskAsyncio._async_run' was never awaited:RuntimeWarning")
def test_a_task_dependency_changed_in_a_worker_render_raises_without_the_guard():
    # The hazard itself, so the guarded tests below are known to exercise it.
    out = _drive(guarded=False)
    gc.collect()  # the coroutine that never started is reported here, under this test's filter
    assert out["mounted"] == [(0, 0)]
    assert out["errors"] and "no running event loop" in out["errors"][0]


def test_a_change_seen_first_in_a_worker_render_starts_the_task_on_the_loop():
    out = _drive(guarded=True)
    assert out["mounted"] == [(0, 0)]
    assert out["errors"] == [] and "close_error" not in out
    # No other render happens: the hook wakes the loop itself.
    assert out["after"] == [(0, 0), (1, 0)]


def test_a_change_made_on_the_loop_during_a_worker_render_is_not_lost():
    """reacton skips a render while another thread is rendering, so the
    status the loop sets mid-render is rendered only by the worker. The loop
    must still render it afterwards and start the task (the review's probe)."""
    value, status, runs, Leaf = _leaf(guarded=True, slow_on=1)
    errors = []

    async def main():
        _box, rc = solara.render(Leaf(), handle_error=False)
        await asyncio.sleep(0.05)
        thread = _worker(lambda: value.set(1), errors)
        await asyncio.sleep(0.1)  # the worker is mid-render
        status.set(1)  # on the loop, like load.pending / load.finished
        await _join(thread)
        await asyncio.sleep(1.5)  # the loop's own renders take 0.5 s each here
        rc.close()

    asyncio.run(main())
    assert errors == []
    assert runs[0] == (0, 0)
    assert runs[-1] == (1, 1), runs


def test_the_scene_widener_waits_for_the_loop():
    # Its dependencies include the foreground load's status, which moves on
    # the loop while a worker may be rendering the loader.
    assert "use_loop_dependencies(" in inspect.getsource(scene_loader.SceneIndexLoader)


def test_a_worker_change_landing_as_the_wake_up_ends_is_not_lost(monkeypatch):
    """The wake-up's last check found the dependencies caught up; a worker
    render changes them right after that check, while the wake-up still reads
    as running, so that render schedules nothing. The wake-up must notice it
    as it ends and render once more. Driven deterministically: the worker
    publish runs inside the wake-up's own equality check, once it is True."""
    import sys

    import reacton.utils

    value, _status, runs, Leaf = _leaf(guarded=True, record_held=True)
    errors, trigger = [], []
    real_equals = reacton.utils.equals

    def spy(a, b):
        result = real_equals(a, b)
        if result and trigger and sys._getframe(1).f_code.co_name == "_wake":
            thread = _worker(trigger.pop(), errors)
            thread.join()  # the worker's render, between the check and what follows it
        return result

    monkeypatch.setattr(reacton.utils, "equals", spy)

    async def main():
        _box, rc = solara.render(Leaf(), handle_error=False)
        await asyncio.sleep(0.05)
        trigger.append(lambda: value.set(2))  # once the wake-up for value=1 has caught up
        await _join(_worker(lambda: value.set(1), errors))
        await asyncio.sleep(0.3)
        rc.close()

    asyncio.run(main())
    assert errors == [] and not trigger
    assert runs[-1] == (2, 0), runs


def test_a_wake_up_lost_with_its_loop_does_not_hold_back_the_next_one():
    """A wake-up scheduled on a loop that closed before running it: the next
    render on a loop starts afresh, so a later worker change still wakes it."""
    value, status, runs, Leaf = _leaf(guarded=True)
    errors = []
    first = asyncio.new_event_loop()

    async def mount():
        out = solara.render(Leaf(), handle_error=False)
        await asyncio.sleep(0.05)
        return out

    _box, rc = first.run_until_complete(mount())
    _worker(lambda: value.set(1), errors).join()  # schedules a wake-up on `first`...
    first.close()  # ...which never runs

    async def main():
        status.set(1)  # a render on this loop
        await asyncio.sleep(0.05)
        await _join(_worker(lambda: value.set(2), errors))
        await asyncio.sleep(0.3)
        rc.close()

    asyncio.run(main())
    assert errors == []
    assert runs[-1] == (2, 1), runs


def test_only_the_period_steps_handlers_move_the_active_period():
    """SceneIndexLoader's foreground load takes the active period (``fg``) as a
    plain dependency, not through ``use_loop_dependencies``: safe only while
    no worker publish moves it. It moves in ``deforestation_alerts.project.periods``
    alone, called from the Period step's handlers on the loop. A new caller,
    or a direct assignment, must be checked for the thread it runs on (and
    the loader's dependencies wrapped if it is a worker)."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    movers = {"new_period", "edit_period", "delete_period", "select_period"}
    assigns, calls = [], []
    for path in sorted([*(root / "gui").rglob("*.py"), *(root / "deforestation_alerts").rglob("*.py")]):
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
            if any(isinstance(tg, ast.Attribute) and tg.attr == "active_period" for tg in targets):
                assigns.append(rel)
            if isinstance(node, ast.Call) and (getattr(node.func, "attr", None) in movers
                                               or getattr(node.func, "id", None) in movers):
                calls.append(rel)
    assert set(assigns) == {"deforestation_alerts/project/periods.py"}, assigns
    assert set(calls) <= {"gui/tile/period_step.py", "deforestation_alerts/project/periods.py"}, calls
    assert "gui/tile/period_step.py" in calls  # the scan sees the real callers
