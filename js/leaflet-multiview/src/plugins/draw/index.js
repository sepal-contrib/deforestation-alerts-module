// The draw plugin (spec §4.2): one FeatureCollection, rendered in every target
// view in the pane "mv-drawing" (above the overlays) and edited there with
// leaflet-geoman. An edit is mirrored to the other views and reported once,
// when it is finished: never per mouse move.
//
// Geoman 2.20.2 behaviour this relies on (line numbers in
// @geoman-io/leaflet-geoman-free/dist/leaflet-geoman.js; Leaflet 1.9.4
// src/layer/marker/Marker.Drag.js), checked in a browser on 2026-10-09:
// - Vertex drag: pm:markerdragstart (15041) ... pm:markerdragend (15116) on
//   release, then pm:edit (15131).
// - Midpoint drag: Leaflet fires the midpoint's "movestart" before its
//   "dragstart" (Marker.Drag.js 115-116). Geoman's _onMiddleMarkerMoveStart
//   (14758) turns the midpoint into a vertex first (_addMarker, 14786), which
//   fires pm:edit (14806) and pm:vertexadded (14809); pm:markerdragstart comes
//   next, in the same call stack, before any microtask.
// - Midpoint click: pm:edit + pm:vertexadded only (_onMiddleMarkerClick, 14749).
// - Right-click on a vertex: pm:edit (14968), then pm:vertexremoved (14970).
//   Below three vertices the polygon is taken off the map (layer.remove()) with
//   no pm:remove. A removal that makes the ring self-intersect is undone by
//   _handleSelfIntersectionOnVertexRemoval (14822) with no event at all.
// - A drag that leaves the polygon self-intersecting (allowSelfIntersection:
//   false): pm:markerdragend with intersectionReset (15116), then the ring Geoman
//   copied at drag start is put back -- for a midpoint drag that copy already
//   holds the inserted vertex -- and pm:layerreset fires (15125).
// - Removal mode: pm:remove on the layer after layer.remove() (removeLayer, 3574).
// - Drawing: L.polygon(coords, pathOptions) in globalOptions.panes.layerPane, then
//   pm:create on the map (_finishShape, 7130-7149).
// - enableGlobalEditMode (3383) enables the layers on the map at once, and each
//   layer added later through a 100 ms throttled "layeradd" handler (3433), with
//   the map's globalOptions. So a rebuilt view becomes editable within 100 ms.
// - enableGlobalRemovalMode (3527) disables editing on every layer;
//   disableGlobalRemovalMode does not re-enable it.
// - Toolbar: a click on any button first clicks every other toggled button
//   (triggerClickOnToggledButtons, 5374), and the edit button's afterClick toggles
//   global edit mode (5495). enableGlobalEditMode toggles that button on (3388),
//   even when it is not shown, so it is toggled off again right after: otherwise
//   a click on "draw polygon" or "remove" would end edit mode.
// - Toolbar actions (_makeButton, 5039): changeActionsOfControl (5816) swaps a
//   button's actions and rebuilds the toolbar; a custom action is an object
//   with text (set as innerHTML) and onClick, and Geoman stores the action's
//   <a> on it as action._node (class "action-<name>"). A click on an action
//   leaves the mode on; only "finishMode" clicks the button again.
// - disableGlobalRemovalMode (3550) toggles the trash button off and fires
//   pm:globalremovalmodetoggled. In removal mode a layer is deleted on its
//   "click", which Leaflet dispatches from the map container.
//
// Bulk deletes (the trash's actions "Select area" and "Clear all") never
// delete straight away: the shapes concerned turn red and a confirmation asks
// first. Select area arms a box drag in one view: the map container's own
// pointer and mouse events are taken in the capture phase while it is armed
// or asking, so Leaflet neither pans nor hands Geoman a removal click. Delete
// is one drawingchange; Cancel, Esc, Finish, leaving edit mode and any other
// change of the drawing close the confirmation and delete nothing.
import {viewsOf} from "../../core/layers.js";
import {createOrderedPane} from "../../core/panes.js";
import {geometryInside} from "./geometry.js";

export const DRAW_STYLE = {color: "#2196F3", weight: 4, opacity: 0.5, fillOpacity: 0.2};

