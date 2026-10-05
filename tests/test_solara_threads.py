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


def _leaf(*, guarded, slow_on=None):
    """A leaf whose ``use_task`` depends on ``status`` (and, for the plain
    hazard, on ``value``). ``slow_on``: a ``value`` whose render takes half a
    second, so the loop can move ``status`` while a worker is rendering."""
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
            runs.append((seen, moved))

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
