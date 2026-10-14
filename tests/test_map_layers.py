import threading
from types import SimpleNamespace

import pytest

from gui.scripts.map_layers import MapLayers


class FakeMap:
    def __init__(self):
        self.removed = []
        self.layers = {}  # key -> the layer currently on the map under that key

    @property
    def drawn(self):
        """The keys currently on the map."""
        return set(self.layers)

    def add_layer(self, layer, key=""):
        self.layers[key] = layer

    def remove_layer(self, key, none_ok=False):
        self.removed.append(key)
        self.layers.pop(key, None)


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


def _slow_build(map_, key, started, release, layer="slow"):
    def build():
        started.set()
        assert release.wait(5)
        map_.add_layer(layer, key=key)

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
    thread, and such a subscriber (or an effect it runs) may call
    ``hide``/``show``/``clear``. Publishing under the registry lock would hang
    that thread on the lock it already holds.
    """
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    layers.on.subscribe(lambda keys: layers.hide("other"))
    worker = threading.Thread(target=lambda: layers.show("k", lambda: None), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "publishing `on` under the registry lock deadlocked"
    assert layers.is_on("k")


def test_inflight_subscribers_may_call_back_into_the_registry(monkeypatch):
    """``inflight`` is published outside every lock the registry holds, too.

    Its subscribers (a map toggle's spinner, an effect re-issuing a show once a
    key is free) are re-rendered synchronously by the publish, and may call
    ``show``/``hide`` from there without hanging the publishing thread.
    """
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    calls = []

    def on_inflight(keys):
        if "k" in keys and not calls:
            calls.append(keys)
            layers.hide("other")
            layers.show("side", lambda: None)

    layers.inflight.subscribe(on_inflight)
    worker = threading.Thread(target=lambda: layers.show("k", lambda: None), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "publishing `inflight` under a lock deadlocked"
    assert calls and layers.is_on("k") and layers.is_on("side")
    assert layers.inflight.value == frozenset()


# ---------------------------------------------------------------------------
# A show or refresh on a key whose build is still running is never dropped:
# the latest request wins, and it runs once the running build has cleaned up.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("switch_off", ["hide", "clear"])
def test_a_show_while_the_previous_build_runs_is_not_lost(monkeypatch, switch_off):
    """Leaving Overview and coming back (hide, then show) or a project switch
    that re-shows the same key (clear, then show) while the first build still
    runs: the first build's layer goes, and the second show draws its own."""
    threads = _run_threaded(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    started, release = threading.Event(), threading.Event()
    layers.show("k", _slow_build(m, "k", started, release, "old"), content_id="c1")
    assert started.wait(5)
    layers.hide("k") if switch_off == "hide" else layers.clear()
    built = []

    def new():
        built.append("new")
        m.add_layer("new", key="k")

    layers.show("k", new, content_id="c2")
    assert layers.is_on("k") and "k" in layers.inflight.value  # waiting for the old build
    release.set()
    _join(threads)
    assert built == ["new"]
    assert layers.is_on("k") and m.layers == {"k": "new"}
    assert layers.inflight.value == frozenset()
    extra = []
    layers.refresh("k", lambda: extra.append(1), "c2")  # c2 is what is drawn
    _join(threads)
    assert extra == []


def test_a_refresh_during_a_build_redraws_the_new_content(monkeypatch):
    """A re-download replaces the file while its layer is still building: the
    layer ends up built from the new content, never on the old tile cache."""
    threads = _run_threaded(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    started, release = threading.Event(), threading.Event()
    layers.show("k", _slow_build(m, "k", started, release, "c1"), content_id="c1")
    assert started.wait(5)
    layers.refresh("k", lambda: m.add_layer("c2", key="k"), "c2")
    extra = []
    layers.refresh("k", lambda: extra.append(1), "c2")  # already requested
    release.set()
    _join(threads)
    assert extra == []
    assert layers.is_on("k") and m.layers == {"k": "c2"}
    layers.refresh("k", lambda: extra.append(2), "c2")
    _join(threads)
    assert extra == []


def test_a_superseded_build_that_fails_is_not_reported(monkeypatch):
    """The build a refresh superseded fails: the key stays on with the new
    layer, and nobody is told about a build whose result was never wanted."""
    threads = _run_threaded(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    started, release = threading.Event(), threading.Event()
    errors = []

    def doomed():
        started.set()
        assert release.wait(5)
        raise RuntimeError("tile server down")

    layers.show("k", doomed, content_id="c1", notifier=SimpleNamespace(error=errors.append))
    assert started.wait(5)
    layers.refresh("k", lambda: m.add_layer("c2", key="k"), "c2")
    release.set()
    _join(threads)
    assert errors == []
    assert layers.is_on("k") and m.layers == {"k": "c2"}


class _SlowRemovalMap(FakeMap):
    """The first ``remove_layer`` of ``key`` blocks until released, like a
    ``hide`` preempted between its registry update and its map removal."""

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.entered, self.release = threading.Event(), threading.Event()
        self._blocked = False

    def remove_layer(self, key, none_ok=False):
        if key == self.key and not self._blocked:
            self._blocked = True
            self.entered.set()
            assert self.release.wait(5)
        super().remove_layer(key, none_ok)


def test_a_show_during_a_slow_removal_is_not_undone_by_it(monkeypatch):
    """A ``hide`` still removing the key's layer when the key is shown again
    (a toggle racing a worker's refresh): the removal must not take away the
    new layer, so the new build waits for it."""
    threads = _run_threaded(monkeypatch)
    m = _SlowRemovalMap("k")
    layers = MapLayers(m)
    hider = threading.Thread(target=layers.hide, args=("k",), daemon=True)
    hider.start()
    assert m.entered.wait(5)
    layers.show("k", lambda: m.add_layer("new", key="k"), content_id="c2")
    assert layers.is_on("k")
    _join(list(threads))  # a build started right away would land before the removal
    m.release.set()
    hider.join(5)
    assert not hider.is_alive(), "hide hung"
    _join(threads)
    assert layers.is_on("k") and m.layers == {"k": "new"}


@pytest.mark.parametrize("published", ["on", "inflight"])
def test_a_raising_subscriber_does_not_leave_the_key_building(monkeypatch, published):
    """A subscriber of ``on`` or ``inflight`` (a render) runs synchronously
    inside the publish and may raise. The pending build must still start, or
    the key stays "building" for the session: a permanent spinner, and every
    toggle on it a no-op."""
    _run_sync(monkeypatch)
    m = FakeMap()
    layers = MapLayers(m)
    raised = []

    def bad_subscriber(keys):
        if not raised:
            raised.append(keys)
            raise RuntimeError("a render failed")

    getattr(layers, published).subscribe(bad_subscriber)
    built = []

    def build():
        built.append("k")
        m.add_layer("layer", key="k")

    with pytest.raises(RuntimeError, match="a render failed"):
        layers.show("k", build)

    assert built == ["k"] and layers.is_on("k") and m.drawn == {"k"}
    assert layers.inflight.value == frozenset()
    layers.toggle("k", lambda: pytest.fail("the key is free: a click hides it, nothing is rebuilt"))
    assert not layers.is_on("k") and m.drawn == set()


from gui.scripts import legend_specs  # noqa: E402


def _legend(key, name):
    return legend_specs.alert_legend(key, name, legend_specs.preview_title(name, "a", "b"))


def _ids(layers):
    return [e.layer_id for e in layers.legends.value]


def test_a_legend_is_published_only_for_a_landed_build(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    layers.show("src:RADD", lambda: None, legend=_legend("src:RADD", "RADD"))
    assert _ids(layers) == ["src:RADD"] and layers.selected.value == "src:RADD"
    layers.show("src:GLAD-S2", _bad, legend=_legend("src:GLAD-S2", "GLAD-S2"))
    assert _ids(layers) == ["src:RADD"]  # a failed build publishes nothing


def test_a_superseded_build_publishes_no_legend(monkeypatch):
    import gui.scripts.map_layers as ml
    started = []
    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: started.append((target, args)))
    layers = MapLayers(FakeMap())
    layers.show("src:RADD", lambda: None, legend=_legend("src:RADD", "RADD"))
    layers.hide("src:RADD")  # switched off while still building
    target, args = started[0]
    target(*args)  # the build lands late
    assert _ids(layers) == []


def test_hide_and_clear_remove_legends_and_the_selection_falls_back(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    for k in ("src:A", "src:B", "src:C"):
        layers.show(k, lambda: None, legend=_legend(k, k))
    assert layers.selected.value == "src:C"
    layers.select("src:A")
    layers.hide("src:A")
    assert _ids(layers) == ["src:B", "src:C"] and layers.selected.value == "src:C"
    layers.clear()
    assert _ids(layers) == [] and layers.selected.value == ""


def test_refresh_replaces_the_legend_with_or_without_a_rebuild(monkeypatch):
    _run_sync(monkeypatch)
    layers = MapLayers(FakeMap())
    built = []
    layers.show("src:RADD", lambda: built.append(1), content_id=None,
                legend=_legend("src:RADD", "RADD · preview"))
    layers.refresh("src:RADD", lambda: built.append(2), "c1", legend=_legend("src:RADD", "RADD · 2026-01-01 → 2026-01-31"))
    assert built == [1, 2]
    assert layers.legends.value[0].label.literal == "RADD · 2026-01-01 → 2026-01-31"
    mask_off = legend_specs.mask_legend("src:RADD", "x", None)
    layers.refresh("src:RADD", lambda: built.append(3), "c1", legend=mask_off)  # same content: legend only
    assert built == [1, 2] and layers.legends.value[0] is mask_off
    layers.refresh("src:RADD", lambda: built.append(4), "c2")  # new pixels and no legend: the old one goes
    assert built == [1, 2, 4] and _ids(layers) == [] and layers.is_on("src:RADD")


def test_legends_publish_into_the_given_reactives(monkeypatch):
    import solara
    _run_sync(monkeypatch)
    legends, selected = solara.reactive(()), solara.reactive("")
    layers = MapLayers(FakeMap(), legends=legends, selected=selected)
    layers.show("k", lambda: None, legend=_legend("k", "k"))
    assert [e.layer_id for e in legends.value] == ["k"] and selected.value == "k"


def test_a_superseded_rebuild_takes_the_old_legend_with_its_layer(monkeypatch):
    """Spec §9.4: a legend never outlives its layer. A superseded rebuild
    removes the key's layer from the map, so the legend that layer carried
    goes too; the newer build publishes its own once it lands."""
    import gui.scripts.map_layers as ml
    started = []
    monkeypatch.setattr(ml, "spawn_in_context", lambda target, args=(), **kw: started.append((target, args)))
    m = FakeMap()
    layers = MapLayers(m)
    layers.show("k", lambda: m.add_layer("c1", key="k"), content_id="c1", legend=_legend("k", "L1"))
    target, args = started.pop(0)
    target(*args)
    assert _ids(layers) == ["k"]
    layers.refresh("k", lambda: m.add_layer("c2", key="k"), "c2", legend=_legend("k", "L2"))  # B2 starts
    l3 = _legend("k", "L3")
    layers.refresh("k", lambda: m.add_layer("c3", key="k"), "c3", legend=l3)  # B3 waits for B2
    target, args = started.pop(0)
    target(*args)  # B2 lands, superseded: its layer is removed, and the old legend with it
    assert m.drawn == set() and layers.legends.value == () and layers.selected.value == ""
    target, args = started.pop(0)
    target(*args)  # B3 lands
    assert m.drawn == {"k"} and layers.legends.value == (l3,) and layers.selected.value == "k"
