import test from "node:test";
import assert from "node:assert/strict";
import {createRegistry} from "../src/core/registry.js";

const plugin = (name, extra = {}) => ({
  name,
  requires: [],
  defaults: {},
  labels: {},
  install: () => ({api: {name}, destroy() {}}),
  ...extra,
});

test("use passes {...defaults, ...options} and the mv to install, and returns the api", () => {
  const mv = {};
  const reg = createRegistry(mv);
  let got = null;
  const api = reg.use(
    plugin("p", {
      defaults: {views: "all", hint: true},
      install(m, options) {
        got = [m, options];
        return {api: {hello: () => "hi"}, destroy() {}};
      },
    }),
    {hint: false},
  );
  assert.equal(got[0], mv);
  assert.deepEqual(got[1], {views: "all", hint: false});
  assert.equal(api.hello(), "hi");
  assert.equal(reg.get("p"), api);
  assert.equal(reg.get("missing"), undefined);
  assert.deepEqual(reg.names(), ["p"]);
});

test("a requirement must be installed first", () => {
  const reg = createRegistry({});
  assert.throws(() => reg.use(plugin("holes", {requires: ["draw"]})), {
    message: "holes requires draw, which is not installed yet",
  });
  assert.deepEqual(reg.names(), []);
  reg.use(plugin("draw"));
  reg.use(plugin("holes", {requires: ["draw"]}));
  assert.deepEqual(reg.names(), ["draw", "holes"]);
});

test("a second use of the same name throws", () => {
  const reg = createRegistry({});
  reg.use(plugin("layerMenu"));
  assert.throws(() => reg.use(plugin("layerMenu")), {message: "layerMenu is already installed"});
});

test("a plugin without a name or an install function is refused", () => {
  const reg = createRegistry({});
  assert.throws(() => reg.use({install() {}}), /needs a name/);
  assert.throws(() => reg.use({name: "x"}), /needs a name/);
});

test("an install that throws leaves the plugin uninstalled", () => {
  const reg = createRegistry({});
  const bad = plugin("draw", {
    install() {
      throw new Error("draw plugin needs leaflet-geoman loaded on this Leaflet");
    },
  });
  assert.throws(() => reg.use(bad), /leaflet-geoman/);
  assert.deepEqual(reg.names(), []);
  assert.equal(reg.get("draw"), undefined);
  reg.use(plugin("draw"));
  assert.deepEqual(reg.names(), ["draw"]);
});

test("an install that returns nothing gets an empty api", () => {
  const reg = createRegistry({});
  assert.deepEqual(reg.use(plugin("quiet", {install: () => undefined})), {});
});

test("destroyAll runs the plugins' destroy in reverse install order and empties the registry", () => {
  const reg = createRegistry({});
  const order = [];
  for (const name of ["a", "b", "c"]) {
    reg.use(plugin(name, {install: () => ({api: {}, destroy: () => order.push(name)})}));
  }
  reg.destroyAll();
  assert.deepEqual(order, ["c", "b", "a"]);
  assert.deepEqual(reg.names(), []);
});

test("a destroy that throws is logged and the others still run", () => {
  const reg = createRegistry({});
  const order = [];
  reg.use(plugin("a", {install: () => ({api: {}, destroy: () => order.push("a")})}));
  reg.use(plugin("b", {install: () => ({api: {}, destroy: () => { throw new Error("boom"); }})}));
  const original = console.error;
  const errors = [];
  console.error = (...args) => errors.push(args);
  try {
    reg.destroyAll();
  } finally {
    console.error = original;
  }
  assert.deepEqual(order, ["a"]);
  assert.equal(errors.length, 1);
});

test("label: the override, else the plugin's English default, else the key", () => {
  const reg = createRegistry({});
  reg.use(plugin("layerMenu", {labels: {title: "Layers"}}));
  assert.equal(reg.label("layerMenu", "title", {}), "Layers");
  assert.equal(reg.label("layerMenu", "title", {"layerMenu.title": "Capas"}), "Capas");
  assert.equal(reg.label("layerMenu", "title", {"other.title": "Nope"}), "Layers");
  assert.equal(reg.label("layerMenu", "missing", {}), "missing");
  assert.equal(reg.label("notInstalled", "x", {"notInstalled.x": "Override"}), "Override");
  assert.equal(reg.label("notInstalled", "x"), "x");
});

test("a plugin can read its own labels while it installs", () => {
  const mv = {};
  const reg = createRegistry(mv);
  mv.label = (name, key) => reg.label(name, key, {});
  let seen = null;
  reg.use(plugin("fitBounds", {
    labels: {button: "Zoom to area"},
    install(m) {
      seen = m.label("fitBounds", "button");
      return {api: {}};
    },
  }));
  assert.equal(seen, "Zoom to area");
});
