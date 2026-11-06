// js/leaflet-multiview/src/core/queued_tiles.js
// A tile layer whose tiles load through the page's tile queue
// (``tile_queue.js``): a tile spec with ``queued: true``. The queue paces
// the requests and retries the ones the server refused, so a rate-limited
// server fills the view in a few seconds instead of leaving holes; the layer
// reports "tileerror" only for a tile that failed every try.
//
// The tiles are fetched (the server must allow CORS) and shown from a blob
// URL, revoked once the image has decoded. A tile Leaflet drops (panned out,
// zoomed past, its layer removed) cancels its request.
import {createTileQueue} from "./tile_queue.js";

let shared = null;

// One queue for every view and layer of the page: they share the server's quota.
export function sharedTileQueue() {
  if (!shared) shared = createTileQueue();
  return shared;
}

export function queuedTileLayer(L, url, options, queue = sharedTileQueue()) {
  const QueuedTileLayer = L.TileLayer.extend({
    createTile(coords, done) {
      const tile = document.createElement("img");
      tile.alt = "";
      tile.setAttribute("role", "presentation");
      L.DomEvent.on(tile, "load", () => {
        URL.revokeObjectURL(tile.src);
        this._tileOnLoad(done, tile);
      });
      L.DomEvent.on(tile, "error", e => {
        URL.revokeObjectURL(tile.src);
        this._tileOnError(done, tile, e);
      });
      tile._mvJob = queue.fetch(
        this.getTileUrl(coords),
        blob => { tile.src = URL.createObjectURL(blob); },
        error => done(error, tile),
      );
      return tile;
    },
  });
  const layer = new QueuedTileLayer(url, options);
  const cancel = e => {
    if (e.tile && e.tile._mvJob) e.tile._mvJob.cancel();
  };
  layer.on("tileunload tileabort", cancel);
  return layer;
}