const PANE = "mv-drawing";
const PANE_Z_INDEX = 450; // above the overlay pane (400), below Geoman's vertex markers (marker pane, 600)

// Edit mode shows "draw polygon" and "remove shape". Editing itself is on as
// long as edit mode is, so the edit button is never shown.
const TOOLBAR = {
  position: "topleft",
  drawPolygon: true, removalMode: true, editMode: false,
  drawMarker: false, drawCircleMarker: false, drawPolyline: false, drawRectangle: false,
  drawCircle: false, drawText: false, dragMode: false, cutPolygon: false, rotateMode: false,
};

const clone = value => JSON.parse(JSON.stringify(value)); // features are plain JSON

// What an armed, drawn or asked-about box takes from its view's map (see swallows()).
const SWALLOWED = ["mousedown", "mouseup", "click", "dblclick", "contextmenu", "touchstart"];

function asFeatures(fc) {
  if (!fc) return [];
  if (Array.isArray(fc)) return fc;
  if (fc.type === "FeatureCollection") return Array.isArray(fc.features) ? fc.features : [];
  if (fc.type === "Feature") return [fc];
  return [];
}

const sameFeatures = (a, b) => JSON.stringify([...a.values()]) === JSON.stringify([...b.values()]);

export const draw = {
  name: "draw",
  requires: [],
  defaults: {views: "all", removeVertexOn: "contextmenu", hint: true},
  labels: {
    hint: "Right-click a vertex to delete it",
    selectArea: "Select area",
    clearAll: "Clear all",
    selectAreaHint: "Drag a box around the shapes to delete · Esc to cancel",
    confirmOne: "Delete 1 shape?",
    confirmMany: "Delete {n} shapes?",
    confirmAll: "Delete all {n} shapes?",
    cancel: "Cancel",
    delete: "Delete",
  },

  install(mv, options) {
    const L = mv.L;
    if (!L.PM) throw new Error("draw plugin needs leaflet-geoman loaded on this Leaflet");

    // Every Geoman layer of ours is enabled with these: by enableGlobalEditMode,
    // by Geoman's layeradd handler (from globalOptions) and after removal mode.
    const EDIT = {allowSelfIntersection: false, removeVertexOn: options.removeVertexOn, snappable: false, draggable: false};
    const targetIds = viewsOf({view: options.views}, mv.views.map(v => v.id));
    const views = mv.views
      .filter(v => targetIds.includes(v.id))
      .map(v => ({id: v.id, map: v.map, layers: new Map(), offs: [], actions: null}));
    const doc = views.length ? views[0].map.getContainer().ownerDocument : null;

    let features = new Map(); // internal id -> feature; the ids never leave this plugin
    let nextId = 1;
    const newId = () => `f${nextId++}`;
    let editOn = false;
    let selected = null;
    let applying = false; // set while we change the maps ourselves: every Geoman handler stands down
    let destroyed = false;
    let gesture = null; // {view, layer, dirty, aborted, closing}: the edit in progress
    let closeToken = 0; // a scheduled close runs only while its token is current
    // The bulk delete in progress: {view, kind: "area" | "clear", phase:
    // "armed" | "dragging" | "confirm", ids, start, box, confirm, offDrag}.
    let bulk = null;

    const collection = () => ({type: "FeatureCollection", features: [...features.values()].map(clone)});

    // --- selection ---------------------------------------------------------------
    function highlight(view) {
      for (const [id, layer] of view.layers) {
        const el = layer.getElement && layer.getElement();
        if (el) el.classList.toggle("mv-draw-selected", id === selected);
      }
    }

    function setSelected(id) {
      if (id === selected) return;
      selected = id;
      for (const view of views) highlight(view);
      mv.emit("selectionchange", {id});
    }

    // --- rendering ---------------------------------------------------------------
    function buildLayer(view, id, feature) {
      const style = (feature.properties && feature.properties.style) || {};
      // properties.style may carry ipyleaflet keys such as pane: "overlayPane"
      // (convertir_formato3), so the keys that make it a drawing layer come last.
      const layer = L.GeoJSON.geometryToLayer(feature, {
        ...DRAW_STYLE, ...style, pane: PANE, pmIgnore: false, interactive: true,
        // A point is shown, never edited.
        pointToLayer: (f, latlng) =>
          L.circleMarker(latlng, {...DRAW_STYLE, ...style, pane: PANE, pmIgnore: true, interactive: false, radius: 6}),
      });
      layer._mvDrawId = id;
      watch(view, id, layer);
      return layer;
    }

    function dropLayer(view, id) {
      const layer = view.layers.get(id);
      if (!layer) return;
      if (layer._mvDrawOff) layer._mvDrawOff();
      if (view.map.hasLayer(layer)) view.map.removeLayer(layer);
      view.layers.delete(id);
    }

    // Rebuilds one view from the collection. In edit mode Geoman's own layeradd
    // handler enables the new layers (see the header).
    function render(view) {
      const was = applying;
      applying = true;
      try {
        for (const id of [...view.layers.keys()]) dropLayer(view, id);
        for (const [id, feature] of features) {
          if (!feature.geometry) continue; // nothing to show; kept in the collection as it is
          const layer = buildLayer(view, id, feature);
          view.layers.set(id, layer);
          view.map.addLayer(layer);
        }
      } finally {
        applying = was;
      }
      highlight(view);
    }

    // --- the gesture protocol ------------------------------------------------------
    function openGesture(view, layer) {
      if (gesture && (gesture.view !== view || gesture.layer !== layer)) finish(); // another edit's close is still pending
      if (!gesture) {
        gesture = {view, layer, dirty: false, aborted: false, closing: false};
      } else if (gesture.closing) {
        // A midpoint drag: pm:vertexadded closed a one-shot gesture, and
        // pm:markerdragstart, in the same call stack, turns it into a drag.
        gesture.closing = false;
        closeToken += 1;
      }
      return gesture;
    }

    // Closes in a microtask, so Geoman's synchronous follow-ups (pm:edit after
    // pm:markerdragend, pm:layerreset) land first.
    function scheduleClose() {
      gesture.closing = true;
      const token = ++closeToken;
      queueMicrotask(() => {
        if (token === closeToken && gesture) finish();
      });
    }

    function oneShot(view, layer) {
      const g = openGesture(view, layer);
      g.dirty = true;
      scheduleClose();
    }

    // Drops the gesture and its pending close. With ``rebuild``, the source view
    // is put back from the collection, which the gesture never touched.
    function abortGesture(rebuild) {
      const g = gesture;
      gesture = null;
      closeToken += 1;
      if (g && rebuild && !destroyed) render(g.view);
    }

    // The source view's geometry, read into a new collection. A feature whose
    // layer left the map is deleted; one created there (pm:create) is added.
    function readView(view) {
      const next = new Map();
      for (const [id, feature] of features) {
        const layer = view.layers.get(id);
        if (!layer) {
          next.set(id, feature); // never rendered (no geometry)
          continue;
        }
        if (!view.map.hasLayer(layer)) continue;
        next.set(id, {...feature, geometry: layer.toGeoJSON(false).geometry}); // full precision
      }
      for (const [id, layer] of view.layers) {
        if (!features.has(id) && view.map.hasLayer(layer)) {
          next.set(id, {type: "Feature", properties: {style: {...DRAW_STYLE}}, geometry: layer.toGeoJSON(false).geometry});
        }
      }
      for (const id of [...view.layers.keys()]) if (!next.has(id)) dropLayer(view, id);
      return next;
    }

    function finish() {
      const g = gesture;
      gesture = null;
      closeToken += 1;
      if (!g || destroyed) return;
      if (g.aborted) {
        render(g.view); // Geoman snapped the source back (with any inserted vertex): use the untouched collection
        return;
      }
      if (!g.dirty) return;
      const next = readView(g.view);
      if (sameFeatures(next, features)) return; // e.g. a vertex removal Geoman undid (14822)
      closeBulk(); // it was asking about shapes that have changed
      features = next;
      if (selected !== null && !features.has(selected)) setSelected(null);
      for (const view of views) if (view !== g.view) render(view); // the source keeps its Geoman state
      highlight(g.view);
      mv.emit("drawingchange", {features: collection()});
    }

    // Geoman events of one drawing layer. Each handler first checks that we are
    // not applying a change ourselves and that the layer is still the one this
    // view shows for that feature (a rebuilt layer's late events are strays).
    function watch(view, id, layer) {
      const current = () => !applying && !destroyed && view.layers.get(id) === layer;
      const handlers = {
        "pm:markerdragstart": () => {
          if (current()) openGesture(view, layer);
        },
        "pm:markerdragend": () => {
          if (current()) oneShot(view, layer);
        },
        "pm:vertexadded": () => {
          if (current()) oneShot(view, layer);
        },
        "pm:vertexremoved": () => {
          if (current()) oneShot(view, layer);
        },
        "pm:remove": () => {
          if (current()) oneShot(view, layer);
        },
        // Only marks an open gesture dirty: a pm:edit outside one never commits.
        "pm:edit": () => {
          if (current() && gesture && gesture.layer === layer) gesture.dirty = true;
        },
        "pm:layerreset": () => {
          if (!current() || !gesture || gesture.layer !== layer) return;
          gesture.aborted = true;
          scheduleClose();
        },
        click: () => {
          if (!current() || !editOn) return;
          const pm = view.map.pm;
          if (pm.globalDrawModeEnabled() || pm.globalRemovalModeEnabled()) return;
          setSelected(id);
        },
      };
      layer.on(handlers);
      layer._mvDrawOff = () => layer.off(handlers);
    }

    // --- edit mode, toolbar and hint -----------------------------------------------
    // A view arming Select area shows its own hint; the others keep the
    // vertex hint.
    const Hint = L.Control.extend({
      options: {position: "bottomleft"},
      onAdd(map) {
        this._map = map;
        this._div = L.DomUtil.create("div", "mv-draw-hint");
        this.relabel();
        return this._div;
      },
      relabel() {
        if (!this._div) return;
        const selecting = bulk && bulk.kind === "area" && bulk.view.map === this._map;
        this._div.textContent = mv.label("draw", selecting ? "selectAreaHint" : "hint");
      },
    });
    let hints = [];
    let removeHints = null;

    function startEditing(view) {
      const pm = view.map.pm;
      pm.enableGlobalEditMode(EDIT);
      pm.Toolbar.toggleButton("editMode", false, false); // see the header (5374, 5495)
      pm.addControls(TOOLBAR);
      setActions(view);
    }

    // The trash's actions, in this order: Select area, Clear all, Finish.
    // Rebuilt on a relabel (Geoman sets the text as innerHTML, hence the escape).
    function setActions(view) {
      const text = key => {
        const span = doc.createElement("span");
        span.textContent = mv.label("draw", key);
        return span.innerHTML;
      };
      view.actions = {
        selectArea: {name: "mvSelectArea", text: text("selectArea"), title: mv.label("draw", "selectArea"),
          onClick: () => toggleSelectArea(view)},
        clearAll: {name: "mvClearAll", text: text("clearAll"), title: mv.label("draw", "clearAll"),
          onClick: () => askClearAll(view)},
      };
      view.map.pm.Toolbar.changeActionsOfControl("removalMode",
        [view.actions.selectArea, view.actions.clearAll, "finishMode"]);
      markArmed();
    }

    function stopEditing(view) {
      const pm = view.map.pm;
      pm.disableDraw();
      if (pm.globalRemovalModeEnabled()) pm.disableGlobalRemovalMode();
      if (pm.globalEditModeEnabled()) pm.disableGlobalEditMode();
      pm.removeControls();
    }

    function setEditMode(on) {
      on = !!on;
      if (on === editOn || destroyed) return;
      if (!on) closeBulk();
      if (!on && gesture) {
        if (gesture.closing) finish();
        else abortGesture(true); // a drag still in progress: its view goes back to the collection
      }
      editOn = on;
      for (const view of views) (on ? startEditing : stopEditing)(view);
      if (on && options.hint) {
        removeHints = mv.addControl(targetIds, () => {
          const hint = new Hint();
          hints.push(hint);
          return hint;
        });
      } else if (!on && removeHints) {
        removeHints();
        removeHints = null;
        hints = [];
      }
      if (!on) setSelected(null);
      mv.refreshVisibility();
      mv.emit("editmodechange", {on});
    }

    // --- per-view Geoman setup -------------------------------------------------------
    for (const view of views) {
      const {map} = view;
      // In z order in the DOM too, so a host that flattens pane z-indexes still
      // stacks Geoman's vertex markers (marker pane) over the drawing.
      createOrderedPane(map, PANE, PANE_Z_INDEX);
      map.pm.setGlobalOptions({
        ...EDIT,
        pathOptions: {...DRAW_STYLE},
        panes: {vertexPane: "markerPane", layerPane: PANE, markerPane: "markerPane"},
      });
      const onCreate = e => {
        if (applying || destroyed || !e.layer) return;
        const id = newId();
        const layer = e.layer;
        layer._mvDrawId = id;
        view.layers.set(id, layer);
        watch(view, id, layer);
        oneShot(view, layer);
      };
      // Removal mode turned editing off on this view's layers (3527). Leaving
      // it (Finish, the trash again) ends this view's bulk delete.
      const onRemovalMode = e => {
        if (!e.enabled && bulk && bulk.view === view) closeBulk();
        if (e.enabled || !editOn || destroyed) return;
        for (const layer of view.layers.values()) {
          if (layer.pm && map.hasLayer(layer)) layer.pm.enable(EDIT);
        }
      };
      map.on("pm:create", onCreate);
      map.on("pm:globalremovalmodetoggled", onRemovalMode);
      view.offs.push(() => map.off("pm:create", onCreate));
      view.offs.push(() => map.off("pm:globalremovalmodetoggled", onRemovalMode));
      const container = map.getContainer();
      const onPointerDown = e => startBox(view, e);
      const onSwallow = e => {
        if (swallows(view, e)) swallow(e);
      };
      container.addEventListener("pointerdown", onPointerDown, true);
      for (const type of SWALLOWED) container.addEventListener(type, onSwallow, true);
      view.offs.push(() => {
        container.removeEventListener("pointerdown", onPointerDown, true);
        for (const type of SWALLOWED) container.removeEventListener(type, onSwallow, true);
      });
    }

    // --- bulk deletes: Select area and Clear all -------------------------------------
    // What the trash's actions put in front of a deletion; see the header.
    const onControl = target => !!(target && target.closest && target.closest(".leaflet-control"));
    const onConfirm = target => !!(target && target.closest && target.closest(".mv-bulk-confirm"));

    // The view's own events Leaflet must not see: all of them while its bulk
    // delete is armed, drawn or asking, except on the toolbar and the
    // confirmation. Immediate: Leaflet also listens on the container itself.
    function swallows(view, e) {
      if (!bulk || bulk.view !== view || destroyed) return false;
      return !onControl(e.target) && !onConfirm(e.target);
    }

    function swallow(e) {
      e.stopImmediatePropagation();
      L.DomEvent.stop(e);
    }

    function setDoomed(ids) {
      for (const view of views) {
        for (const [id, layer] of view.layers) {
          const el = layer.getElement && layer.getElement();
          if (el) el.classList.toggle("mv-draw-doomed", ids.has(id));
        }
      }
    }

    function markArmed() {
      for (const view of views) {
        const node = view.actions && view.actions.selectArea._node;
        if (node) node.classList.toggle("mv-active", !!(bulk && bulk.kind === "area" && bulk.view === view));
      }
    }

    function syncBulkLook() {
      for (const view of views) {
        view.map.getContainer().classList.toggle("mv-selecting",
          !!(bulk && bulk.kind === "area" && bulk.view === view && bulk.phase !== "confirm"));
      }
      markArmed();
      hints.forEach(hint => hint.relabel());
    }

    function closeBulk() {
      const b = bulk;
      if (!b) return;
      bulk = null;
      if (b.offDrag) b.offDrag();
      if (b.box) b.box.remove();
      if (b.confirm) b.confirm.remove();
      setDoomed(new Set());
      syncBulkLook();
    }

    function toggleSelectArea(view) {
      if (destroyed || !editOn) return;
      const armed = bulk && bulk.kind === "area" && bulk.view === view;
      closeBulk();
      if (!armed) bulk = {view, kind: "area", phase: "armed", ids: new Set()};
      syncBulkLook();
    }

    function askClearAll(view) {
      if (destroyed || !editOn) return;
      closeBulk();
      if (!features.size) return; // nothing to clear, nothing to ask
      bulk = {view, kind: "clear", phase: "confirm", ids: new Set(features.keys())};
      setDoomed(bulk.ids);
      // Under the trash's action strip.
      const container = view.map.getContainer();
      const strip = container.querySelector(".leaflet-pm-icon-delete");
      const actions = strip && strip.closest(".button-container");
      const where = actions && actions.querySelector(".leaflet-pm-actions-container");
      const rect = (where || actions || container).getBoundingClientRect();
      const origin = container.getBoundingClientRect();
      showConfirm(view, {left: rect.left - origin.left, top: rect.bottom - origin.top + 6});
      syncBulkLook();
    }

    const boxBounds = (map, a, b) => {
      const p = map.containerPointToLatLng(L.point(Math.min(a.x, b.x), Math.min(a.y, b.y)));
      const q = map.containerPointToLatLng(L.point(Math.max(a.x, b.x), Math.max(a.y, b.y)));
      return {west: p.lng, north: p.lat, east: q.lng, south: q.lat};
    };

    function idsInside(bounds) {
      const ids = new Set();
      for (const [id, feature] of features) if (geometryInside(feature.geometry, bounds)) ids.add(id);
      return ids;
    }

    function startBox(view, e) {
      if (!swallows(view, e)) return;
      swallow(e);
      if (bulk.kind !== "area" || bulk.phase !== "armed" || e.button !== 0) return;
      const map = view.map;
      const container = map.getContainer();
      const start = map.mouseEventToContainerPoint(e);
      const box = L.DomUtil.create("div", "mv-select-box", container);
      bulk = {...bulk, phase: "dragging", start, box, end: start};
      const draw = point => {
        const x = Math.min(start.x, point.x), y = Math.min(start.y, point.y);
        Object.assign(box.style, {left: `${x}px`, top: `${y}px`,
          width: `${Math.abs(point.x - start.x)}px`, height: `${Math.abs(point.y - start.y)}px`});
      };
      draw(start);
      const onMove = ev => {
        if (!bulk || bulk.box !== box) return;
        const point = map.mouseEventToContainerPoint(ev);
        bulk.end = point;
        draw(point);
        bulk.ids = idsInside(boxBounds(map, start, point));
        setDoomed(bulk.ids);
      };
      const onUp = ev => {
        if (!bulk || bulk.box !== box) return;
        onMove(ev);
        bulk.offDrag();
        bulk.offDrag = null;
        if (!bulk.ids.size) {
          box.remove(); // nothing wholly inside: no question, still armed
          bulk = {view, kind: "area", phase: "armed", ids: new Set()};
          setDoomed(bulk.ids);
          syncBulkLook();
          return;
        }
        bulk.phase = "confirm";
        showConfirm(view, {box: {
          left: Math.min(start.x, bulk.end.x), right: Math.max(start.x, bulk.end.x),
          top: Math.min(start.y, bulk.end.y), bottom: Math.max(start.y, bulk.end.y),
        }});
        syncBulkLook();
      };
      doc.addEventListener("pointermove", onMove, true);
      doc.addEventListener("pointerup", onUp, true);
      bulk.offDrag = () => {
        doc.removeEventListener("pointermove", onMove, true);
        doc.removeEventListener("pointerup", onUp, true);
      };
      syncBulkLook();
    }

    // The confirmation in the view: at {left, top}, or beside ``box`` (right
    // of it, else left, else below, else above) so it never covers the shapes
    // it asks about; kept inside the view either way.
    function showConfirm(view, {left, top, box}) {
      const container = view.map.getContainer();
      const n = bulk.ids.size;
      const key = n === 1 ? "confirmOne" : bulk.kind === "clear" ? "confirmAll" : "confirmMany";
      const el = L.DomUtil.create("div", "mv-bulk-confirm", container);
      const message = L.DomUtil.create("span", "mv-bulk-message", el);
      message.textContent = mv.label("draw", key).replace("{n}", String(n));
      const cancel = L.DomUtil.create("button", "mv-bulk-cancel", el);
      const del = L.DomUtil.create("button", "mv-bulk-delete", el);
      cancel.type = del.type = "button";
      cancel.textContent = mv.label("draw", "cancel");
      del.textContent = mv.label("draw", "delete");
      L.DomEvent.disableClickPropagation(el);
      L.DomEvent.disableScrollPropagation(el);
      L.DomEvent.on(cancel, "click", ev => {
        L.DomEvent.stop(ev);
        closeBulk();
      });
      L.DomEvent.on(del, "click", ev => {
        L.DomEvent.stop(ev);
        deleteAsked();
      });
      bulk.confirm = el;
      const w = el.offsetWidth, h = el.offsetHeight, gap = 8;
      const width = container.clientWidth, height = container.clientHeight;
      if (box) {
        if (box.right + gap + w <= width - gap) [left, top] = [box.right + gap, box.top];
        else if (box.left - gap - w >= gap) [left, top] = [box.left - gap - w, box.top];
        else if (box.bottom + gap + h <= height - gap) [left, top] = [box.left, box.bottom + gap];
        else [left, top] = [box.left, box.top - gap - h];
      }
      el.style.left = `${Math.max(gap, Math.min(left, width - w - gap))}px`;
      el.style.top = `${Math.max(gap, Math.min(top, height - h - gap))}px`;
    }

    // Delete: the shapes asked about leave the collection, in one
    // drawingchange. Clear all also ends removal mode in its view.
    function deleteAsked() {
      const b = bulk;
      if (!b || b.phase !== "confirm" || destroyed) return;
      closeBulk();
      abortGesture(false); // every view is rebuilt below
      const next = new Map([...features].filter(([id]) => !b.ids.has(id)));
      if (next.size !== features.size) {
        features = next;
        if (selected !== null && !features.has(selected)) setSelected(null);
        for (const view of views) render(view);
        mv.emit("drawingchange", {features: collection()});
      }
      if (b.kind === "clear" && b.view.map.pm.globalRemovalModeEnabled()) b.view.map.pm.disableGlobalRemovalMode();
    }

    const onKey = e => {
      if (e.key === "Escape" && bulk) closeBulk();
    };
    if (doc) doc.addEventListener("keydown", onKey);

    // Layers marked meta.hideWhileEditing (the alert box, the reviewed polygon)
    // are denied while edit mode is on.
    const removeRule = mv.addVisibilityRule(spec =>
      editOn && spec && spec.meta && spec.meta.hideWhileEditing ? false : undefined,
    );
    const offLabels = mv.on("labelschange", () => {
      hints.forEach(hint => hint.relabel());
      if (editOn) views.forEach(setActions);
    });

    // --- API ---------------------------------------------------------------------------
    function setFeatures(fc) {
      closeBulk(); // a host write: the shapes asked about may be gone
      abortGesture(false); // every view is rebuilt below
      const was = applying;
      applying = true;
      try {
        features = new Map();
        for (const f of asFeatures(fc)) if (f && typeof f === "object") features.set(newId(), clone(f));
        setSelected(null);
        for (const view of views) render(view);
      } finally {
        applying = was;
      }
    }

    // A change made by another plugin (holes): the features in order, mapped to
    // the current ids by position, so the selection survives. One drawingchange,
    // unless nothing changed.
    function commit(fc) {
      closeBulk();
      abortGesture(false);
      const ids = [...features.keys()];
      const next = new Map();
      asFeatures(fc)
        .filter(f => f && typeof f === "object")
        .forEach((f, i) => next.set(i < ids.length ? ids[i] : newId(), clone(f)));
      if (sameFeatures(next, features)) return;
      features = next;
      if (selected !== null && !features.has(selected)) setSelected(null);
      for (const view of views) render(view);
      mv.emit("drawingchange", {features: collection()});
    }

    function destroy() {
      if (destroyed) return;
      closeBulk();
      abortGesture(false);
      destroyed = true;
      if (doc) doc.removeEventListener("keydown", onKey);
      if (removeHints) removeHints();
      removeHints = null;
      hints = [];
      removeRule();
      offLabels();
      for (const view of views) {
        if (editOn) stopEditing(view);
        view.offs.forEach(off => off());
        for (const id of [...view.layers.keys()]) dropLayer(view, id);
      }
      features = new Map();
    }

    return {
      api: {
        setFeatures,
        features: collection,
        setEditMode,
        editMode: () => editOn,
        selection: () => selected,
        select(id) {
          if (id === null || id === undefined) setSelected(null);
          else if (features.has(id)) setSelected(id);
        },
        // The position of a feature id in features().features, or -1 (for holes).
        indexOf: id => [...features.keys()].indexOf(id),
        commit,
      },
      destroy,
    };
  },
};
