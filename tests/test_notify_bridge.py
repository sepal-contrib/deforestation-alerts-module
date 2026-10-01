"""notify_bridge: app log milestones -> pysepal notification tasks.

The bridge replaces the old on-map LogConsole: instead of rendering raw log
records in a custom widget, each background job opens a pysepal TaskTracker
(``tracked_job``) and the ``TaskStepLogHandler`` forwards INFO+ records emitted
on the job's own thread as task milestones, so granular library progress
surfaces in the official notification pill.
"""

import logging
import threading

import pytest
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import TaskStatus, ToastType

from gui.scripts import notify_bridge


@pytest.fixture(autouse=True)
def _restore_app_logger_level():
    """``_fresh`` drops the app logger to DEBUG; put the previous level back."""
    logger = logging.getLogger("SEPALUI.deforestation_alerts")
    previous = logger.level
    yield
    logger.setLevel(previous)


def _fresh():
    """Real app logger with the bridge handler + a private bus.

    The DEBUG level it sets is undone after each test by the autouse
    ``_restore_app_logger_level`` fixture.
    """
    notify_bridge.install_task_log_handler()
    logger = logging.getLogger("SEPALUI.deforestation_alerts")
    logger.setLevel(logging.DEBUG)
    bus = NotificationBus()
    return logger, bus, Notifier(bus)


def _milestones(bus, title):
    task = next(t for t in bus.tasks.value if t.title == title)
    return [m.message for m in task.milestones]


def _status(bus, title):
    return next(t for t in bus.tasks.value if t.title == title).status


def test_job_log_lines_become_milestones_and_task_completes():
    """INFO lines from the job thread land as milestones; exit completes."""
    logger, bus, notifier = _fresh()
    with notify_bridge.tracked_job(notifier, "My job"):
        logger.info("Step one")
        logger.info("Step %d", 2)
    assert _milestones(bus, "My job") == ["Step one", "Step 2"]
    assert _status(bus, "My job") == TaskStatus.COMPLETED


def test_debug_lines_are_not_forwarded():
    """DEBUG records stay out of the milestone log."""
    logger, bus, notifier = _fresh()
    with notify_bridge.tracked_job(notifier, "Quiet job"):
        logger.debug("hidden debug")
    assert _milestones(bus, "Quiet job") == []


def test_lines_from_unrelated_threads_are_not_forwarded():
    """Only the job's own thread feeds its tracker.

    A concurrent log line from elsewhere (another session, an unrelated
    worker) must not pollute the task's milestone list.
    """
    logger, bus, notifier = _fresh()
    with notify_bridge.tracked_job(notifier, "Mine"):
        other = threading.Thread(target=lambda: logger.info("not mine"))
        other.start()
        other.join()
        logger.info("mine")
    assert _milestones(bus, "Mine") == ["mine"]


def test_exception_marks_task_failed_with_error_toast_and_reraises():
    """A job exception fails the task, raises a toast, and re-raises."""
    logger, bus, notifier = _fresh()
    with pytest.raises(ValueError, match="boom"):
        with notify_bridge.tracked_job(notifier, "Doomed job"):
            logger.info("about to fail")
            raise ValueError("boom")
    task = next(t for t in bus.tasks.value if t.title == "Doomed job")
    assert task.status == TaskStatus.FAILED
    assert task.error_message == "boom"
    assert any(
        t.type == ToastType.ERROR and "boom" in t.message for t in bus.toasts.value
    )
    # The failed thread's registration must not leak into later log calls.
    logger.info("after the failure")
    assert "after the failure" not in _milestones(bus, "Doomed job")


def test_no_active_job_is_a_noop():
    """Log lines with no tracked job on the thread are ignored."""
    logger, bus, _notifier = _fresh()
    logger.info("nobody is tracking this")
    assert bus.tasks.value == []


def test_install_is_idempotent():
    """Repeated installs attach exactly one bridge handler."""
    notify_bridge.install_task_log_handler()
    notify_bridge.install_task_log_handler()
    handlers = [
        h
        for h in logging.getLogger("SEPALUI.deforestation_alerts").handlers
        if isinstance(h, notify_bridge.TaskStepLogHandler)
    ]
    assert len(handlers) == 1


