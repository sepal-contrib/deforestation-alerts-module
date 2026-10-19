import test, {beforeEach} from "node:test";
import assert from "node:assert/strict";
import {createSync, debounce} from "../src/core/sync.js";

// A controllable requestAnimationFrame: sync.js reads it at call time.
const frames = new Map();
let nextFrameId = 1;
globalThis.requestAnimationFrame = fn => {
  const id = nextFrameId++;
  frames.set(id, fn);
  return id;
};
globalThis.cancelAnimationFrame = id => {
  frames.delete(id);
};
function flushFrames() {
  const due = [...frames.values()];
  frames.clear();
  due.forEach(fn => fn());
}
beforeEach(() => frames.clear());

// A Leaflet-map stand-in. setView fires "zoom" (when the zoom changed), "move"
// and "moveend" synchronously, like Leaflet with {animate: false}.
// invalidateSize mimics {pan: false}: a new container size moves the centre and
// fires "move" + "moveend" synchronously.
function fakeMap(name, {w = 400, h = 300} = {}) {
  const handlers = {};
  const m = {
    name,
    center: [0, 0],
    zoom: 2,
    setViewCalls: 0,
    invalidateCalls: [],
    el: {clientWidth: w, clientHeight: h},
    measured: {w, h},
    on(ev, fn) {
      for (const e of ev.split(" ")) (handlers[e] ||= []).push(fn);
      return m;
    },
    off(ev, fn) {
      for (const e of ev.split(" ")) handlers[e] = (handlers[e] || []).filter(f => f !== fn);
      return m;
    },
    fire(ev, data) {
      for (const f of (handlers[ev] || []).slice()) f(data);
      return m;
    },
    listenerCount(ev) {
      return (handlers[ev] || []).length;
    },
    getContainer: () => m.el,
    getCenter: () => ({lat: m.center[0], lng: m.center[1]}),
    getZoom: () => m.zoom,
    setView(c, z) {
      m.setViewCalls++;
      const zoomChanged = z !== m.zoom;
      m.center = Array.isArray(c) ? [c[0], c[1]] : [c.lat, c.lng];
      m.zoom = z;
      if (zoomChanged) m.fire("zoom");
      m.fire("move");
      m.fire("moveend");
      return m;
    },
    invalidateSize(options) {
      m.invalidateCalls.push(options);
      const dx = (m.el.clientWidth - m.measured.w) / 2;
      const dy = (m.el.clientHeight - m.measured.h) / 2;
      m.measured = {w: m.el.clientWidth, h: m.el.clientHeight};
      if (!dx && !dy) return m;
      m.center = [m.center[0] + dy, m.center[1] - dx];
      m.fire("move");
      m.fire("moveend");
      return m;
    },
    // A user drag: the map moves itself through ``steps``, firing "move" for
    // each, then one "moveend".
    drag(...steps) {
      for (const c of steps) {
        m.center = c;
        m.fire("move");
      }
      m.fire("moveend");
    },
    // An animated zoom (wheel, buttons): Leaflet fires "zoomanim" with the
    // target at the start, and holds "zoom"/"move"/"moveend" until the CSS
    // transition ends. Returns the function that ends it.
    animateZoom(center, zoom) {
      m.fire("zoomanim", {center: {lat: center[0], lng: center[1]}, zoom});
      return () => {
        m.center = center;
        m.zoom = zoom;
        m.fire("zoom");
        m.fire("move");
        m.fire("moveend");
      };
    },
  };
  return m;
}

function trio() {
  const maps = [fakeMap("a"), fakeMap("b"), fakeMap("c")];
  const gestures = [];
  const sync = createSync(maps, {onGestureEnd: v => gestures.push(v)});
  return {maps, gestures, sync};
}

test("N = 3: a move on the leader sets both followers' view, frame by frame", () => {
  const {maps: [a, b, c]} = trio();
  a.center = [5, 6];
  a.zoom = 9;
  a.fire("move");
  assert.deepEqual([b.center, b.zoom], [[5, 6], 9]);
  assert.deepEqual([c.center, c.zoom], [[5, 6], 9]);
  a.center = [7, 8];
  a.fire("move");
  assert.deepEqual(b.center, [7, 8]);
  assert.deepEqual(c.center, [7, 8]);
});

test("the guard drops the followers' echoes: the leader is never set, and only its moveend is a gesture", () => {
  const {maps: [a, b, c], gestures} = trio();
  a.drag([1, 1], [2, 2], [3, 3]);
  assert.equal(a.setViewCalls, 0, "no follower event bounced back onto the leader");
  assert.ok(b.setViewCalls >= 3 && c.setViewCalls >= 3);
  assert.deepEqual(gestures, [{center: [3, 3], zoom: 2}], "one gesture: the followers' own moveends were dropped");
});

test("an animated zoom moves the followers when it starts, not when its transition ends", () => {
  const {maps: [a, b, c], gestures, sync} = trio();
  const end = a.animateZoom([5, 6], 9);
  assert.deepEqual([b.center, b.zoom], [[5, 6], 9]);
  assert.deepEqual([c.center, c.zoom], [[5, 6], 9]);
  assert.equal(a.setViewCalls, 0, "the followers' own events did not bounce back onto the leader");
  assert.deepEqual(gestures, [], "the gesture ends with the transition");
  end();
  assert.equal(a.setViewCalls, 0);
  assert.deepEqual(sync.canonical(), {center: [5, 6], zoom: 9});
  assert.deepEqual(gestures, [{center: [5, 6], zoom: 9}]);
});

