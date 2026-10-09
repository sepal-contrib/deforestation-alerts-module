"""``LayerStore``: the one writer of a ``MultiView``'s ``layers`` trait.

The frontend diffs the ``layers`` list by spec id, so Python only has to keep
that list right. Two things make that harder than an assignment:

- **Threads.** A worker adds a layer while the UI thread clears a group. Each
  is a read-modify-write of the list; unlocked, one of them drops the other's
  change. Every write here holds one re-entrant lock and assigns a NEW list
  (an in-place change of the trait would not sync).
- **Gates.** ``add(spec, gate=g)`` calls ``g(write)``, and ``g`` decides
  whether ``write`` runs at all (an app passes "only while this alert is still
  the current one"). The gate is called OUTSIDE the store lock and ``write``
  takes it, so the lock order is always *gate's lock, then store lock*. A UI
  thread that holds the gate's lock and calls ``clear`` takes them in the same
  order, so the two can never deadlock.

A spec whose ``z`` is None gets its group's band from ``z_bands`` (0 for a
group the bands do not name). The store keeps its own deep copy of each spec
and hands out deep copies: no caller can change a stored spec in place,
which would never reach the frontend.

Self-contained: it imports nothing outside this package
(``tests/test_multiview_boundaries.py``).
"""

import copy
import threading


class LayerStore:
    """The layer specs of ``widget`` (a ``MultiView``), kept consistent across threads."""

    def __init__(self, widget, *, z_bands=None):
        self.widget = widget
        self._z_bands = dict(z_bands or {})
        self._lock = threading.RLock()

    def add(self, spec, *, gate=None) -> None:
        """Add ``spec``, or replace the spec with the same id in place.

        With ``gate``, ``gate(write)`` decides whether the write happens; it is
        called outside the store lock.
        """
        spec = copy.deepcopy(dict(spec))
        if spec.get("z") is None:
            spec["z"] = int(self._z_bands.get(spec.get("group"), 0))

        def write():
            with self._lock:
                layers = list(self.widget.layers)
                for i, current in enumerate(layers):
                    if current["id"] == spec["id"]:
                        layers[i] = spec
                        break
                else:
                    layers.append(spec)
                self.widget.layers = layers

        if gate is None:
            write()
        else:
            gate(write)

    def remove(self, layer_id) -> None:
        """Remove the spec ``layer_id``; a missing id is a no-op."""
        with self._lock:
            current = list(self.widget.layers)
            layers = [s for s in current if s["id"] != layer_id]
            if len(layers) != len(current):
                self.widget.layers = layers

    def clear(self, group) -> None:
        """Remove every spec of ``group``, in one write."""
        with self._lock:
            current = list(self.widget.layers)
            layers = [s for s in current if s.get("group") != group]
            if len(layers) != len(current):
                self.widget.layers = layers

    def spec(self, layer_id):
        """A deep copy of the spec ``layer_id``, or None."""
        with self._lock:
            for s in self.widget.layers:
                if s["id"] == layer_id:
                    return copy.deepcopy(s)
        return None

    def ids(self, group=None) -> list:
        """The spec ids in list order, of one group or of all."""
        with self._lock:
            return [s["id"] for s in self.widget.layers if group is None or s.get("group") == group]
