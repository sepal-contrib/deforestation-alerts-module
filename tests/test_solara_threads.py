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
import inspect
import threading
import traceback

import solara

from gui.scripts import scene_loader
from gui.scripts.solara_threads import use_loop_dependencies


def _leaf(*, guarded):
    value, tick, runs = solara.reactive(0), solara.reactive(0), []

    @solara.component
    def Leaf():
        seen = value.value
        _ = tick.value
        deps = [seen]
        if guarded:
            deps = use_loop_dependencies(deps)

        async def _work():
            runs.append(seen)

        solara.lab.use_task(_work, dependencies=deps, prefer_threaded=False, raise_error=False)

    return value, tick, runs, Leaf


def _on_worker(fn):
    errors = []

    def body():
        try:
            fn()
        except BaseException:  # noqa: BLE001 - reported by the test
            errors.append(traceback.format_exc())

    thread = threading.Thread(target=body, name="worker")
    thread.start()
    thread.join()
    return errors


def _drive(guarded):
    value, tick, runs, Leaf = _leaf(guarded=guarded)
    out = {}

    async def main():
        _box, rc = solara.render(Leaf(), handle_error=False)
        await asyncio.sleep(0.05)
        out["mounted"] = list(runs)
        out["errors"] = _on_worker(lambda: value.set(1))
        await asyncio.sleep(0.05)
        out["after_worker"] = list(runs)
        tick.set(1)  # any render on the loop
        await asyncio.sleep(0.05)
        out["after_loop"] = list(runs)
        rc.close()

    asyncio.run(main())
    return out


def test_a_task_dependency_changed_in_a_worker_render_raises_without_the_guard():
    # The hazard itself, so the guarded test below is known to exercise it.
    out = _drive(guarded=False)
    assert out["mounted"] == [0]
    assert out["errors"] and "no running event loop" in out["errors"][0]


def test_the_guard_holds_the_task_until_a_render_on_the_loop():
    out = _drive(guarded=True)
    assert out["mounted"] == [0]
    assert out["errors"] == []
    assert out["after_worker"] == [0]  # nothing started on the worker
    assert out["after_loop"] == [0, 1]  # the loop's next render caught up


def test_the_scene_widener_waits_for_the_loop():
    # Its dependencies include the foreground load's status, which moves on
    # the loop while a worker may be rendering the loader.
    assert "use_loop_dependencies(" in inspect.getsource(scene_loader.SceneIndexLoader)
