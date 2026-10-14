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

The server log. The app logger's only handler used to be the milestone
bridge, and a handler on the logger silences Python's last-resort stderr
fallback, so a worker's ``logger.exception`` reached nothing but the toast.
``install_server_log_handler`` puts WARNING+ records, tracebacks included, on
the server process's own stderr (``sys.__stderr__``: in a Voila kernel
``sys.stderr`` is the browser's, which Voila never shows), and ``tracked_job``
logs every failure it reports, so each failed job leaves one traceback there.
"""

import asyncio
import logging
import sys
import threading
from contextlib import contextmanager

from gui.scripts.error_text import error_text

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


class ServerLogHandler(logging.StreamHandler):
    """WARNING+ app records on the server's stderr, with their tracebacks."""


_SERVER_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"


def install_server_log_handler(level: int = logging.WARNING, stream=None) -> ServerLogHandler:
    """Attach a single ``ServerLogHandler`` to the app logger (idempotent).

    ``stream`` defaults to the process's original stderr (``sys.__stderr__``),
    the server log under Voila and jupyter-server; a test passes its own.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    with _install_lock:
        for h in logger.handlers:
            if isinstance(h, ServerLogHandler):
                return h
        handler = ServerLogHandler(stream or sys.__stderr__ or sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(_SERVER_LOG_FORMAT))
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
    user-facing message; without it, ``error_text`` (a keyed error in the
    user's language, any other error as its own text). On failure the
    task is marked FAILED with that message *before* the toast is published:
    pysepal's ``_TaskTrackerContextManager.__exit__`` only raises its own
    bare-exception toast while the task is not already FAILED, so failing first
    is what keeps this to exactly one toast. The failure is logged with its
    traceback once the task is FAILED (a FAILED task takes no more milestones,
    so the record is not echoed into the pill), for the server log; callers
    need not log it again.
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
                    message = (error_format or error_text)(exc)
                except Exception:  # a broken formatter must never mask the real failure
                    logging.getLogger(_LOGGER_NAME).exception("error_format failed")
                    message = str(exc)
                task.fail(message)
                logging.getLogger(_LOGGER_NAME).error("Task %r failed: %s", title, message, exc_info=exc)
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


def run_button_job(title, body, *, release, on_success=None, on_refused=None, refused_log=None,
                   error_format=None, logger=None, spawn=None, notifier=None):
    """Run the job a button started on a worker, with the choreography every
    such button shares. The caller has already spun its button.

    On the worker, ``body(task, notifier)`` runs under ``tracked_job(notifier,
    title, error_format)``:

    * it returns None: the job is done. ``on_success(notifier)`` runs after
      the tracked block, so its toast follows the finished task.
    * it returns a reason: a refusal, a WARNING outcome. The task is failed
      with the reason before the warning toast, so the run is recorded as
      FAILED when the message lands; then ``refused_log`` (a label) logs it,
      last because a FAILED task takes no more milestones; then
      ``on_refused(reason)`` runs. A hook that raises is logged and changes
      nothing else: the refusal has already been reported.
    * it raises: ``tracked_job`` has failed the task, toasted and logged it.

    ``release()`` runs last on the worker whatever happened. If the worker
    cannot be started at all, that is logged and ``release()`` runs at once,
    so the button never spins forever.

    ``spawn`` and ``notifier`` default to ``solara_threads.spawn_in_context``
    and ``current_notifier``. A caller passes its own module's names, so a
    test that patches them there still reaches the job.
    """
    log = logger or logging.getLogger(_LOGGER_NAME)
    if spawn is None:
        from gui.scripts.solara_threads import spawn_in_context as spawn
    make_notifier = notifier or current_notifier

    def _worker():
        notifier_ = make_notifier()
        done = False
        try:
            with tracked_job(notifier_, title, error_format=error_format) as task:
                reason = body(task, notifier_)
                if reason is not None:
                    task.fail(reason)
                    notifier_.warning(reason)
                    if refused_log:
                        log.warning("%s — %s", refused_log, reason)
                    if on_refused is not None:
                        try:
                            on_refused(reason)
                        except Exception:
                            log.exception("%s: the refusal hook failed", title)
                done = reason is None
        except Exception:  # noqa: BLE001 — tracked_job toasted and logged it (traceback included)
            pass
        else:
            if done and on_success is not None:
                on_success(notifier_)
        finally:
            release()

    try:
        spawn(_worker)
    except Exception:
        log.exception("%s: the worker could not be started", title)
        release()


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
