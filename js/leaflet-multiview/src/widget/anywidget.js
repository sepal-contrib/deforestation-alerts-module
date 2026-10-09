// A generic anywidget adapter for leaflet-multiview (spec §5.1).
//
//   export default createWidget({registry, always, L});
//
// - registry: {name: {plugin, bind?, options?}}. The widget's ``plugins`` trait
//   lists the names to install, in order. ``options(model)`` maps the plugin's
//   option traits (read once, at install) and ``bind({model, api, mv})`` its
//   state traits; it returns a cleanup. A name with no entry is skipped with a
//   warning: the normal state while an app registers its plugins task by task.
// - always: plugin objects installed after the listed ones, with no binding
//   (app plugins such as js/app's resizeNudge).
// - L: the Leaflet the maps use. Without it the core takes the page's global L.
//
// Core traits: views, view_layout, basemap, theme, labels, layers, plugins, and
// view = {center, zoom, n, origin}. A host view (Python's set_view) is applied
// when its ``n`` is greater than the last one seen. The viewer's view goes back
// on the core's "viewchange" (already debounced 300 ms) as {center, zoom,
// n: <last seen n>, origin: "user"}; a host view cancels a pending one, because
// the core's setView cancels the pending "viewchange".
//
// anywidget has no re-render API: a change of ``views`` or ``view_layout`` tears the
// multiview down and builds it again in the same element. Every other core
// trait is applied to the live multiview.
import {createMultiView} from "../core/multiview.js";

const message = err => (err && err.message) || String(err);

function toCenter(center) {
  return Array.isArray(center) ? [Number(center[0]), Number(center[1])] : [Number(center.lat), Number(center.lng)];
}

// One build of the multiview into ``el``. Returns its cleanup.
function mount({model, el, registry, always, L}) {
  const doc = el.ownerDocument || document;
  const cleanups = []; // run in reverse order
  const cleanup = () => {
    while (cleanups.length) {
      const fn = cleanups.pop();
      try {
        fn();
      } catch (err) {
        console.error("leaflet-multiview: a widget cleanup failed", err);
      }
    }
  };

  const wrap = doc.createElement("div");
  wrap.className = "mv-widget";
  el.appendChild(wrap);
  cleanups.push(() => wrap.remove());
  const host = doc.createElement("div");
  host.className = "mv-widget-views";
  wrap.appendChild(host);

  let strip = null;
  const fail = (what, err) => {
    console.error(`leaflet-multiview: ${what} failed`, err);
    if (!strip) {
      strip = doc.createElement("div");
      strip.className = "mv-error";
      strip.setAttribute("role", "alert");
      wrap.appendChild(strip);
    }
    const line = `${what}: ${message(err)}`;
    strip.textContent = strip.textContent ? `${strip.textContent} · ${line}` : line;
  };

  const listen = (trait, fn) => {
    const event = `change:${trait}`;
    model.on(event, fn);
    cleanups.push(() => model.off(event, fn));
  };

  const first = model.get("view") || {};
  let lastN = Number(first.n) || 0;
  let mv;
  try {
    mv = createMultiView(host, {
      L,
      views: model.get("views") || [],
      layout: model.get("view_layout") || "row",
      basemap: model.get("basemap") || null,
      view: Array.isArray(first.center) ? {center: first.center, zoom: first.zoom} : undefined,
      labels: model.get("labels") || {},
      theme: model.get("theme") || "light",
    });
  } catch (err) {
    // No views yet, an unknown layout, no Leaflet: say so and wait for the
    // next change of ``views`` or ``view_layout``, which builds again.
    fail("leaflet-multiview", err);
    return cleanup;
  }
  cleanups.push(() => mv.destroy());
  el._multiview = mv; // the debug handle, on the widget's own element too
  cleanups.push(() => {
    if (el._multiview === mv) delete el._multiview;
  });

  // Layers before plugins: the layer-menu binding re-applies the viewer's
  // toggles, and an override for a layer the core does not have yet would be
  // pruned by the first setLayers.
  mv.setLayers(model.get("layers") || []);

  listen("view", () => {
    const v = model.get("view") || {};
    const n = Number(v.n);
    if (!Array.isArray(v.center) || !(n > lastN)) return; // our own write-back, or stale
    lastN = n;
    mv.setView(v.center, v.zoom);
  });
  cleanups.push(
    mv.on("viewchange", ({center, zoom}) => {
      model.set("view", {center: toCenter(center), zoom, n: lastN, origin: "user"});
      model.save_changes();
    }),
  );
  listen("basemap", () => mv.setBasemap(model.get("basemap") || null));
  listen("layers", () => mv.setLayers(model.get("layers") || []));
  listen("labels", () => mv.setLabels(model.get("labels") || {}));
  listen("theme", () => mv.setTheme(model.get("theme") || "light"));

  for (const name of model.get("plugins") || []) {
    const entry = registry[name];
    if (!entry) {
      console.warn(`leaflet-multiview: no plugin registered as ${name}`);
      continue;
    }
    try {
      const api = mv.use(entry.plugin, entry.options ? entry.options(model) : undefined);
      if (entry.bind) {
        const unbind = entry.bind({model, api, mv});
        if (typeof unbind === "function") cleanups.push(unbind);
      }
    } catch (err) {
      fail(name, err);
    }
  }
  for (const plugin of always) {
    try {
      mv.use(plugin);
    } catch (err) {
      fail((plugin && plugin.name) || "plugin", err);
    }
  }
  // The same specs again: an empty diff, but a "layerschange" that plugins
  // installed after the first setLayers build their first state from.
  mv.setLayers(model.get("layers") || []);
  return cleanup;
}

export function createWidget({registry = {}, always = [], L} = {}) {
  function render({model, el}) {
    let alive = true;
    let current = mount({model, el, registry, always, L});
    let queued = false;
    // ``views`` and ``view_layout`` often change together: one rebuild.
    const rebuild = () => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        if (!alive) return;
        current();
        current = mount({model, el, registry, always, L});
      });
    };
    const events = ["change:views", "change:view_layout"];
    for (const event of events) model.on(event, rebuild);
    return () => {
      alive = false;
      for (const event of events) model.off(event, rebuild);
      current();
    };
  }
  return {render};
}
