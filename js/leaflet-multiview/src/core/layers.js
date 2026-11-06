// js/leaflet-multiview/src/core/layers.js
// Layer specs against what one view shows. Pure: no Leaflet, no DOM.
//
// A spec targets views through ``spec.view``: one view id, an array of ids,
// or ALL. Each view diffs the specs that target it by ``id``
// (``diffLayers``), and decides per layer whether it is shown
// (``isVisible``): deny-only rules first, then the per-view override (the
// layer menu), then ``spec.visible``.

export const ALL = "all";

// The view ids a spec targets. A spec without ``view`` targets every view.
export function viewsOf(spec, viewIds) {
  const view = spec.view ?? ALL;
  if (view === ALL) return [...viewIds];
  if (Array.isArray(view)) return view.filter(id => viewIds.includes(id));
  return [view];
}

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map(k => [k, sortKeys(value[k])]));
  }
  return value;
}

// What makes a layer a different layer: everything but ``visible``, which
// is applied to the existing Leaflet layer instead of rebuilding it.
export function layerKey(spec) {
  const {visible, ...rest} = spec;
  return JSON.stringify(sortKeys(rest));
}

// ``current`` maps each id the view shows to its ``layerKey``; ``specs`` are
// the specs that target this view (the caller filters with ``viewsOf``).
export function diffLayers(current, specs) {
  const add = [], replace = [], keep = [];
  const wanted = new Set();
  for (const spec of specs) {
    wanted.add(spec.id);
    if (!current.has(spec.id)) add.push(spec);
    else if (current.get(spec.id) !== layerKey(spec)) replace.push(spec);
    else keep.push(spec);
  }
  const remove = [...current.keys()].filter(id => !wanted.has(id));
  return {add, replace, remove, keep};
}

// Rules only deny: ``rule(spec, viewId) === false`` hides the layer whatever
// the override says; any other return value is ignored.
export function isVisible(spec, viewId, {rules = [], overrides = {}} = {}) {
  for (const rule of rules) {
    if (rule(spec, viewId) === false) return false;
  }
  const override = overrides?.[viewId]?.[spec.id];
  if (typeof override === "boolean") return override;
  return spec.visible !== false;
}

// Drops each override ``(view, id)`` whose layer is gone or no longer
// targets that view. Returns the SAME object when nothing was dropped, so a
// caller can tell by identity whether there is anything to write back.
export function pruneOverrides(overrides, specs, viewIds) {
  const targets = new Map();
  for (const spec of specs) {
    for (const view of viewsOf(spec, viewIds)) {
      if (!targets.has(view)) targets.set(view, new Set());
      targets.get(view).add(spec.id);
    }
  }
  let changed = false;
  const out = {};
  for (const [view, byId] of Object.entries(overrides || {})) {
    const ids = targets.get(view);
    out[view] = {};
    for (const [id, on] of Object.entries(byId || {})) {
      if (ids && ids.has(id)) out[view][id] = on;
      else changed = true;
    }
  }
  return changed ? out : overrides;
}

// Draw order within a view: lower ``z`` first, a missing or null ``z`` is 0.
// Array.prototype.sort is stable, so equal ``z`` keeps the input order.
export const byZ = (a, b) => (a.z ?? 0) - (b.z ?? 0);
