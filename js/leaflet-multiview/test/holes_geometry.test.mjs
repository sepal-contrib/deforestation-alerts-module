// js/leaflet-multiview/test/holes_geometry.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  fillHoles, fillHolesInFeatures, holeCount, pointInRing, removeHoleAt, removeHoleInFeatures,
} from "../src/plugins/holes/geometry.js";

const shell = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
const holeA = [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]];
const holeB = [[6, 6], [8, 6], [8, 8], [6, 8], [6, 6]];
const poly = {type: "Polygon", coordinates: [shell, holeA, holeB]};
const multi = {
  type: "MultiPolygon",
  coordinates: [[shell, holeA], [[[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]], [[22, 2], [24, 2], [24, 4], [22, 4], [22, 2]]]],
};
const feature = (geometry, color = "blue") => ({type: "Feature", properties: {style: {color}}, geometry});

test("pointInRing: inside, outside", () => {
  assert.equal(pointInRing([2, 2], holeA), true);
  assert.equal(pointInRing([5, 5], holeA), false);
});

test("holeCount counts interior rings of Polygon and MultiPolygon", () => {
  assert.equal(holeCount(poly), 2);
  assert.equal(holeCount(multi), 2);
  assert.equal(holeCount({type: "Polygon", coordinates: [shell]}), 0);
  assert.equal(holeCount({type: "Point", coordinates: [0, 0]}), 0);
});

test("fillHoles removes every hole, Polygon and MultiPolygon, without mutating", () => {
  const before = JSON.stringify(poly);
  assert.deepEqual(fillHoles(poly).coordinates, [shell]);
  assert.deepEqual(fillHoles(multi).coordinates.map(rings => rings.length), [1, 1]);
  assert.equal(JSON.stringify(poly), before);
});

test("a polygon with no holes is left unchanged", () => {
  const plain = {type: "Polygon", coordinates: [shell]};
  assert.deepEqual(fillHoles(plain), plain);
  assert.deepEqual(removeHoleAt(plain, [2, 2]), {geometry: plain, removed: false});
});

test("removeHoleAt removes only the ring under the point", () => {
  const {geometry, removed} = removeHoleAt(poly, [7, 7]);
  assert.equal(removed, true);
  assert.deepEqual(geometry.coordinates, [shell, holeA]);
});

test("removeHoleAt: a point outside every hole, or on the shell, removes nothing", () => {
  assert.equal(removeHoleAt(poly, [5, 5]).removed, false);
  assert.equal(removeHoleAt(poly, [0, 5]).removed, false);
  assert.equal(removeHoleAt(poly, [50, 50]).removed, false);
});

test("removeHoleAt finds a hole in the second polygon of a MultiPolygon", () => {
  const {geometry, removed} = removeHoleAt(multi, [23, 3]);
  assert.equal(removed, true);
  assert.equal(geometry.coordinates[1].length, 1);
  assert.equal(geometry.coordinates[0].length, 2);
});

test("fillHolesInFeatures: all, or only the selected index; properties kept", () => {
  const fs = [feature(poly), feature(multi, "lime")];
  assert.deepEqual(fillHolesInFeatures(fs).map(f => holeCount(f.geometry)), [0, 0]);
  const one = fillHolesInFeatures(fs, 1);
  assert.deepEqual(one.map(f => holeCount(f.geometry)), [2, 0]);
  assert.equal(one[1].properties.style.color, "lime");
});

test("removeHoleInFeatures reports which feature changed", () => {
  const fs = [feature({type: "Polygon", coordinates: [shell]}), feature(poly)];
  const out = removeHoleInFeatures(fs, [2, 2]);
  assert.equal(out.removed, true);
  assert.equal(out.index, 1);
  assert.equal(holeCount(out.features[1].geometry), 1);
  assert.deepEqual(removeHoleInFeatures(fs, [5, 5]), {features: fs, removed: false, index: -1});
});
