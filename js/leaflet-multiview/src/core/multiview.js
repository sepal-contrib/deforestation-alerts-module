// createMultiView(el, options): N named Leaflet views in a row or a grid, kept
// on one canonical view (sync.js), fed by declarative layer specs
// (layers.js, layer_view.js), extended by plugins (registry.js).
//
// Leaflet is injected: options.L, else the page's global L. Nothing in this
// package imports it, and everything below uses only ``L`` from here.
import {createEmitter} from "./events.js";
import {buildLayout} from "./layout.js";
import {createLayerView} from "./layer_view.js";
import {isVisible, pruneOverrides, viewsOf} from "./layers.js";
import {createRegistry} from "./registry.js";
import {createSync, debounce} from "./sync.js";

const LAYOUTS = ["row", "grid"];
const DEFAULT_VIEW = {center: [0, 0], zoom: 2};
const BASEMAP_PANE = "mvBasemap"; // under Leaflet's tile pane: overlays always draw on top
const VIEWCHANGE_MS = 300;

const hasSize = node => node.clientWidth > 0 && node.clientHeight > 0;
const toCenter = c => (Array.isArray(c) ? [Number(c[0]), Number(c[1])] : [Number(c.lat), Number(c.lng)]);
const nextFrame = fn => (typeof requestAnimationFrame === "function" ? requestAnimationFrame(fn) : setTimeout(fn, 16));

function checkViews(views) {
  if (!Array.isArray(views) || views.length === 0) throw new Error("leaflet-multiview needs at least one view");
  const seen = new Set();
  for (const view of views) {
    if (!view || typeof view.id !== "string" || view.id === "") {
      throw new Error("leaflet-multiview: every view needs a non-empty string id");
    }
    if (view.id === "all") throw new Error('leaflet-multiview: "all" is reserved and cannot be a view id');
    if (seen.has(view.id)) throw new Error(`leaflet-multiview: duplicate view id "${view.id}"`);
    seen.add(view.id);
  }
}

