// js/leaflet-multiview/test/layers.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {ALL, byZ, diffLayers, isVisible, layerKey, pruneOverrides, viewsOf} from "../src/core/layers.js";

const VIEWS = ["a", "b", "c"];
const tile = (id, extra = {}) => ({id, view: ALL, kind: "tile", url: `https://t/${id}/{z}/{x}/{y}`,
  name: id, visible: true, opacity: 1, z: 0, group: "g", meta: {}, ...extra});

// ---- view targeting -------------------------------------------------------

test("ALL is \"all\" and targets every view, in view order", () => {
  assert.equal(ALL, "all");
  assert.deepEqual(viewsOf(tile("x"), VIEWS), ["a", "b", "c"]);
  assert.deepEqual(viewsOf(tile("x", {view: "all"}), ["only"]), ["only"]);
});

test("viewsOf: one view id targets that view", () => {
  assert.deepEqual(viewsOf(tile("x", {view: "b"}), VIEWS), ["b"]);
});

test("viewsOf: an array targets those views, unknown ids filtered out", () => {
  assert.deepEqual(viewsOf(tile("x", {view: ["c", "a"]}), VIEWS), ["c", "a"]);
  assert.deepEqual(viewsOf(tile("x", {view: ["a", "nope", "c"]}), VIEWS), ["a", "c"]);
  assert.deepEqual(viewsOf(tile("x", {view: ["nope"]}), VIEWS), []);
  assert.deepEqual(viewsOf(tile("x", {view: []}), VIEWS), []);
});

test("viewsOf: a spec without a view targets every view", () => {
  const {view, ...noView} = tile("x");
  assert.deepEqual(viewsOf(noView, VIEWS), ["a", "b", "c"]);
});

test("viewsOf returns a new array, never the caller's", () => {
  const out = viewsOf(tile("x"), VIEWS);
  out.push("z");
  assert.deepEqual(VIEWS, ["a", "b", "c"]);
});

// ---- the diff -------------------------------------------------------------

test("layerKey ignores visible and key order, at every depth", () => {
  assert.equal(layerKey(tile("a")), layerKey({...tile("a"), visible: false}));
  const reordered = Object.fromEntries(Object.entries(tile("a", {style: {color: "red", weight: 2}})).reverse());
  reordered.style = {weight: 2, color: "red"};
  assert.equal(layerKey(reordered), layerKey(tile("a", {style: {color: "red", weight: 2}})));
  assert.notEqual(layerKey(tile("a")), layerKey(tile("a", {url: "https://other/{z}/{x}/{y}"})));
  assert.notEqual(layerKey(tile("a")), layerKey(tile("a", {meta: {reportErrors: true}})));
  assert.notEqual(layerKey(tile("a")), layerKey(tile("a", {view: "b"})));
});

test("diffLayers: add, replace when the spec changed, remove, keep", () => {
  const current = new Map([["a", layerKey(tile("a"))], ["b", layerKey(tile("b"))], ["c", layerKey(tile("c"))]]);
  const specs = [tile("a"), tile("b", {opacity: 0.5}), tile("d")];
  const {add, replace, remove, keep} = diffLayers(current, specs);
  assert.deepEqual(add.map(s => s.id), ["d"]);
  assert.deepEqual(replace.map(s => s.id), ["b"]);
  assert.deepEqual(remove, ["c"]);
  assert.deepEqual(keep.map(s => s.id), ["a"]);
});

test("diffLayers: a nested change (style, data) is a replace", () => {
  const before = tile("a", {kind: "geojson", url: undefined, data: {type: "Point", coordinates: [0, 0]},
    style: {color: "red"}});
  const current = new Map([["a", layerKey(before)]]);
  assert.deepEqual(diffLayers(current, [{...before, style: {color: "blue"}}]).replace.map(s => s.id), ["a"]);
  assert.deepEqual(diffLayers(current, [{...before, data: {type: "Point", coordinates: [1, 0]}}])
    .replace.map(s => s.id), ["a"]);
});

test("a visibility change alone is a keep, not a replace", () => {
  const current = new Map([["a", layerKey(tile("a"))]]);
  const out = diffLayers(current, [tile("a", {visible: false})]);
  assert.deepEqual(out.keep.map(s => s.id), ["a"]);
  assert.deepEqual([out.add, out.replace, out.remove], [[], [], []]);
});

test("diffLayers on an empty view adds everything; no specs removes everything", () => {
  assert.deepEqual(diffLayers(new Map(), [tile("a"), tile("b")]).add.map(s => s.id), ["a", "b"]);
  const current = new Map([["a", layerKey(tile("a"))]]);
  assert.deepEqual(diffLayers(current, []), {add: [], replace: [], remove: ["a"], keep: []});
});

// ---- visibility: deny-only rules, then the override, then spec.visible ----

