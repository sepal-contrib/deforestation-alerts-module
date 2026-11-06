// The plugin registry. A plugin is
//   {name, requires: string[], defaults: {}, labels: {}, install(mv, options) -> {api, destroy}}
// Install order is dependency order: use() refuses a plugin whose requirement
// is not installed yet, and a second use() of the same name.

export function createRegistry(mv) {
  const installed = new Map(); // name -> {api, destroy}, in install order
  const definitions = new Map(); // name -> plugin, set before install() runs (for labels)

  function use(plugin, options = {}) {
    if (!plugin || typeof plugin.name !== "string" || !plugin.name || typeof plugin.install !== "function") {
      throw new Error("leaflet-multiview: a plugin needs a name and an install(mv, options) function");
    }
    const {name} = plugin;
    if (installed.has(name)) throw new Error(`${name} is already installed`);
    for (const dep of plugin.requires || []) {
      if (!installed.has(dep)) throw new Error(`${name} requires ${dep}, which is not installed yet`);
    }
    // Known before install() runs, so the plugin can read its labels while installing.
    definitions.set(name, plugin);
    let result;
    try {
      result = plugin.install(mv, {...(plugin.defaults || {}), ...(options || {})}) || {};
    } catch (err) {
      definitions.delete(name);
      throw err;
    }
    const api = result.api || {};
    installed.set(name, {api, destroy: typeof result.destroy === "function" ? result.destroy : null});
    return api;
  }

  function get(name) {
    const entry = installed.get(name);
    return entry ? entry.api : undefined;
  }

  function names() {
    return [...installed.keys()];
  }

  // labels["<plugin>.<key>"] when it is a string, else the plugin's English
  // default, else the key itself.
  function label(name, key, overrides = {}) {
    const override = overrides ? overrides[`${name}.${key}`] : undefined;
    if (typeof override === "string") return override;
    const plugin = definitions.get(name);
    const fallback = plugin && plugin.labels ? plugin.labels[key] : undefined;
    return typeof fallback === "string" ? fallback : key;
  }

  function destroyAll() {
    const entries = [...installed.entries()].reverse();
    installed.clear();
    for (const [name, {destroy}] of entries) {
      if (!destroy) continue;
      try {
        destroy();
      } catch (err) {
        console.error(`leaflet-multiview: destroying the ${name} plugin failed`, err);
      }
    }
    definitions.clear();
  }

  return {use, get, names, label, destroyAll};
}
