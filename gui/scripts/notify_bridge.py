"""Bridge the `SEPALUI.deforestation_alerts` logger to pysepal notification
TaskTrackers.

Each background job wraps its work in ``tracked_job(notifier, title)``, which
opens a pysepal ``notifier.track(...)`` task. While the job body runs, the
``TaskStepLogHandler`` attached to the app logger forwards every INFO+ record
*emitted on the job's own thread* as a ``task.step(...)`` milestone — so
granular processing-stage log lines surface in the official notification pill
without the deforestation-alerts scripts knowing about the GUI. This replaces
the deleted on-map LogConsole.

Thread-scoping is what keeps concurrent jobs honest: two jobs running at once
each see only their own log lines. Completion / failure semantics come from
pysepal's tracker context manager (auto-complete on exit, mark FAILED + error
toast + re-raise on exception).

Threads without a Solara kernel context (``asyncio.to_thread`` pool threads)
cannot publish reactives to the browser — run those job bodies via
``solara_threads.to_thread_in_context`` so the tracker's bus updates land.
"""

import asyncio
import logging
import threading
from contextlib import contextmanager

_LOGGER_NAME = "SEPALUI.deforestation_alerts"

ERROR_TOAST_TIMEOUT = 10.0
"""Seconds an error toast stays on screen. Successes keep pysepal's 3 s default.

Failures deserve a longer read than confirmations, but nothing is sticky: a
tracked job's failure also lands in the notification log, which outlives the
toast.
"""

# thread ident -> TaskTracker of the tracked job running on that thread.
_trackers = {}
_install_lock = threading.Lock()


class TaskStepLogHandler(logging.Handler):
    """Forward INFO+ records from tracked-job threads as task milestones.

    Records from threads with no registered tracker are ignored (file/console
    handlers still see them). The handler itself reads no reactive state, so a
    component that logs during its render cannot be auto-subscribed by it
    (``tracker.step`` does read the bus, but only job threads are registered,
    never a rendering thread).
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Step the emitting thread's tracker (if any) with the record message."""
        try:
            tracker = _trackers.get(threading.get_ident())
            if tracker is not None:
                tracker.step(record.getMessage())
        except Exception:  # a logging handler must never raise into caller code
            pass


def install_task_log_handler(level: int = logging.INFO) -> TaskStepLogHandler:
    """Attach a single ``TaskStepLogHandler`` to the app logger.

    Idempotent: returns the existing handler if one is already attached (guards
    Solara hot-reload / repeated imports). Sets the handler level to ``INFO``
    but does NOT change the logger's own level (file/console keep DEBUG detail).
    """
    logger = logging.getLogger(_LOGGER_NAME)
    with _install_lock:
        for h in logger.handlers:
            if isinstance(h, TaskStepLogHandler):
                return h
        handler = TaskStepLogHandler()
        handler.setLevel(level)
        logger.addHandler(handler)
        return handler


@contextmanager
def forward_logs_to(task):
    """Forward this thread's INFO+ app log records to ``task`` as milestones.

    For work whose tracker was opened elsewhere (e.g. a ``use_task`` coroutine
    that offloads a stage to a pool thread): enter it ON the worker thread.
    Nesting restores the previous tracker on exit.
    """
    install_task_log_handler()
    tid = threading.get_ident()
    previous = _trackers.get(tid)
    _trackers[tid] = task
    try:
        yield task
    finally:
        if previous is not None:
            _trackers[tid] = previous
        else:
            _trackers.pop(tid, None)


@contextmanager
def tracked_job(notifier, title: str, total_steps=None, error_format=None):
    """Track a job as a pysepal notification task, echoing its log milestones.

    Must be entered on the thread that runs the job body — the log→milestone
    forwarding is keyed on that thread's ident. Nesting restores the previous
    tracker on exit. Yields the ``TaskTracker`` for explicit ``step()`` /
    ``set_progress()`` calls where log lines aren't enough. ``notifier=None``
    falls back to a no-op tracker so workers stay callable from plain tests;
    pysepal's ``NoopNotifier`` logs a WARNING for every dropped
    success/error/warning and task failure, so a job run with no provider
    mounted still leaves a trail in the server log.

    ``error_format`` (``Callable[[Exception], str]``) turns a failure into the
    user-facing message; without it the raw ``str(exc)`` is used. On failure the
    task is marked FAILED with that message *before* the toast is published:
    pysepal's ``_TaskTrackerContextManager.__exit__`` only raises its own
    bare-exception toast while the task is not already FAILED, so failing first
    is what keeps this to exactly one toast.
    """
    if notifier is None:
        from pysepal.solara.notifications.notifier import NoopNotifier

        notifier = NoopNotifier()
    with notifier.track(title, total_steps=total_steps) as task:
        with forward_logs_to(task):
            try:
                yield task
            except asyncio.CancelledError:
                raise  # pysepal's __exit__ marks the task CANCELLED; not a failure
            except Exception as exc:
                try:
                    message = (
                        error_format(exc) if error_format is not None else str(exc)
                    )
                except Exception:  # a broken formatter must never mask the real failure
                    logging.getLogger(_LOGGER_NAME).exception("error_format failed")
                    message = str(exc)
                task.fail(message)
                notifier.error(message, timeout=ERROR_TOAST_TIMEOUT)
                raise


def current_notifier():
    """A Notifier on the current kernel's bus, or a NoopNotifier without one.

    For raw-widget workers (no component render, so no ``use_notifications``
    hook): it resolves the bus exactly like pysepal's ``track_task``/``notify``
    globals do, but returns a notifier so ``tracked_job`` can publish its error
    toast with ``ERROR_TOAST_TIMEOUT``.
    """
    from pysepal.solara.notifications import NoopNotifier
    from pysepal.solara.notifications.bus import get_current_bus
    from pysepal.solara.notifications.notifier import Notifier

    bus = get_current_bus()
    return NoopNotifier() if bus is None else Notifier(bus)


def tile_progress_reporter(task):
    """Adapt geedim tile ticks ``(done, total)`` to ``task.set_progress``.

    Throttled to whole-percent changes so a many-tile export does not flood the
    bus. Safe after the task finished (pysepal's tracker ignores late calls).
    """
    last_pct = -1

    def on_tiles(done, total):
        nonlocal last_pct
        if not total:
            return
        pct = int(100 * done / total)
        if pct == last_pct:
            return
        last_pct = pct
        task.set_progress(done / total)

    return on_tiles
