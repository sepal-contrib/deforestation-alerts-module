// The tileErrors plugin (spec §4.4): a per-view badge, "Imagery failed to
// load", while a tile layer whose spec says meta.reportErrors === true has
// failed to load a tile in that view.
//
// - Only those layers are watched. The basemap (not a layer spec) and every
//   other layer never raise the badge.
// - A failure is forgotten when its layer is replaced (any change to its spec
//   but `visible`) or removed; the new layer starts clean.
// - "tileerror" {view, id} is emitted once per failing layer per view, the
//   first time one of its tiles fails, not once per tile.
// - Leaflet fires "tileerror" for tiles that were loading when their layer
//   left the map, so an error from a layer that is no longer the view's layer
//   for that id is ignored.
//
// It reads the core's layers with layerEntries() after each "layerschange"
// (and once at install, for the layers that came before the plugin) and
// changes nothing but its own badges.

export const tileErrors = {
  name: "tileErrors",
  requires: [],
  defaults: {},
  labels: {badge: "Imagery failed to load"},

  install(mv) {
    const L = mv.L;
    const failed = new Map(mv.views.map(v => [v.id, new Set()])); // view id -> ids that failed
    const badges = new Map(); // view id -> its badge element
    const watched = new Map(); // L.Layer -> {viewId, id, off}
    let destroyed = false;

    const removeBadges = mv.addControl("all", v => {
      const control = L.control({position: "bottomright"});
      control.onAdd = () => {
        const el = L.DomUtil.create("div", "mv-tile-error");
        el.setAttribute("role", "status");
        el.textContent = mv.label("tileErrors", "badge");
        el.style.display = "none";
        badges.set(v.id, el);
        return el;
      };
      return control;
    });

    function render() {
      for (const [viewId, el] of badges) el.style.display = failed.get(viewId).size ? "" : "none";
    }

    function relabel() {
      const text = mv.label("tileErrors", "badge");
      for (const el of badges.values()) el.textContent = text;
    }

    const reports = spec => spec.kind === "tile" && !!spec.meta && spec.meta.reportErrors === true;

    function watch(viewId, id, layer) {
      if (watched.has(layer)) return;
      const onError = () => {
        if (destroyed || mv.layer(viewId, id) !== layer) return; // a layer that has gone
        const ids = failed.get(viewId);
        if (ids.has(id)) return; // reported already
        ids.add(id);
        render();
        mv.emit("tileerror", {view: viewId, id});
      };
      layer.on("tileerror", onError);
      watched.set(layer, {viewId, id, off: () => layer.off("tileerror", onError)});
    }

    function sync(diff) {
      for (const v of mv.views) {
        const changed = diff && diff[v.id];
        if (changed) for (const id of [...changed.replaced, ...changed.removed]) failed.get(v.id).delete(id);
        for (const [id, entry] of mv.layerEntries(v.id)) {
          if (reports(entry.spec)) watch(v.id, id, entry.layer);
        }
      }
      for (const [layer, w] of [...watched]) {
        if (mv.layer(w.viewId, w.id) === layer) continue;
        w.off();
        watched.delete(layer);
      }
      render();
    }

    sync(null);
    const offLayers = mv.on("layerschange", ({diff}) => sync(diff));
    const offLabels = mv.on("labelschange", relabel);

    return {
      api: {
        errors: () => Object.fromEntries(mv.views.map(v => [v.id, [...failed.get(v.id)]])),
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        offLayers();
        offLabels();
        for (const w of watched.values()) w.off();
        watched.clear();
        removeBadges();
      },
    };
  },
};
