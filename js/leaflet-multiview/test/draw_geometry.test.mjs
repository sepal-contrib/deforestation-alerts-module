// js/leaflet-multiview/test/draw_geometry.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {geometryInside} from "../src/plugins/draw/geometry.js";

const box = {west: 0, south: 0, east: 10, north: 10};
const square = (x, y, s) => [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]];
const polygon = (...rings) => ({type: "Polygon", coordinates: rings});

test("a polygon wholly inside the box is inside", () => {
  assert.equal(geometryInside(polygon(square(2, 2, 3)), box), true);
});

test("a polygon partly outside the box is not inside", () => {
  assert.equal(geometryInside(polygon(square(8, 8, 5)), box), false);
});

test("a polygon touching the box's edge is inside", () => {
  assert.equal(geometryInside(polygon(square(0, 0, 10)), box), true);
});

test("a polygon around the box is not inside", () => {
  assert.equal(geometryInside(polygon(square(-5, -5, 20)), box), false);
});

test("only the outer ring counts: its holes are inside it", () => {
  assert.equal(geometryInside(polygon(square(1, 1, 8), square(3, 3, 2)), box), true);
});

test("a multipolygon is inside only when every part is", () => {
  const multi = parts => ({type: "MultiPolygon", coordinates: parts.map(p => [p])});
  assert.equal(geometryInside(multi([square(1, 1, 2), square(5, 5, 2)]), box), true);
  assert.equal(geometryInside(multi([square(1, 1, 2), square(9, 9, 2)]), box), false);
});

test("a point is inside when it is in the box", () => {
  assert.equal(geometryInside({type: "Point", coordinates: [5, 5]}, box), true);
  assert.equal(geometryInside({type: "Point", coordinates: [15, 5]}, box), false);
});

test("no geometry, or an empty one, is never inside", () => {
  assert.equal(geometryInside(null, box), false);
  assert.equal(geometryInside({type: "Polygon", coordinates: []}, box), false);
  assert.equal(geometryInside({type: "LineString", coordinates: [[1, 1], [2, 2]]}, box), false);
});
