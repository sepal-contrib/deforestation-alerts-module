// Keeps N Leaflet maps on one view, in the browser only.
//
// - Leader: the map that fires "move"/"zoom" while nothing else runs. Every
//   other map gets setView(..., {animate: false}) at once, so the followers
//   track a drag frame by frame. Leaflet fires the followers' own events
//   synchronously inside setView; ``busy`` drops them, so nothing echoes back.
//   An animated zoom fires no "move"/"zoom" until its transition ends: on its
//   "zoomanim" the followers start the same animation, and until each one's
//   own "moveend" its events are ``following``, not gestures.
// - Canonical view: the last setCanonical(), or the leader's view when a user
//   gesture ends (its "moveend"). Nothing else writes it.
// - restore(): after a size change every map must re-measure
//   (invalidateSize), which moves its centre and fires "move"/"moveend"
//   synchronously. While ``restoring`` is set those moves are not gestures:
//   no sync, no canonical write, no onGestureEnd. The canonical view is then
//   put back on every map that has a size.
// No Leaflet internals are patched.

const frame = fn =>
  typeof globalThis.requestAnimationFrame === "function" ? globalThis.requestAnimationFrame(fn) : setTimeout(fn, 16);

const cancelFrame = id => {
  if (id === null) return;
  if (typeof globalThis.cancelAnimationFrame === "function") globalThis.cancelAnimationFrame(id);
  else clearTimeout(id);
};

const toCenter = c => (Array.isArray(c) ? [Number(c[0]), Number(c[1])] : [Number(c.lat), Number(c.lng)]);

const viewOf = map => ({center: toCenter(map.getCenter()), zoom: map.getZoom()});

const hasSize = map => {
  const el = map.getContainer();
  return el.clientWidth > 0 && el.clientHeight > 0;
};

const copy = view => (view ? {center: [...view.center], zoom: view.zoom} : null);

export function createSync(maps, {onGestureEnd = () => {}} = {}) {
  let canonical = null;
  let busy = false; // a follower update or setCanonical is running
  let restoring = false; // a resize-driven re-measure is running (until the next frame)
  let pending = null; // the frame that clears ``restoring``
  const following = new Set(); // maps running a zoom animation another map started

  const followFrom = leader => {
    busy = true;
    try {
      const center = leader.getCenter();
      const zoom = leader.getZoom();
      for (const map of maps) if (map !== leader) map.setView(center, zoom, {animate: false});
    } finally {
      busy = false;
    }
  };

  const unlinks = maps.map(leader => {
    const onMove = () => {
      if (busy || restoring || following.has(leader)) return;
      followFrom(leader);
    };
    // An animated zoom holds the leader's "move"/"zoom" until its CSS
    // transition ends (~250 ms); "zoomanim" carries the target at the start.
    // The followers run the same animation: a zoom with {animate: false}
    // resets the view, which drops every tile (a blank flash) before redrawing.
    const onZoomAnim = e => {
      if (busy || restoring || following.has(leader)) return;
      for (const map of maps) {
        if (map === leader) continue;
        following.add(map);
        // When it cannot animate, Leaflet resets at once: its moveend clears it.
        map.setView(e.center, e.zoom, {animate: true});
      }
    };
    const onMoveEnd = () => {
      // ``restoring`` is checked before anything else: a resize-driven moveend
      // must never reach onGestureEnd (and so never schedules a write-back).
      if (busy || restoring) return;
      if (following.delete(leader)) return; // the end of an animation another map started
      followFrom(leader); // a moveend without a last "move" still leaves the followers in place
      canonical = viewOf(leader);
      onGestureEnd(copy(canonical));
    };
    leader.on("move zoom", onMove);
    leader.on("zoomanim", onZoomAnim);
    leader.on("moveend", onMoveEnd);
    return () => {
      leader.off("move zoom", onMove);
      leader.off("zoomanim", onZoomAnim);
      leader.off("moveend", onMoveEnd);
    };
  });

  function setCanonical({center, zoom}) {
    canonical = {center: toCenter(center), zoom: Number(zoom)};
    busy = true;
    try {
      for (const map of maps) map.setView(canonical.center, canonical.zoom, {animate: false});
    } finally {
      busy = false;
    }
  }

  function restore() {
    restoring = true;
    following.clear(); // the canonical view goes back on every map anyway
    try {
      for (const map of maps) {
        if (!hasSize(map)) continue; // a hidden map keeps its last measured size
        map.invalidateSize({pan: false});
        if (canonical) map.setView(canonical.center, canonical.zoom, {animate: false});
      }
    } finally {
      cancelFrame(pending);
      pending = frame(() => {
        pending = null;
        restoring = false;
      });
    }
  }

  function destroy() {
    unlinks.forEach(unlink => unlink());
    cancelFrame(pending);
    pending = null;
    restoring = false;
    following.clear();
  }

  return {
    setCanonical,
    canonical: () => copy(canonical),
    restore,
    isRestoring: () => restoring,
    destroy,
  };
}

export function debounce(fn, ms) {
  let timer = null;
  const debounced = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn(...args);
    }, ms);
  };
  debounced.cancel = () => {
    clearTimeout(timer);
    timer = null;
  };
  return debounced;
}
