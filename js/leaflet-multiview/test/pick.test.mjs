// The pick plugin: a map click emits "pick" unless the draw plugin is editing
// or Geoman has a draw/removal mode on. Fake maps: no DOM needed.
import test from "node:test";
import assert from "node:assert/strict";
import {pick, shouldPick} from "../src/plugins/pick/index.js";
import {createEmitter} from "../src/core/events.js";

function fakeMap(zoom = 14, pm = undefined) {
  const handlers = {};
  return {
    pm,
    getZoom: () => zoom,
    on: (ev, f) => (handlers[ev] = handlers[ev] || []).push(f),
    off: (ev, f) => (handlers[ev] = (handlers[ev] || []).filter(g => g !== f)),
    click: (lat, lng) => (handlers.click || []).slice().forEach(f => f({latlng: {lat, lng}})),
    count: () => (handlers.click || []).length,
  };
}

function setup({editing = false, pm = undefined} = {}) {
  const maps = {before: fakeMap(13, pm), after: fakeMap(15, pm)};
  const emitter = createEmitter();
  const state = {editing};
  const mv = {
    views: Object.entries(maps).map(([id, map]) => ({id, map})),
    on: emitter.on,
    emit: emitter.emit,
    plugin: name => {
      assert.equal(name, "draw");
      return {editMode: () => state.editing};
    },
  };
  const picks = [];
  emitter.on("pick", p => picks.push(p));
  const installed = pick.install(mv, {});
  return {maps, picks, state, installed};
}

test("the plugin needs draw", () => {
  assert.equal(pick.name, "pick");
  assert.deepEqual(pick.requires, ["draw"]);
});

test("a click emits pick with the view id, the latlng and the zoom", () => {
  const {maps, picks} = setup();
  maps.before.click(-1.5, -60.25);
  maps.after.click(2, 3);
  assert.deepEqual(picks, [
    {view: "before", lat: -1.5, lng: -60.25, zoom: 13},
    {view: "after", lat: 2, lng: 3, zoom: 15},
  ]);
});

test("no pick while the draw plugin is in edit mode", () => {
  const {maps, picks, state} = setup({editing: true});
  maps.before.click(0, 0);
  assert.equal(picks.length, 0);
  state.editing = false;
  maps.before.click(0, 0);
  assert.equal(picks.length, 1);
});

test("no pick while Geoman's draw or removal mode is on", () => {
  const pm = {draw: false, removal: false, globalDrawModeEnabled: () => pm.draw, globalRemovalModeEnabled: () => pm.removal};
  const {maps, picks} = setup({pm});
  pm.draw = true;
  maps.before.click(0, 0);
  pm.draw = false;
  pm.removal = true;
  maps.before.click(0, 0);
  assert.equal(picks.length, 0);
  pm.removal = false;
  maps.before.click(0, 0);
  assert.equal(picks.length, 1);
});

test("shouldPick is the decision on its own", () => {
  assert.equal(shouldPick(false, undefined), true);
  assert.equal(shouldPick(true, undefined), false);
  assert.equal(shouldPick(false, {globalDrawModeEnabled: () => true, globalRemovalModeEnabled: () => false}), false);
});

test("destroy removes the click listeners and silences the plugin", () => {
  const {maps, picks, installed} = setup();
  installed.destroy();
  assert.equal(maps.before.count() + maps.after.count(), 0);
  maps.before.click(0, 0);
  assert.equal(picks.length, 0);
});
