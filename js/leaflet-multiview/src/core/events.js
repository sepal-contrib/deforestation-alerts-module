// A tiny event emitter for the multiview and its plugins.
//
// A listener that throws is logged with console.error; the other listeners
// still run. A listener removed by an earlier one during the same emit is not
// called (the DOM's rule).

export function createEmitter() {
  const listeners = new Map(); // event -> Set<fn>

  function off(event, fn) {
    const set = listeners.get(event);
    if (!set) return;
    set.delete(fn);
    if (set.size === 0) listeners.delete(event);
  }

  function on(event, fn) {
    if (typeof fn !== "function") {
      throw new TypeError(`leaflet-multiview: a "${event}" listener must be a function`);
    }
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => off(event, fn);
  }

  function emit(event, payload) {
    const set = listeners.get(event);
    if (!set) return;
    for (const fn of [...set]) {
      if (!set.has(fn)) continue; // removed by an earlier listener
      try {
        fn(payload);
      } catch (err) {
        console.error(`leaflet-multiview: a "${event}" listener failed`, err);
      }
    }
  }

  return {on, off, emit};
}
