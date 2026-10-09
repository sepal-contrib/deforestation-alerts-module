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
import {viewsOf} from "../../core/layers.js";

export const DRAW_STYLE = {color: "#2196F3", weight: 4, opacity: 0.5, fillOpacity: 0.2};

const PANE = "mv-drawing";
const PANE_Z_INDEX = "450"; // above the overlay pane (400), below Geoman's vertex markers (marker pane, 600)

// Edit mode shows "draw polygon" and "remove shape". Editing itself is on as
// long as edit mode is, so the edit button is never shown.
const TOOLBAR = {
  position: "topleft",
  drawPolygon: true, removalMode: true, editMode: false,
  drawMarker: false, drawCircleMarker: false, drawPolyline: false, drawRectangle: false,
  drawCircle: false, drawText: false, dragMode: false, cutPolygon: false, rotateMode: false,
};

const clone = value => JSON.parse(JSON.stringify(value)); // features are plain JSON

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
  labels: {hint: "Right-click a vertex to delete it"},

  install(mv, options) {
    const L = mv.L;
    if (!L.PM) throw new Error("draw plugin needs leaflet-geoman loaded on this Leaflet");

    // Every Geoman layer of ours is enabled with these: by enableGlobalEditMode,
    // by Geoman's layeradd handler (from globalOptions) and after removal mode.
    const EDIT = {allowSelfIntersection: false, removeVertexOn: options.removeVertexOn, snappable: false, draggable: false};
    const targetIds = viewsOf({view: options.views}, mv.views.map(v => v.id));
    const views = mv.views
      .filter(v => targetIds.includes(v.id))
      .map(v => ({id: v.id, map: v.map, layers: new Map(), offs: []}));

    let features = new Map(); // internal id -> feature; the ids never leave this plugin
    let nextId = 1;
    const newId = () => `f${nextId++}`;
    let editOn = false;
    let selected = null;
    let applying = false; // set while we change the maps ourselves: every Geoman handler stands down
    let destroyed = false;
    let gesture = null; // {view, layer, dirty, aborted, closing}: the edit in progress
    let closeToken = 0; // a scheduled close runs only while its token is current

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
    const Hint = L.Control.extend({
      options: {position: "bottomleft"},
      onAdd() {
        this._div = L.DomUtil.create("div", "mv-draw-hint");
        this._div.textContent = mv.label("draw", "hint");
        return this._div;
      },
      relabel() {
        if (this._div) this._div.textContent = mv.label("draw", "hint");
      },
    });
    let hints = [];
    let removeHints = null;

    function startEditing(view) {
      const pm = view.map.pm;
      pm.enableGlobalEditMode(EDIT);
      pm.Toolbar.toggleButton("editMode", false, false); // see the header (5374, 5495)
      pm.addControls(TOOLBAR);
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
      if (!map.getPane(PANE)) map.createPane(PANE).style.zIndex = PANE_Z_INDEX;
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
      // Removal mode turned editing off on this view's layers (3527).
      const onRemovalMode = e => {
        if (e.enabled || !editOn || destroyed) return;
        for (const layer of view.layers.values()) {
          if (layer.pm && map.hasLayer(layer)) layer.pm.enable(EDIT);
        }
      };
      map.on("pm:create", onCreate);
      map.on("pm:globalremovalmodetoggled", onRemovalMode);
      view.offs.push(() => map.off("pm:create", onCreate));
      view.offs.push(() => map.off("pm:globalremovalmodetoggled", onRemovalMode));
    }

    // Layers marked meta.hideWhileEditing (the alert box, the reviewed polygon)
    // are denied while edit mode is on.
    const removeRule = mv.addVisibilityRule(spec =>
      editOn && spec && spec.meta && spec.meta.hideWhileEditing ? false : undefined,
    );
    const offLabels = mv.on("labelschange", () => hints.forEach(hint => hint.relabel()));

    // --- API ---------------------------------------------------------------------------
    function setFeatures(fc) {
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
      abortGesture(false);
      destroyed = true;
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
