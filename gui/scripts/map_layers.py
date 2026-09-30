"""One registry of toggled map layers per session (spec §4.3).

A layer switched on in any step stays on until switched off or the project
changes (``clear``). Builders are blocking (Earth Engine map ids, tile
servers), so they run on a worker thread; ``InflightKeys`` makes a second click
on a key that is still building a no-op. ``refresh`` rebuilds a shown layer
when its artifact's content id changed (spec §9.1: never show a stale tile
cache after a file is replaced).

A key switched off while its layer is still building (``hide`` on a panel exit,
``clear`` on a project switch) stays off: once the builder returns, the worker
re-checks the registry and removes the layer it just drew. A build that fails
turns its key off and removes the key's layer, so the map never keeps a layer
the registry says is off.

Locking: the set of keys (``_keys``) and the content ids change under
``_lock``; the ``on`` reactive is published after the lock is released. A
reactive ``set`` re-renders its subscribers synchronously in the calling thread
(taking reacton's render lock), and effects, which already hold that render
lock, call ``show``/``hide``/``clear``. Publishing under ``_lock`` would let a
worker and an effect deadlock each other.
"""

import logging
import threading

import solara

from gui.scripts.inflight import InflightKeys
from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts")


class MapLayers:
    def __init__(self, map_):
        self.map_ = map_
        self.on = solara.reactive(frozenset())
        self.inflight = InflightKeys()
        self._keys = frozenset()  # the registry itself; ``on`` publishes it
        self._content = {}
        self._lock = threading.Lock()

    def is_on(self, key) -> bool:
        return key in self.on.value

    def _switch(self, key, on: bool) -> None:
        with self._lock:
            if on:
                self._keys = self._keys | {key}
            else:
                self._keys = self._keys - {key}
                self._content.pop(key, None)
        self._publish()

    def _publish(self) -> None:
        # Outside the lock (see the module docstring). Two threads can publish
        # out of order, so re-publish until nobody changed the keys meanwhile:
        # the value left in ``on`` is always the latest one.
        while True:
            keys = self._keys
            self.on.set(keys)
            if self._keys is keys:
                return

    def _build(self, key, build, content_id, notifier):
        try:
            build()
        except Exception as exc:  # noqa: BLE001 - reported as a toast
            logger.exception("map layer %s failed", key)
            self._switch(key, False)
            self.map_.remove_layer(key, none_ok=True)
            if notifier is not None:
                notifier.error(str(exc) or exc.__class__.__name__)
        else:
            with self._lock:
                wanted = key in self._keys
                if wanted:
                    self._content[key] = content_id
            if not wanted:  # hidden or cleared while building
                self.map_.remove_layer(key, none_ok=True)
        finally:
            self.inflight.release(key)

    def show(self, key, build, content_id=None, notifier=None) -> None:
        if not self.inflight.claim(key):
            return
        self._switch(key, True)
        spawn_in_context(self._build, (key, build, content_id, notifier))

    def hide(self, key) -> None:
        # Off first, then remove: a build finishing in between sees the key off
        # and removes its own layer.
        self._switch(key, False)
        self.map_.remove_layer(key, none_ok=True)

    def toggle(self, key, build, content_id=None, notifier=None) -> None:
        if key in self.inflight:
            return  # still building: a second click must not hide a layer about to land
        if key in self._keys:
            self.hide(key)
        else:
            self.show(key, build, content_id, notifier)

    def refresh(self, key, build, content_id) -> None:
        with self._lock:
            stale = key in self._keys and self._content.get(key) != content_id
        if stale:
            self.show(key, build, content_id)

    def clear(self) -> None:
        for key in self._keys:
            self.hide(key)
