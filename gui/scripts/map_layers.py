"""One registry of toggled map layers per session (spec §4.3).

A layer switched on in any step stays on until switched off or the project
changes (``clear``). Builders are blocking (Earth Engine map ids, tile
servers), so each one runs on a worker thread, at most one build per key at a
time; ``inflight`` publishes the keys being built. ``refresh`` rebuilds a
shown layer when its artifact's content id changed (spec §9.1: never show a
stale tile cache after a file is replaced).

The latest request on a key wins, even while the key is still building:

- ``show`` (so ``refresh`` too), ``hide`` and ``clear`` bump the key's
  generation. A build compares the generation it started with once its builder
  returns; a build that was superseded removes the layer it drew and changes
  nothing else. So a key switched off during its build (a panel exit, a project
  switch) stays off, and an older build never outlives a newer request.
- A ``show`` (or a ``refresh`` that needs one) arriving while the key is still
  building is kept as the key's pending request and starts once that build has
  cleaned up. Leaving Overview and coming back mid-build, a project switch that
  re-shows the same key, or a refresh during a build all end with the new
  layer drawn. A ``hide`` drops the pending request.
- ``hide`` removes the layer right away, even mid-build. A request arriving
  while that removal is still running waits for it too, so the removal can
  never take away the newer layer.
- ``toggle`` is the exception (ruling #10): a click on a key that is building
  is a no-op, so a second click never hides a layer that is about to land.

A build that fails turns its key off and removes the key's layer, so the map
never keeps a layer the registry says is off (for a first ``show`` the removal
is a no-op: the map is left untouched, spec §12). Only a build that was still
wanted reports through its notifier.

Locking: every piece of registry state (the keys switched on, the keys being
built, the pending requests, the generations, the removals in progress and the
requested content ids) is a plain Python object changed together under
``_lock``, and nothing else ever runs under it: no reactive ``set``, no map
call, no builder. ``on`` and ``inflight`` are published after the lock is
released, re-publishing until no other thread changed the registry meanwhile,
so the values they are left with are always the latest. A reactive ``set``
re-renders its subscribers synchronously in the setting thread (taking
reacton's render lock), and effects, which already hold that render lock, call
``show``/``hide``/``clear``; because no ``set`` runs under a lock MapLayers
holds, a subscriber or an effect may call back into the registry from any
thread.

That is why MapLayers keeps its own in-flight set instead of using
``gui.scripts.inflight.InflightKeys``, whose ``claim``/``release`` publish
while holding its own lock: a synchronous subscriber of an instance that claims
or releases that same instance hangs on that lock, and an effect that claims it
can deadlock against a worker whose ``release`` is re-rendering the effect's
context. Code that owns a bare ``InflightKeys`` (the download and integrate
steps) must never claim or release it from a render or an effect of a context
that subscribes to that same instance.
"""

import logging
import threading

import solara

from gui.scripts.solara_threads import spawn_in_context

logger = logging.getLogger("SEPALUI.deforestation_alerts")