export function createMultiView(el, options = {}) {
  const L = options.L ?? globalThis.L;
  if (!L || typeof L.map !== "function") {
    throw new Error("leaflet-multiview needs Leaflet: pass options.L or load it globally");
  }
  const {views: viewDefs, layout = "row", basemap = null, view = DEFAULT_VIEW, theme = "light"} = options;
  checkViews(viewDefs);
  if (!LAYOUTS.includes(layout)) throw new Error(`leaflet-multiview: unknown layout "${layout}" (use "row" or "grid")`);

  const emitter = createEmitter();
  const viewIds = viewDefs.map(v => v.id);
  const start = {
    center: toCenter((view && view.center) || DEFAULT_VIEW.center),
    zoom: Number(view && view.zoom != null ? view.zoom : DEFAULT_VIEW.zoom),
  };
  const cells = buildLayout(el, viewDefs, layout, el.ownerDocument || document);
  const views = cells.map(({id, container, header, mapEl}) => {
    // trackResize: false -- the ResizeObserver below owns every re-measure, so
    // Leaflet never moves a map by itself (its own resize handler fires a
    // late, debounced moveend that would look like a user gesture).
    const map = L.map(mapEl, {center: start.center, zoom: start.zoom, maxZoom: 24, trackResize: false});
    map.createPane(BASEMAP_PANE).style.zIndex = "150";
    return {id, map, container, header};
  });
  const layerViews = new Map(views.map(v => [v.id, createLayerView(L, v.map, v.id)]));

  let destroyed = false;
  let labels = {...(options.labels || {})};
  let specs = [];
  let overrides = {};
  const rules = [];
  let basemapLayers = [];
  let pendingFit = null; // bounds asked for while nothing was shown

  // --- view ---------------------------------------------------------------
  const viewchange = debounce(v => emitter.emit("viewchange", v), VIEWCHANGE_MS);
  const sync = createSync(
    views.map(v => v.map),
    {onGestureEnd: v => {
      if (!sync.isRestoring()) viewchange(v);
    }},
  );
  sync.setCanonical(start);

  function setView(center, zoom) {
    viewchange.cancel();
    pendingFit = null;
    sync.setCanonical({center: toCenter(center), zoom: zoom == null ? sync.canonical().zoom : Number(zoom)});
  }

  // A fit is a user move: the first shown view fits, the others follow, and
  // its moveend writes the canonical view and schedules "viewchange".
  function fitNow(bounds) {
    if (destroyed) return;
    if (sync.isRestoring()) {
      nextFrame(() => fitNow(bounds)); // runs after the frame that clears ``restoring``
      return;
    }
    const leader = views.find(v => hasSize(v.map.getContainer()));
    if (!leader) {
      pendingFit = bounds;
      return;
    }
    leader.map.fitBounds(bounds, {animate: false});
  }

  function fitBounds(bounds) {
    const b = L.latLngBounds(bounds);
    if (!b.isValid()) return;
    pendingFit = null;
    fitNow(b);
  }

  // --- size: one observer for the root and every map ----------------------
  let shown = hasSize(el);
  const observer = new ResizeObserver(() => {
    if (destroyed) return;
    sync.restore();
    const now = hasSize(el);
    if (now !== shown) {
      shown = now;
      emitter.emit("visibilitychange", {shown: now});
    }
    if (now && pendingFit) {
      const bounds = pendingFit;
      pendingFit = null;
      fitNow(bounds);
    }
  });
  observer.observe(el);
  for (const v of views) observer.observe(v.map.getContainer());

  // --- basemap ------------------------------------------------------------
  // basemap.maxZoom is the provider's native maximum; above it tiles are upscaled.
  function setBasemap(next) {
    for (const [map, layer] of basemapLayers) map.removeLayer(layer);
    basemapLayers = [];
    if (!next || !next.url) return;
    for (const v of views) {
      const layer = L.tileLayer(next.url, {
        pane: BASEMAP_PANE,
        attribution: next.attribution || "",
        maxZoom: 24,
        maxNativeZoom: next.maxZoom ?? 20,
      });
      v.map.addLayer(layer);
      basemapLayers.push([v.map, layer]);
    }
  }
  setBasemap(basemap);

  // --- layers -------------------------------------------------------------
  const visibleIn = viewId => spec => isVisible(spec, viewId, {rules, overrides});
  const specsFor = viewId => specs.filter(s => viewsOf(s, viewIds).includes(viewId));

  function setLayers(next) {
    specs = Array.isArray(next) ? next.filter(s => s && s.id != null) : [];
    overrides = pruneOverrides(overrides, specs, viewIds); // the same object when nothing was dropped
    const diff = {};
    for (const v of views) diff[v.id] = layerViews.get(v.id).sync(specsFor(v.id), visibleIn(v.id));
    emitter.emit("layerschange", {layers: specs.slice(), diff});
  }

  function refreshVisibility() {
    for (const v of views) layerViews.get(v.id).refresh(visibleIn(v.id));
  }

  function setLayerOverride(viewId, layerId, value) {
    if (!viewIds.includes(viewId)) return;
    const current = overrides[viewId] || {};
    let nextForView;
    if (value === null || value === undefined) {
      if (!(layerId in current)) return;
      nextForView = {...current};
      delete nextForView[layerId];
    } else {
      if (current[layerId] === Boolean(value)) return;
      nextForView = {...current, [layerId]: Boolean(value)};
    }
    overrides = {...overrides, [viewId]: nextForView}; // a new object: callers compare by identity
    refreshVisibility();
  }

  function addVisibilityRule(fn) {
    const rule = (spec, viewId) => {
      try {
        return fn(spec, viewId);
      } catch (err) {
        console.error("leaflet-multiview: a visibility rule failed", err);
        return undefined;
      }
    };
    rules.push(rule);
    refreshVisibility();
    return () => {
      const i = rules.indexOf(rule);
      if (i < 0) return;
      rules.splice(i, 1);
      if (!destroyed) refreshVisibility();
    };
  }

  // --- controls, labels, theme ---------------------------------------------
  function addControl(target, makeControl) {
    const ids = viewsOf({view: target}, viewIds);
    const added = [];
    for (const v of views) {
      if (!ids.includes(v.id)) continue;
      const control = makeControl(v);
      if (control) {
        control.addTo(v.map);
        added.push([v.map, control]);
      }
    }
    let removed = false;
    return () => {
      if (removed) return;
      removed = true;
      for (const [map, control] of added) map.removeControl(control);
    };
  }

  function setLabels(next) {
    labels = {...(next || {})};
    emitter.emit("labelschange", {labels: {...labels}});
  }

  function setTheme(next) {
    el.classList.toggle("mv-dark", next === "dark");
    emitter.emit("themechange", {theme: next});
  }
  el.classList.toggle("mv-dark", theme === "dark");

  // --- teardown -------------------------------------------------------------
  function destroy() {
    if (destroyed) return;
    destroyed = true;
    registry.destroyAll();
    viewchange.cancel();
    observer.disconnect();
    sync.destroy();
    for (const layerView of layerViews.values()) layerView.destroy();
    for (const v of views) v.map.remove();
    const body = cells[0].container.parentNode;
    if (body && body.parentNode === el) el.removeChild(body);
    el.classList.remove("mv-root", `mv-layout-${layout}`, "mv-dark");
    if (el._multiview === mv) delete el._multiview;
  }

  const mv = {
    L,
    views,
    setLayers,
    layers: () => specs.slice(),
    layer: (viewId, layerId) => (layerViews.has(viewId) ? layerViews.get(viewId).layer(layerId) : undefined),
    layerEntries: viewId => (layerViews.has(viewId) ? layerViews.get(viewId).entries() : new Map()),
    setView,
    getView: () => sync.canonical(),
    fitBounds,
    setBasemap,
    setLabels,
    label: (pluginName, key) => registry.label(pluginName, key, labels),
    setTheme,
    on: emitter.on,
    off: emitter.off,
    emit: emitter.emit,
    addControl,
    addVisibilityRule,
    refreshVisibility,
    setLayerOverride,
    overrides: () => overrides,
    use: (plugin, pluginOptions) => registry.use(plugin, pluginOptions),
    plugin: name => registry.get(name),
    destroy,
  };
  const registry = createRegistry(mv);
  el._multiview = mv; // debug handle for tests and walks
  return mv;
}