def test_concurrent_jobs_keep_milestones_separate():
    """Two jobs running at once each see only their own log lines."""
    logger, bus, notifier = _fresh()
    barrier = threading.Barrier(2, timeout=5)

    def job(title, line):
        with notify_bridge.tracked_job(notifier, title):
            barrier.wait()  # both jobs are tracking before either logs
            logger.info(line)
            barrier.wait()  # neither exits before both have logged

    t1 = threading.Thread(target=job, args=("Job A", "line A"))
    t2 = threading.Thread(target=job, args=("Job B", "line B"))
    t1.start(), t2.start()
    t1.join(), t2.join()
    assert _milestones(bus, "Job A") == ["line A"]
    assert _milestones(bus, "Job B") == ["line B"]


def test_to_thread_in_context_attaches_caller_context_to_pool_thread(monkeypatch):
    """Job bodies tracked inside ``asyncio.to_thread`` publish from a pool thread.

    Without the caller's kernel context attached first, those updates never
    reach the browser (same reason spawn_in_context exists).
    """
    import asyncio

    from solara.server import kernel_context

    from gui.scripts.solara_threads import to_thread_in_context

    sentinel = object()
    attached = []
    monkeypatch.setattr(kernel_context, "get_current_context", lambda: sentinel)
    monkeypatch.setattr(kernel_context, "has_current_context", lambda: False)
    monkeypatch.setattr(
        kernel_context,
        "set_context_for_thread",
        lambda ctx, thread: attached.append((ctx, thread)),
    )

    caller = threading.current_thread()

    def work(x):
        return (threading.current_thread(), x * 2)

    worker, result = asyncio.run(to_thread_in_context(work, 21))
    assert result == 42
    assert worker is not caller
    assert attached and attached[0] == (sentinel, worker)


def test_to_thread_in_context_without_context_still_runs(monkeypatch):
    """No kernel context (plain scripts/tests) must not break the helper."""
    import asyncio

    from solara.server import kernel_context

    from gui.scripts.solara_threads import to_thread_in_context

    def _raise():
        raise RuntimeError("no context")

    monkeypatch.setattr(kernel_context, "get_current_context", _raise)
    assert asyncio.run(to_thread_in_context(lambda: "ok")) == "ok"