test("isVisible: spec.visible when no rule and no override applies", () => {
  assert.equal(isVisible(tile("a"), "a", {rules: [], overrides: {}}), true);
  assert.equal(isVisible(tile("a", {visible: false}), "a", {rules: [], overrides: {}}), false);
  assert.equal(isVisible({id: "a"}, "a", {}), true, "a missing visible means shown");
  assert.equal(isVisible(tile("a")), true, "rules and overrides are optional");
});

test("isVisible: an override beats spec.visible, in its own view only", () => {
  const overrides = {b: {a: true}, c: {a: false}};
  assert.equal(isVisible(tile("a", {visible: false}), "b", {overrides}), true);
  assert.equal(isVisible(tile("a", {visible: false}), "a", {overrides}), false);
  assert.equal(isVisible(tile("a"), "c", {overrides}), false);
});

test("isVisible: a rule returning false beats an override set to true", () => {
  const deny = spec => (spec.meta?.hideWhileEditing ? false : undefined);
  const bb = tile("bb", {meta: {hideWhileEditing: true}});
  assert.equal(isVisible(bb, "a", {rules: [deny], overrides: {a: {bb: true}}}), false);
  assert.equal(isVisible(tile("other"), "a", {rules: [deny], overrides: {}}), true);
});

test("isVisible: rules returning undefined or true are ignored (rules only deny)", () => {
  const silent = () => undefined;
  const yes = () => true;
  assert.equal(isVisible(tile("a", {visible: false}), "a", {rules: [silent, yes]}), false);
  assert.equal(isVisible(tile("a", {visible: false}), "a", {rules: [yes], overrides: {a: {a: true}}}), true);
  assert.equal(isVisible(tile("a"), "a", {rules: [silent, yes]}), true);
});

test("isVisible: rules may be any iterable and receive (spec, viewId)", () => {
  const seen = [];
  const onlyInB = (spec, viewId) => { seen.push([spec.id, viewId]); return viewId === "b" ? undefined : false; };
  const rules = new Set([onlyInB]);
  assert.equal(isVisible(tile("x"), "a", {rules}), false);
  assert.equal(isVisible(tile("x"), "b", {rules}), true);
  assert.deepEqual(seen, [["x", "a"], ["x", "b"]]);
});

// ---- override pruning -----------------------------------------------------

test("pruneOverrides: an override survives a same-id update (same object back)", () => {
  const overrides = {a: {x: false}};
  const updated = [tile("x", {url: "https://new/{z}/{x}/{y}"})];
  assert.equal(pruneOverrides(overrides, updated, VIEWS), overrides, "same object: nothing to write back");
  assert.equal(isVisible(updated[0], "a", {overrides}), false);
});

test("pruneOverrides drops every override of a layer that is gone", () => {
  const overrides = {a: {x: true, gone: false}, b: {gone: true}};
  const pruned = pruneOverrides(overrides, [tile("x")], VIEWS);
  assert.deepEqual(pruned, {a: {x: true}, b: {}});
  assert.notEqual(pruned, overrides);
  assert.deepEqual(overrides, {a: {x: true, gone: false}, b: {gone: true}}, "the input is not mutated");
});

test("pruneOverrides drops the override of a view the layer left, keeps the one where it still is", () => {
  const overrides = {a: {x: false}, b: {x: false}, c: {x: true}};
  const pruned = pruneOverrides(overrides, [tile("x", {view: ["a", "c"]})], VIEWS);
  assert.deepEqual(pruned, {a: {x: false}, b: {}, c: {x: true}});
  assert.deepEqual(pruneOverrides(overrides, [tile("x", {view: "b"})], VIEWS), {a: {}, b: {x: false}, c: {}});
});

test("pruneOverrides drops overrides for a view that does not exist", () => {
  const overrides = {a: {x: true}, zz: {x: true}};
  assert.deepEqual(pruneOverrides(overrides, [tile("x")], VIEWS), {a: {x: true}, zz: {}});
});

test("pruneOverrides returns the same object when nothing was dropped", () => {
  const overrides = {a: {x: true, y: false}, c: {y: true}};
  const specs = [tile("x", {view: ["a"]}), tile("y")];
  assert.equal(pruneOverrides(overrides, specs, VIEWS), overrides);
  const empty = {};
  assert.equal(pruneOverrides(empty, [], VIEWS), empty);
  assert.equal(pruneOverrides(undefined, [tile("x")], VIEWS), undefined);
});

// ---- draw order -----------------------------------------------------------

test("byZ sorts by z, missing or null z as 0", () => {
  const sorted = [tile("b", {z: 5}), {id: "x"}, tile("a", {z: 2}), tile("n", {z: null}), tile("m", {z: -1})]
    .sort(byZ).map(s => s.id);
  assert.deepEqual(sorted, ["m", "x", "n", "a", "b"]);
});

test("byZ is stable: equal z keeps the input order", () => {
  const specs = ["p", "q", "r", "s", "t", "u"].map((id, i) => tile(id, {z: i % 2}));
  assert.deepEqual(specs.slice().sort(byZ).map(s => s.id), ["p", "r", "t", "q", "s", "u"]);
});
