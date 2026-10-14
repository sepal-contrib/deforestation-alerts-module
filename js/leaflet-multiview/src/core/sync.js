// Keeps N Leaflet maps on one view, in the browser only.
//
// - Leader: the map that fires "move"/"zoom" while nothing else runs. Every
//   other map gets setView(..., {animate: false}) at once, so the followers
//   track a drag frame by frame. Leaflet fires the followers' own events
//   synchronously inside setView; ``busy`` drops them, so nothing echoes back.
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
      if (busy || restoring) return;
      followFrom(leader);
    };
    const onMoveEnd = () => {
      // ``restoring`` is checked before anything else: a resize-driven moveend
      // must never reach onGestureEnd (and so never schedules a write-back).
      if (busy || restoring) return;
      followFrom(leader); // a moveend without a last "move" still leaves the followers in place
      canonical = viewOf(leader);
      onGestureEnd(copy(canonical));
    };
    leader.on("move zoom", onMove);
    leader.on("moveend", onMoveEnd);
    return () => {
      leader.off("move zoom", onMove);
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