def test_to_thread_in_context_rebinds_a_reused_pool_thread_per_call(monkeypatch):
    """A reused pool thread must publish into *each* caller's kernel.

    pysepal 4 app-launcher containers are multi-user: the same ``to_thread``
    pool thread can serve one kernel's job and then another's. Each call must
    bind the pool thread to its own caller's context (not keep the first one
    it ever saw) and unbind it afterwards so nothing stale lingers.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from solara.server import kernel_context

    from gui.scripts.solara_threads import to_thread_in_context

    ctx_a, ctx_b = object(), object()
    caller_ctx = [None]
    # Isolated registry; the real set/clear/has helpers operate on it.
    monkeypatch.setattr(kernel_context, "current_context", {})
    monkeypatch.setattr(kernel_context, "get_current_context", lambda: caller_ctx[0])

    def bound_context():
        key = kernel_context.get_thread_key(threading.current_thread())
        return threading.current_thread(), kernel_context.current_context.get(key)

    async def main():
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=1)
        )
        caller_ctx[0] = ctx_a
        first = await to_thread_in_context(bound_context)
        caller_ctx[0] = ctx_b
        second = await to_thread_in_context(bound_context)
        return first, second

    (thread_1, seen_1), (thread_2, seen_2) = asyncio.run(main())
    assert thread_1 is thread_2, "single-worker pool must reuse its thread"
    assert seen_1 is ctx_a
    assert seen_2 is ctx_b
    pool_key = kernel_context.get_thread_key(thread_2)
    assert pool_key not in kernel_context.current_context


def test_nested_tracked_jobs_restore_the_outer_tracker():
    """Exiting an inner tracked job re-registers the outer one."""
    logger, bus, notifier = _fresh()
    with notify_bridge.tracked_job(notifier, "Outer"):
        with notify_bridge.tracked_job(notifier, "Inner"):
            logger.info("inner line")
        logger.info("outer line")
    assert _milestones(bus, "Inner") == ["inner line"]
    assert _milestones(bus, "Outer") == ["outer line"]


def test_error_format_localizes_the_failure_toast_and_task():
    """A formatter turns the raw exception into the user-facing message."""
    _logger, bus, notifier = _fresh()
    with pytest.raises(ValueError, match="boom"):
        with notify_bridge.tracked_job(
            notifier,
            "Harmonizing variables",
            error_format=lambda exc: f"Harmonization failed: {exc}",
        ):
            raise ValueError("boom")

    task = next(t for t in bus.tasks.value if t.title == "Harmonizing variables")
    assert task.status == TaskStatus.FAILED
    assert task.error_message == "Harmonization failed: boom"
    errors = [t for t in bus.toasts.value if t.type == ToastType.ERROR]
    assert [t.message for t in errors] == ["Harmonization failed: boom"]
    assert errors[0].effective_timeout() == notify_bridge.ERROR_TOAST_TIMEOUT


def test_broken_error_format_does_not_mask_the_original_exception():
    """A raising error_format must not replace the real failure.

    The task still fails and toasts with str(exc) of the ORIGINAL exception,
    and the ORIGINAL exception is what propagates — not the formatter's.
    """
    _logger, bus, notifier = _fresh()

    def broken_formatter(exc):
        raise RuntimeError("formatter is broken")

    with pytest.raises(ValueError, match="^boom$"):
        with notify_bridge.tracked_job(
            notifier, "Fragile job", error_format=broken_formatter
        ):
            raise ValueError("boom")

    task = next(t for t in bus.tasks.value if t.title == "Fragile job")
    assert task.status == TaskStatus.FAILED
    assert task.error_message == "boom"
    errors = [t for t in bus.toasts.value if t.type == ToastType.ERROR]
    assert [t.message for t in errors] == ["boom"]


def test_failure_publishes_exactly_one_error_toast():
    """Pysepal's own __exit__ toast must not fire on top of ours.

    bus.add_toast keeps only the newest ERROR toast, so the bus cannot show a
    duplicate — count the publications at the notifier instead.
    """
    _logger, _bus, notifier = _fresh()
    published = []
    original = notifier.error

    def spy(message, *, timeout=None):
        published.append((message, timeout))
        return original(message, timeout=timeout)

    notifier.error = spy
    with pytest.raises(RuntimeError):
        with notify_bridge.tracked_job(notifier, "One toast only"):
            raise RuntimeError("kaboom")

    assert published == [("kaboom", notify_bridge.ERROR_TOAST_TIMEOUT)]


def test_cancellation_is_not_reported_as_a_failure():
    """A cancelled job stays CANCELLED and raises no error toast."""
    import asyncio

    _logger, bus, notifier = _fresh()
    with pytest.raises(asyncio.CancelledError):
        with notify_bridge.tracked_job(notifier, "Cancelled job"):
            raise asyncio.CancelledError()

    task = next(t for t in bus.tasks.value if t.title == "Cancelled job")
    assert task.status == TaskStatus.CANCELLED
    assert [t for t in bus.toasts.value if t.type == ToastType.ERROR] == []


def test_forward_logs_to_registers_the_current_thread_only():
    logger, bus, notifier = _fresh()
    with notifier.track("fw") as task:
        with notify_bridge.forward_logs_to(task):
            logger.info("inside")
        logger.info("after")
    assert _milestones(bus, "fw") == ["inside"]


def test_tile_reporter_throttles_to_whole_percent():
    bus = NotificationBus()
    seen = []
    with Notifier(bus).track("tiles") as task:
        task.set_progress = seen.append  # spy
        report = notify_bridge.tile_progress_reporter(task)
        for done in range(1, 1001):
            report(done, 1000)
    assert len(seen) == 101  # one publish per whole percent, 0..100
    assert seen[-1] == 1.0


def test_tile_reporter_ignores_a_zero_total():
    bus = NotificationBus()
    seen = []
    with Notifier(bus).track("tiles0") as task:
        task.set_progress = seen.append
        notify_bridge.tile_progress_reporter(task)(0, 0)
    assert seen == []


def test_tile_reporter_after_task_finished_is_a_noop():
    """A cancelled Analyze abandons its download thread, which keeps ticking."""
    bus = NotificationBus()
    with Notifier(bus).track("done") as task:
        pass
    notify_bridge.tile_progress_reporter(task)(5, 10)  # must not raise


def test_no_provider_falls_back_to_noop():
    from pysepal.solara.notifications import NoopNotifier

    assert isinstance(notify_bridge.current_notifier(), NoopNotifier)
    with notify_bridge.tracked_job(None, "orphan") as task:
        task.step("still fine")


# ---------------------------------------------------------------------------
# The server log: failures leave a traceback on the server's stderr
# ---------------------------------------------------------------------------
@pytest.fixture
def server_log():
    """The app logger's server-log handler on a private stream, removed after."""
    import io

    logger = logging.getLogger("SEPALUI.deforestation_alerts")
    for h in list(logger.handlers):
        if isinstance(h, notify_bridge.ServerLogHandler):
            logger.removeHandler(h)
    stream = io.StringIO()
    handler = notify_bridge.install_server_log_handler(stream=stream)
    yield stream
    logger.removeHandler(handler)


