"""``DrawState``: the Python side of a ``Draw`` widget's drawing (spec §5.2).

The ``draw`` trait carries an envelope ``{fc, gen, seq, origin}`` both ways.
``DrawState`` is its only Python writer, and it owns the drawing:

- ``set_features`` keeps a private copy and sends it as a *host* write: ``gen``
  goes up by one, ``seq`` back to 0. The frontend replaces its drawing and
  adopts that ``gen``.
- The frontend sends each finished edit as a *user* write with the ``gen`` it
  adopted and ``seq`` one higher than its previous write. It is accepted only
  when ``gen`` is the current one and ``seq`` is above the last accepted;
  ``on_edit`` callbacks then run. Anything else is stale -- an edit made before
  the last host write reached the browser, or a replay -- and Python re-asserts
  its own copy as a new host write, so the browser converges on it.
- A rejected write has already landed in the trait (ipywidgets sets it before
  any observer runs), so ``features()`` and ``geojson()`` read the private
  copy, never the trait.

Thread-safe: one re-entrant lock around every change of the copy, and the
trait write that follows it, so host writes reach the trait in ``gen`` order.
Callbacks run outside the lock.
"""

import copy
import logging
import threading

log = logging.getLogger(__name__)


def _collection(fc_or_features) -> dict:
    """A deep-copied FeatureCollection from a FeatureCollection, a Feature, a
    list of features or None."""
    if fc_or_features is None:
        return {"type": "FeatureCollection", "features": []}
    if isinstance(fc_or_features, (list, tuple)):
        return {"type": "FeatureCollection", "features": copy.deepcopy(list(fc_or_features))}
    if isinstance(fc_or_features, dict) and fc_or_features.get("type") == "Feature":
        return {"type": "FeatureCollection", "features": [copy.deepcopy(fc_or_features)]}
    if isinstance(fc_or_features, dict) and fc_or_features.get("type") == "FeatureCollection":
        fc = copy.deepcopy(fc_or_features)
        fc["features"] = list(fc.get("features") or [])
        return fc
    raise TypeError(f"expected a FeatureCollection, a Feature, a list of features or None, got {fc_or_features!r}")


def _count(value) -> int:
    """``gen``/``seq`` from an envelope; anything that is not a whole number is 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return 0
    return int(value)


class DrawState:
    """The drawing of ``widget`` (a ``MultiView`` with the ``Draw`` mixin)."""

    def __init__(self, widget):
        if not widget.has_trait("draw") or not widget.has_trait("draw_edit_mode"):
            raise TypeError("DrawState needs a widget with the Draw mixin")
        self._widget = widget
        self._lock = threading.RLock()
        self._callbacks = []
        envelope = widget.draw or {}
        self._fc = _collection(envelope.get("fc"))
        self._gen = _count(envelope.get("gen"))
        self._seq = _count(envelope.get("seq"))
        widget.observe(self._on_draw, names="draw")

    # --- Python -> browser ---------------------------------------------------

    def set_features(self, fc_or_features) -> None:
        """Replace the drawing in every view. Never fires ``on_edit``."""
        fc = _collection(fc_or_features)
        with self._lock:
            self._fc = fc
            self._gen += 1
            self._seq = 0
            self._write_host()

    def set_edit_mode(self, on) -> None:
        self._widget.draw_edit_mode = bool(on)

    def _write_host(self) -> None:
        # Always a new dict: an in-place change of the trait would not sync.
        self._widget.draw = {"fc": copy.deepcopy(self._fc), "gen": self._gen, "seq": 0, "origin": "host"}

    # --- browser -> Python ---------------------------------------------------

    def _on_draw(self, change) -> None:
        envelope = change["new"]
        if not isinstance(envelope, dict) or envelope.get("origin") != "user":
            return  # our own host write
        with self._lock:
            seq = envelope.get("seq")
            current = (
                envelope.get("gen") == self._gen
                and isinstance(seq, int)
                and not isinstance(seq, bool)
                and seq > self._seq
            )
            if current:
                try:
                    self._fc = _collection(envelope.get("fc"))
                except TypeError:
                    current = False
                else:
                    self._seq = seq
            if not current:
                log.debug("stale drawing write %r (gen %s, seq %s): re-asserting", envelope.get("gen"), self._gen, self._seq)
                self._gen += 1
                self._seq = 0
                self._write_host()
                return
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                log.exception("a drawing on_edit callback failed")

    # --- reads -----------------------------------------------------------------

    def features(self) -> list:
        """A copy of the drawing's features."""
        with self._lock:
            return copy.deepcopy(self._fc["features"])

    def geojson(self) -> dict:
        """A copy of the drawing as a FeatureCollection."""
        with self._lock:
            return copy.deepcopy(self._fc)

    def on_edit(self, callback):
        """Call ``callback()`` after each accepted user edit. Returns an unsubscribe."""
        with self._lock:
            self._callbacks.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return unsubscribe
