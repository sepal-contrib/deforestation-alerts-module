"""Helpers for running background work from Solara event handlers.

Reactive updates (``some_reactive.set(...)``) made from a *bare*
``threading.Thread`` never reach the browser session, so status cards stay
stuck on "running" even after the backend has finished. Registering the render
thread's kernel context onto the worker thread — exactly what
``solara.lab.use_task`` does internally — lets those updates propagate.

``to_thread_in_context`` — the ``asyncio.to_thread`` counterpart of
``spawn_in_context`` for ``use_task`` bodies.
"""

import asyncio
import threading


async def to_thread_in_context(fn, *args, **kwargs):
    """``asyncio.to_thread`` that propagates the caller's Solara kernel context.

    ``to_thread`` runs on a pool thread that has no kernel context, so any
    reactive publish made there (job status, notification-task milestones)
    never reaches the browser. This captures the caller's context (the
    ``use_task`` body runs with one) and binds it to the pool thread for the
    duration of the call — the ``to_thread`` counterpart of
    ``spawn_in_context``.

    Pool threads are reused across calls, and pysepal 4 app-launcher
    containers are multi-user, so one pool thread can serve different kernels
    in turn. The binding is therefore made unconditionally on every call (a
    stale binding from a previous caller must not win) and removed again in a
    ``finally`` once the work returns, so no kernel stays attached to an idle
    pool thread.
    """
    from solara.server import kernel_context

    try:
        ctx = kernel_context.get_current_context()
    except RuntimeError:
        ctx = None

    def _with_context():
        if ctx is None:
            return fn(*args, **kwargs)
        thread = threading.current_thread()
        kernel_context.set_context_for_thread(ctx, thread)
        try:
            return fn(*args, **kwargs)
        finally:
            kernel_context.clear_context_for_thread(thread)

    return await asyncio.to_thread(_with_context)


def spawn_in_context(target, args=(), *, daemon=True):
    """Start a daemon thread that inherits the caller's Solara kernel context.

    Falls back to a plain thread when there is no active context (e.g. unit
    tests), so the function is usable outside a running app.

    Parameters
    ----------
    target : callable
        Function to run in the background thread.
    args : tuple
        Positional arguments forwarded to ``target``.
    daemon : bool
        Whether the thread is a daemon (default ``True``).

    Returns
    -------
    threading.Thread
        The started thread.
    """
    from solara.server import kernel_context

    thread = threading.Thread(target=target, args=args, daemon=daemon)
    try:
        ctx = kernel_context.get_current_context()
    except RuntimeError:
        ctx = None
    if ctx is not None:
        kernel_context.set_context_for_thread(ctx, thread)
    thread.start()
    return thread