def test_a_failed_job_is_logged_with_its_traceback_once_it_is_failed(caplog):
    """The record carries the task's title and the exception (traceback
    included), goes out after ``fail()`` so it is not echoed as a milestone,
    and sits next to exactly one error toast."""
    logger, bus, notifier = _fresh()  # DEBUG: the milestone must still get through
    with pytest.raises(ValueError, match="boom"):
        with notify_bridge.tracked_job(notifier, "Doomed job"):
            logger.info("about to fail")
            raise ValueError("boom")
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "Doomed job" in record.getMessage() and "boom" in record.getMessage()
    assert record.exc_info is not None and isinstance(record.exc_info[1], ValueError)
    assert _milestones(bus, "Doomed job") == ["about to fail"]
    assert _status(bus, "Doomed job") == TaskStatus.FAILED
    assert [t.message for t in bus.toasts.value if t.type == ToastType.ERROR] == ["boom"]


def test_the_server_log_handler_prints_warnings_and_tracebacks_not_milestones(server_log):
    logger, _bus, _notifier = _fresh()
    logger.info("a milestone")
    logger.warning("careful")
    try:
        raise ValueError("bad")
    except ValueError:
        logger.exception("the row failed")
    out = server_log.getvalue()
    assert "a milestone" not in out
    assert "WARNING SEPALUI.deforestation_alerts" in out and "careful" in out
    assert "ERROR" in out and "the row failed" in out
    assert "Traceback (most recent call last)" in out and "ValueError: bad" in out
    assert notify_bridge.install_server_log_handler() is next(
        h for h in logger.handlers if isinstance(h, notify_bridge.ServerLogHandler))
    assert sum(isinstance(h, notify_bridge.ServerLogHandler) for h in logger.handlers) == 1


def test_a_failed_tracked_job_reaches_the_server_log_once(server_log):
    """A worker's failure (a Prepare row, an Integrate, a Save) is findable in
    the server log with its traceback, once: callers do not log it again."""
    logger, bus, notifier = _fresh()

    def job():
        with notify_bridge.tracked_job(notifier, "Integrate & vectorize"):
            logger.info("combining")
            raise RuntimeError("tippecanoe died")

    thread = threading.Thread(target=job, name="integrate-worker")
    thread.start()
    thread.join(timeout=10)
    out = server_log.getvalue()
    assert out.count("Traceback (most recent call last)") == 1
    assert "Task 'Integrate & vectorize' failed: tippecanoe died" in out
    assert "RuntimeError: tippecanoe died" in out and "[integrate-worker]" in out
    assert "combining" not in out  # milestones stay in the task pill
    assert _milestones(bus, "Integrate & vectorize") == ["combining"]
