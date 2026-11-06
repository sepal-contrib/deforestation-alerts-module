// One view's Leaflet layers, built from the layer specs that target it.
//
// - Tile layers stack by zIndex (= spec.z) and are upscaled above their native
//   zoom (maxNativeZoom 20 by default), so imagery is never blank when zoomed in.
//   A tile spec with ``queued: true`` loads through the page's tile queue
//   (``queued_tiles.js``): paced, and retried when the server refuses.
// - GeoJSON layers are overlays: pmIgnore (Geoman never edits them) and not
//   interactive (they never swallow the clicks the drawing relies on). Each
//   feature's properties.style is merged over the spec's style. They share the
//   overlay pane's SVG, so z order is applied with bringToFront, lowest first.
import {byZ, diffLayers, layerKey} from "./layers.js";
import {queuedTileLayer} from "./queued_tiles.js";

export function createLayerView(L, map, viewId) {
  const entries = new Map(); // id -> {spec, key, layer}

  function build(spec) {
    if (spec.kind === "tile") {
      const options = {
        opacity: spec.opacity ?? 1,
        attribution: spec.attribution || "",
        zIndex: spec.z ?? 0,
        maxZoom: spec.maxZoom ?? 24,
        maxNativeZoom: spec.maxNativeZoom ?? 20,
      };
      return spec.queued ? queuedTileLayer(L, spec.url, options) : L.tileLayer(spec.url, options);
    }
    if (spec.kind === "geojson") {
      const base = spec.style || {};
      return L.geoJSON(spec.data, {
        pmIgnore: true,
        interactive: false,
        style: f => ({...base, ...((f && f.properties && f.properties.style) || {})}),
        // Points would become L.Marker, which ignores the options above.
        pointToLayer: (feature, latlng) => L.circleMarker(latlng, {pmIgnore: true, interactive: false, radius: 6}),
      });
    }
    console.error(`leaflet-multiview: layer "${spec.id}" in view "${viewId}" has an unknown kind "${spec.kind}"`);
    return null;
  }

  function drop(id) {
    const entry = entries.get(id);
    if (!entry) return;
    if (map.hasLayer(entry.layer)) map.removeLayer(entry.layer);
    entries.delete(id);
  }

  function show(layer, on) {
    const has = map.hasLayer(layer);
    if (on && !has) map.addLayer(layer);
    else if (!on && has) map.removeLayer(layer);
  }

  function refresh(visibleFn) {
    for (const {spec, layer} of entries.values()) show(layer, visibleFn(spec));
    [...entries.values()]
      .filter(e => e.spec.kind === "geojson")
      .sort((a, b) => byZ(a.spec, b.spec))
      .forEach(e => {
        if (map.hasLayer(e.layer)) e.layer.bringToFront();
      });
  }

  // ``specs``: only the specs that target this view. Returns what changed.
  function sync(specs, visibleFn) {
    const current = new Map([...entries].map(([id, e]) => [id, e.key]));
    const {add, replace, remove, keep} = diffLayers(current, specs);
    for (const id of remove) drop(id);
    for (const spec of replace) drop(spec.id);
    for (const spec of [...add, ...replace]) {
      const layer = build(spec);
      if (layer) entries.set(spec.id, {spec, key: layerKey(spec), layer});
    }
    for (const spec of keep) entries.get(spec.id).spec = spec;
    refresh(visibleFn);
    return {added: add.map(s => s.id), replaced: replace.map(s => s.id), removed: remove};
  }

  function destroy() {
    for (const id of [...entries.keys()]) drop(id);
  }

  return {
    sync,
    refresh,
    layer: id => (entries.has(id) ? entries.get(id).layer : undefined),
    entries: () => entries,
    destroy,
  };
}