class MapLayers:
    def __init__(self, map_):
        self.map_ = map_
        #: keys switched on (the published copy of ``_keys``)
        self.on = solara.reactive(frozenset())
        #: keys whose builder is running (the published copy of ``_building``)
        self.inflight = solara.reactive(frozenset())
        self._lock = threading.Lock()
        # Everything below changes only under ``_lock``.
        self._keys = frozenset()  # the registry itself
        self._building = frozenset()
        self._pending = {}  # key -> (build, content_id, notifier) waiting for the key
        self._removing = {}  # key -> number of ``hide`` removals still running
        self._generation = {}  # key -> bumped by every show/hide
        self._content = {}  # shown key -> content id of its latest request
        self._version = 0  # bumped by every change, for ``_publish``

    def is_on(self, key) -> bool:
        return key in self.on.value

    # ---- requests -----------------------------------------------------------
    def show(self, key, build, content_id=None, notifier=None) -> None:
        with self._lock:
            start = self._request(key, build, content_id, notifier)
        self._publish()
        self._start(start)

    def hide(self, key) -> None:
        self._hide(frozenset([key]))

    def toggle(self, key, build, content_id=None, notifier=None) -> None:
        with self._lock:
            if key in self._building:
                return  # still building: a second click must not hide a layer about to land
            on = key in self._keys
        if on:
            self.hide(key)
        else:
            self.show(key, build, content_id, notifier)

    def refresh(self, key, build, content_id) -> None:
        # One lock section for the check and the request: a hide in between
        # must not be undone by this refresh.
        with self._lock:
            if key not in self._keys or self._content.get(key) == content_id:
                return
            start = self._request(key, build, content_id, None)
        self._publish()
        self._start(start)

    def clear(self) -> None:
        self._hide(None)

    # ---- internals ----------------------------------------------------------
    def _request(self, key, build, content_id, notifier):
        """Switch ``key`` on for a new build (``_lock`` held). Returns the
        build to start now, or None when it has to wait for the key."""
        self._generation[key] = self._generation.get(key, 0) + 1
        self._keys = self._keys | {key}
        self._content[key] = content_id
        self._version += 1
        if key in self._building or key in self._removing:
            self._pending[key] = (build, content_id, notifier)  # the latest one wins
            return None
        self._pending.pop(key, None)
        self._building = self._building | {key}
        return (key, self._generation[key], build, content_id, notifier)

    def _next(self, key):
        """Start the key's pending request if the key is free (``_lock`` held)."""
        if key in self._building or key in self._removing or key not in self._pending:
            return None
        build, content_id, notifier = self._pending.pop(key)
        self._building = self._building | {key}
        self._version += 1
        return (key, self._generation[key], build, content_id, notifier)

    def _start(self, start) -> None:
        if start is not None:
            spawn_in_context(self._build, start)

    def _hide(self, keys) -> None:
        with self._lock:
            keys = self._keys if keys is None else keys  # None: every key (``clear``)
            for key in keys:
                self._generation[key] = self._generation.get(key, 0) + 1
                self._pending.pop(key, None)
                self._content.pop(key, None)
                self._removing[key] = self._removing.get(key, 0) + 1
            self._keys = self._keys - keys
            self._version += 1
        try:
            for key in keys:
                self.map_.remove_layer(key, none_ok=True)
        finally:
            starts = []
            with self._lock:
                for key in keys:
                    left = self._removing.pop(key) - 1
                    if left:
                        self._removing[key] = left
                    starts.append(self._next(key))  # a show that arrived meanwhile
            self._publish()
            for start in starts:
                self._start(start)

    def _build(self, key, generation, build, content_id, notifier):
        try:
            try:
                build()
                error = None
            except Exception as exc:  # noqa: BLE001 - reported as a toast
                logger.exception("map layer %s failed", key)
                error = exc
            with self._lock:
                wanted = self._generation.get(key) == generation
                if wanted and error is not None:
                    self._keys = self._keys - {key}
                    self._content.pop(key, None)
                    self._version += 1
            if error is not None or not wanted:
                # Failed, or superseded by hide/clear/a newer show: this build's
                # layer must not stay on the map. The key is still ours, so no
                # newer build can have drawn under it yet.
                self.map_.remove_layer(key, none_ok=True)
            if error is not None and wanted and notifier is not None:
                notifier.error(str(error) or error.__class__.__name__)
        finally:
            with self._lock:
                self._building = self._building - {key}
                self._version += 1
                start = self._next(key)
            self._publish()
            self._start(start)

    def _publish(self) -> None:
        # Outside the lock (see the module docstring). Two threads can publish
        # out of order, so re-publish until nobody changed the registry
        # meanwhile: the values left in ``on`` and ``inflight`` are the latest.
        while True:
            with self._lock:
                version, keys, building = self._version, self._keys, self._building
            self.on.set(keys)
            self.inflight.set(building)
            if self._version == version:
                return