test("while restoring, an animated zoom does not sync", () => {
  const {maps: [a, b], sync} = trio();
  sync.restore();
  a.animateZoom([5, 6], 9);
  assert.deepEqual([b.center, b.zoom], [[0, 0], 2]);
});

test("any view can lead; the end of its gesture becomes the canonical view", () => {
  const {maps: [a, b, c], gestures, sync} = trio();
  c.zoom = 11;
  c.drag([-3, -50]);
  assert.deepEqual([a.center, a.zoom], [[-3, -50], 11]);
  assert.deepEqual([b.center, b.zoom], [[-3, -50], 11]);
  assert.deepEqual(sync.canonical(), {center: [-3, -50], zoom: 11});
  assert.deepEqual(gestures, [{center: [-3, -50], zoom: 11}]);
});

test("onGestureEnd gets a copy: mutating it does not change the canonical view", () => {
  const {maps: [a], gestures, sync} = trio();
  a.drag([1, 2]);
  gestures[0].center[0] = 99;
  assert.deepEqual(sync.canonical().center, [1, 2]);
});

test("setCanonical moves every map and never fires onGestureEnd", () => {
  const {maps, gestures, sync} = trio();
  sync.setCanonical({center: [10, 20], zoom: 8});
  for (const m of maps) assert.deepEqual([m.center, m.zoom], [[10, 20], 8]);
  assert.deepEqual(gestures, []);
  assert.deepEqual(sync.canonical(), {center: [10, 20], zoom: 8});
  sync.setCanonical({center: {lat: 1, lng: 2}, zoom: 3});
  assert.deepEqual(sync.canonical(), {center: [1, 2], zoom: 3}, "a LatLng-like centre is stored as [lat, lng]");
});

test("restore re-measures every sized map, puts the canonical view back, and leaves zero-size maps alone", () => {
  const {maps: [a, b, c], gestures, sync} = trio();
  sync.setCanonical({center: [10, 20], zoom: 8});
  a.el.clientWidth = 600; // a grew
  b.el.clientHeight = 200; // b shrank
  c.el.clientWidth = 0; // c is hidden
  c.el.clientHeight = 0;
  const cCalls = c.setViewCalls;
  sync.restore();
  assert.deepEqual(a.invalidateCalls, [{pan: false}]);
  assert.deepEqual(b.invalidateCalls, [{pan: false}]);
  assert.deepEqual(c.invalidateCalls, [], "a hidden map keeps its last measured size");
  assert.equal(c.setViewCalls, cCalls);
  assert.deepEqual([a.center, a.zoom], [[10, 20], 8], "the resize-driven move was undone");
  assert.deepEqual([b.center, b.zoom], [[10, 20], 8]);
  assert.deepEqual(gestures, [], "restoring suppresses onGestureEnd");
  assert.deepEqual(sync.canonical(), {center: [10, 20], zoom: 8}, "a resize never writes the canonical view");
});

test("while restoring, moves neither sync nor end a gesture; the next frame clears it", () => {
  const {maps: [a, b], gestures, sync} = trio();
  sync.setCanonical({center: [10, 20], zoom: 8});
  sync.restore();
  assert.equal(sync.isRestoring(), true);
  a.center = [50, 50];
  a.fire("move");
  a.fire("moveend");
  assert.deepEqual(b.center, [10, 20], "no sync while restoring");
  assert.deepEqual(gestures, [], "no gesture while restoring");
  flushFrames();
  assert.equal(sync.isRestoring(), false);
  a.drag([1, 1]);
  assert.deepEqual(b.center, [1, 1]);
  assert.deepEqual(gestures, [{center: [1, 1], zoom: 8}]);
});

test("a second restore before the frame keeps restoring until its own frame", () => {
  const {sync} = trio();
  sync.restore();
  sync.restore();
  assert.equal(frames.size, 1, "the first frame was cancelled");
  flushFrames();
  assert.equal(sync.isRestoring(), false);
});

test("destroy detaches every listener and cancels the pending frame", () => {
  const {maps: [a, b], gestures, sync} = trio();
  sync.restore();
  sync.destroy();
  assert.equal(frames.size, 0);
  assert.equal(
    a.listenerCount("move") + a.listenerCount("zoom") + a.listenerCount("zoomanim") + a.listenerCount("moveend"),
    0,
  );
  a.drag([4, 4]);
  assert.deepEqual(b.center, [0, 0]);
  assert.deepEqual(gestures, []);
});

test("debounce runs once after the quiet period with the last arguments; cancel drops it", async () => {
  const seen = [];
  const d = debounce(v => seen.push(v), 20);
  d(1);
  d(2);
  d(3);
  await new Promise(r => setTimeout(r, 50));
  assert.deepEqual(seen, [3]);
  d(4);
  d.cancel();
  await new Promise(r => setTimeout(r, 50));
  assert.deepEqual(seen, [3]);
});
