"""``gui.scripts.detached``: the tiles MapApp shows in its widget slots.

A tile is rendered into a container of its own, outside the enclosing render
context. When its render raises, nothing is raised into the publisher whose
reactive set triggered the render: the slot shows the traceback, the app
logger gets it (the server log), and a toast names the tile.
"""

import logging
import threading
import time

import ipyvuetify as v
import ipywidgets
import pytest
import reacton
import solara
from pysepal.solara.notifications.bus import NotificationBus
from pysepal.solara.notifications.notifier import Notifier
from pysepal.solara.notifications.state import ToastType

from gui.i18n import t
from gui.scripts import detached
from gui.scripts.detached import render_detached

broken = solara.reactive(False)
counter = solara.reactive(0)


@solara.component
def Tile():
    if broken.value:
        raise RuntimeError("tile broke")
    solara.Text(f"tile {counter.value}")


@pytest.fixture
def healthy():
    broken.set(False)
    counter.set(0)
    yield
    broken.set(False)


@pytest.fixture
def toasts(monkeypatch):
    """The toasts ``render_detached`` publishes, and the threads it reports on."""
    bus = NotificationBus()
    threads = []
    real_spawn = detached.spawn_in_context

    def _spawn(target, args=(), **kw):
        thread = real_spawn(target, args, **kw)
        threads.append(thread)
        return thread

    monkeypatch.setattr(detached, "spawn_in_context", _spawn)
    monkeypatch.setattr(detached, "current_notifier", lambda: Notifier(bus))

    def _join():
        for thread in threads:
            thread.join(timeout=5)
        return [(toast.type, toast.message) for toast in bus.toasts.value]

    return _join


def test_the_tile_renders_into_its_own_container_and_follows_its_reactives(healthy):
    container, rc = render_detached(lambda: Tile(), "demo", notify=False)
    try:
        assert isinstance(container, v.Html) and container.tag == "div"
        assert container.children[0].children == ["tile 0"]
        counter.set(1)
        assert container.children[0].children == ["tile 1"]
    finally:
        rc.close()


def test_the_tile_is_not_added_to_the_enclosing_render_context(healthy):
    """Built inside another component's body (the shell builds every tile in
    a ``use_memo`` of ``_AppShell``), the tile must not also land in that
    component's own tree: reacton auto-parents a freshly constructed element
    into the active context, which rendered every tile twice."""
    built = []

    @solara.component
    def Shell():
        built.append(solara.use_memo(lambda: render_detached(lambda: Tile(), "demo", notify=False), []))
        solara.Text("shell")

    box, rc = reacton.render(Shell(), handle_error=False)
    try:
        texts = [w.children for w in rc.find(v.Html, tag="span").widgets]
        assert texts == [["shell"]]  # the tile's own text is not in the shell's tree
        [(container, tile_rc)] = built
        assert container.children[0].children == ["tile 0"]
    finally:
        rc.close()
        tile_rc.close()


def test_a_failing_render_is_kept_out_of_the_publisher_shown_logged_and_toasted(healthy, toasts, caplog):
    """The publisher (an auto-save, a worker's ``update_project``) must not
    see the failure: its other listeners still run. The tile's slot shows the
    traceback, the app logger gets it with the tile's name, and a toast names
    the tile and the exception."""
    seen = []
    container, rc = render_detached(lambda: Tile(), "Demo")
    broken.subscribe(seen.append)  # a listener registered after the tile
    try:
        with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts"):
            broken.set(True)  # raises nothing
            assert seen == [True]
            published = toasts()
        [widget] = container.children
        assert isinstance(widget, ipywidgets.HTML)
        assert "RuntimeError: tile broke" in widget.value and "Traceback" in widget.value
        [record] = [r for r in caplog.records if r.name == "SEPALUI.deforestation_alerts"]
        assert record.levelno == logging.ERROR
        assert "Demo" in record.getMessage()
        assert "RuntimeError: tile broke" in record.getMessage() and "Traceback (most recent call last)" in record.getMessage()
        assert "&quot;" not in record.getMessage()  # the HTML escaping is undone
        assert published == [(ToastType.ERROR, t("app.tile_failed", tile="Demo", error="RuntimeError: tile broke"))]
    finally:
        rc.close()


def test_the_report_runs_off_the_failed_render(healthy, monkeypatch):
    """The container's observer fires inside the failed render, under that
    context's render lock; the log line and the toast go through a helper
    thread so neither takes another context's lock from there."""
    on_thread = []
    monkeypatch.setattr(detached, "spawn_in_context",
                        lambda target, args=(), **kw: on_thread.append(threading.current_thread()) or target())
    monkeypatch.setattr(detached, "current_notifier", lambda: Notifier(NotificationBus()))
    container, rc = render_detached(lambda: Tile(), "Demo")
    try:
        broken.set(True)
        assert on_thread == [threading.current_thread()]  # spawned from the setting thread, not inline
    finally:
        rc.close()


def test_notify_false_keeps_the_report_to_the_log(healthy, toasts, caplog):
    container, rc = render_detached(lambda: Tile(), "Demo", notify=False)
    try:
        with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts"):
            broken.set(True)
            assert toasts() == []
        assert any("Demo" in r.getMessage() for r in caplog.records)
    finally:
        rc.close()


def test_a_failure_on_a_worker_thread_is_reported_the_same_way(healthy, toasts, caplog):
    """The renders that failed in the browser walk ran on worker threads (the
    auto-save of a Prepare row, an Integrate, a review Save)."""
    errors = []
    container, rc = render_detached(lambda: Tile(), "Demo")
    try:
        with caplog.at_level(logging.ERROR, logger="SEPALUI.deforestation_alerts"):

            def worker():
                try:
                    broken.set(True)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=10)
            deadline = time.monotonic() + 5
            while not caplog.records and time.monotonic() < deadline:
                time.sleep(0.01)
            published = toasts()
        assert errors == []
        assert isinstance(container.children[0], ipywidgets.HTML)
        assert len(published) == 1 and "Demo" in published[0][1]
    finally:
        rc.close()
