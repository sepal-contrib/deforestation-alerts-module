import threading
from types import SimpleNamespace

import pytest

from gui.scripts.map_layers import MapLayers


class FakeMap:
    def __init__(self):
        self.removed = []
        self.drawn = set()  # keys currently on the map

    def add_layer(self, layer, key=""):
        self.drawn.add(key)

    def remove_layer(self, key, none_ok=False):
        self.removed.append(key)
        self.drawn.discard(key)


def _run_sync(monkeypatch):
    import gui.scripts.map_layers as ml

    def spawn(target, args=(), **kw):
        target(*args)
        return threading.current_thread()

    monkeypatch.setattr(ml, "spawn_in_context", spawn)


def test_toggle_on_and_off(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    built = []
    layers.toggle("k", lambda: built.append("k"))
    assert layers.is_on("k") and built == ["k"]
    layers.toggle("k", lambda: built.append("again"))
    assert not layers.is_on("k") and built == ["k"]
    assert layers.map_.removed == ["k"]


def test_refresh_rebuilds_only_when_content_changes(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    built = []
    layers.show("k", lambda: built.append(1), content_id="c1")
    layers.refresh("k", lambda: built.append(2), "c1")
    layers.refresh("k", lambda: built.append(3), "c2")
    layers.refresh("off", lambda: built.append(4), "c9")  # not shown: ignored
    assert built == [1, 3]


def test_failed_build_turns_the_key_off(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())

    def bad():
        raise RuntimeError("tile server down")

    layers.show("k", bad)
    assert not layers.is_on("k")


def test_clear_removes_everything(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    layers.show("a", lambda: None)
    layers.show("b", lambda: None)
    layers.clear()
    assert layers.on.value == frozenset() and set(layers.map_.removed) == {"a", "b"}


def _bad():
    raise RuntimeError("tile server down")


def test_a_failed_build_is_reported_through_the_notifier(monkeypatch):
    _run_sync(monkeypatch)
    errors = []
    layers = MapLayers(FakeMap())
    layers.show("k", _bad, notifier=SimpleNamespace(error=errors.append))
    assert errors == ["tile server down"]


def test_a_failed_rebuild_drops_the_stale_layer(monkeypatch):
    """The registry and the map agree: a key turned off by a failed rebuild must
    not leave the previous tile layer (and its stale tile cache) on the map."""
    _run_sync(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    layers.show("k", lambda: m.add_layer(object(), key="k"), content_id="c1")
    layers.refresh("k", _bad, "c2")
    assert not layers.is_on("k") and m.drawn == set()


# ---------------------------------------------------------------------------
# Real worker threads: a build still running when the user (or a project
# switch) acts on the same key.
# ---------------------------------------------------------------------------
def _run_threaded(monkeypatch):
    import gui.scripts.map_layers as ml

    real = ml.spawn_in_context
    threads = []

    def spawn(target, args=(), **kw):
        thread = real(target, args, **kw)
        threads.append(thread)
        return thread

    monkeypatch.setattr(ml, "spawn_in_context", spawn)
    return threads


def _join(threads):
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive(), "a map-layer worker hung"


def _slow_build(map_, key, started, release):
    def build():
        started.set()
        assert release.wait(5)
        map_.add_layer(object(), key=key)

    return build


def test_a_second_toggle_while_building_is_a_no_op(monkeypatch):
    threads = _run_threaded(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    started, release = threading.Event(), threading.Event()
    layers.toggle("k", _slow_build(m, "k", started, release))
    assert started.wait(5)
    layers.toggle("k", lambda: pytest.fail("a second click must not rebuild"))
    release.set()
    _join(threads)
    assert layers.is_on("k") and m.drawn == {"k"}


@pytest.mark.parametrize("switch_off", ["hide", "clear"])
def test_a_layer_switched_off_while_building_never_lands(monkeypatch, switch_off):
    """A panel exit (hide) or a project switch (clear) during a slow build: the
    build finishes afterwards, and its layer must not stay on the map."""
    threads = _run_threaded(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    started, release = threading.Event(), threading.Event()
    layers.show("k", _slow_build(m, "k", started, release), content_id="c1")
    assert started.wait(5)
    layers.hide("k") if switch_off == "hide" else layers.clear()
    release.set()
    _join(threads)
    assert not layers.is_on("k") and m.drawn == set()
    # the key is free again, and showing it builds afresh
    layers.show("k", lambda: m.add_layer(object(), key="k"), content_id="c1")
    _join(threads)
    assert layers.is_on("k") and m.drawn == {"k"}


def test_subscribers_may_call_back_into_the_registry(monkeypatch):
    """``on`` is published outside the registry lock.

    A reactive ``set`` re-renders its subscribers synchronously in the setting
    thread, and effects (which run inside reacton's render lock) call
    ``hide``/``show``/``clear``. Publishing under the lock would deadlock such a
    subscriber against the thread that holds it.
    """
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    layers.on.subscribe(lambda keys: layers.hide("other"))
    worker = threading.Thread(target=lambda: layers.show("k", lambda: None), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "publishing `on` under the registry lock deadlocked"
    assert layers.is_on("k")
