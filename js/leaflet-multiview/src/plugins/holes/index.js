// The holes plugin (spec §4.3): "Fill holes" and "Remove hole" for the draw
// plugin's polygons, as a two-button bar under the draw toolbar of every view
// while edit mode is on.
//
// - Fill holes: removes the interior rings of the selected drawing feature,
//   or of every feature when nothing is selected, in one draw.commit().
// - Remove hole: a click mode. Each map click inside an interior ring deletes
//   that ring (one commit per removal). Esc, the button again, or leaving edit
//   mode ends it. While it is on, a hint replaces the draw hint and the map
//   cursor is a crosshair.
// - A map click that hits no drawing polygon clears the draw selection, so
//   "Fill holes" can go back to filling every polygon.
//
// The drawing changes only through the draw API (features, selection, select,
// indexOf, editMode, commit), and the bar follows its "editmodechange" event.
// The draw widget binding may turn edit mode on before this plugin installs,
// so install() reads editMode() for the first state.
//
// A hole is not painted (Leaflet fills with fill-rule evenodd), so a click
// inside one reaches the map, whose "click" carries the latlng. A click on a
// drawing polygon fires the polygon's "click" (draw selects it) and then the
// map's; the map handler tells them apart by the DOM target, an interactive
// SVG path in the "mv-drawing" pane.
import {fillHolesInFeatures, removeHoleInFeatures} from "./geometry.js";

const ON_DRAWING = ".leaflet-mv-drawing-pane path.leaflet-interactive";

export const holes = {
  name: "holes",
  requires: ["draw"],
  defaults: {},
  labels: {fill: "Fill holes", remove: "Remove hole", hint: "Click inside a hole to remove it · Esc to stop"},

  install(mv) {
    const L = mv.L;
    const draw = mv.plugin("draw");
    const doc = mv.views.length ? mv.views[0].map.getContainer().ownerDocument : null;
    let bars = []; // {fill, remove}: the two links of each shown bar
    let hints = []; // the remove-mode hint elements
    let removeBars = null;
    let removeHints = null;
    let removing = false;
    let destroyed = false;

    const collection = features => ({type: "FeatureCollection", features});

    function fillHoles() {
      if (destroyed) return;
      const id = draw.selection();
      const index = id === null ? -1 : draw.indexOf(id);
      draw.commit(collection(fillHolesInFeatures(draw.features().features, index < 0 ? null : index)));
    }

    function relabel() {
      const fill = mv.label("holes", "fill");
      const remove = mv.label("holes", "remove");
      for (const bar of bars) {
        bar.fill.title = fill;
        bar.fill.setAttribute("aria-label", fill);
        bar.remove.title = remove;
        bar.remove.setAttribute("aria-label", remove);
      }
      const hint = mv.label("holes", "hint");
      for (const el of hints) el.textContent = hint;
    }

    // Ignored (stays off) while edit mode is off.
    function setRemoveMode(on) {
      on = !!on && !destroyed && draw.editMode();
      if (on === removing) return;
      removing = on;
      for (const bar of bars) bar.remove.classList.toggle("mv-active", on);
      for (const v of mv.views) v.map.getContainer().classList.toggle("mv-removing", on);
      if (on) {
        removeHints = mv.addControl("all", () => {
          const control = L.control({position: "bottomleft"});
          control.onAdd = () => {
            const el = L.DomUtil.create("div", "mv-holes-hint");
            el.textContent = mv.label("holes", "hint");
            hints.push(el);
            return el;
          };
          return control;
        });
      } else {
        if (removeHints) removeHints();
        removeHints = null;
        hints = [];
      }
    }

    function showBars() {
      if (removeBars || destroyed) return;
      removeBars = mv.addControl("all", () => {
        const control = L.control({position: "topleft"});
        control.onAdd = () => {
          const bar = L.DomUtil.create("div", "leaflet-bar mv-hole-bar");
          const fill = L.DomUtil.create("a", "mv-fill-holes", bar);
          const remove = L.DomUtil.create("a", "mv-remove-hole", bar);
          fill.textContent = "▣"; // a filled square
          remove.textContent = "▢"; // a square with a hole
          for (const a of [fill, remove]) {
            a.href = "#";
            a.setAttribute("role", "button");
          }
          L.DomEvent.disableClickPropagation(bar);
          L.DomEvent.on(fill, "click", e => {
            L.DomEvent.preventDefault(e);
            fillHoles();
          });
          L.DomEvent.on(remove, "click", e => {
            L.DomEvent.preventDefault(e);
            setRemoveMode(!removing);
          });
          bars.push({fill, remove});
          return bar;
        };
        return control;
      });
      relabel();
    }

    function hideBars() {
      setRemoveMode(false);
      if (removeBars) removeBars();
      removeBars = null;
      bars = [];
    }

    const offClicks = mv.views.map(v => {
      const map = v.map;
      const onClick = e => {
        if (destroyed || !draw.editMode()) return;
        const pm = map.pm;
        if (pm && (pm.globalDrawModeEnabled() || pm.globalRemovalModeEnabled())) return; // Geoman's own clicks
        if (removing) {
          const out = removeHoleInFeatures(draw.features().features, [e.latlng.lng, e.latlng.lat]);
          if (out.removed) draw.commit(collection(out.features));
          return;
        }
        const target = e.originalEvent && e.originalEvent.target;
        const onDrawing = !!(target && target.closest && target.closest(ON_DRAWING));
        if (!onDrawing && draw.selection() !== null) draw.select(null);
      };
      map.on("click", onClick);
      return () => map.off("click", onClick);
    });

    const onKey = e => {
      if (e.key === "Escape" && removing) setRemoveMode(false);
    };
    if (doc) doc.addEventListener("keydown", onKey);

    const offEditMode = mv.on("editmodechange", ({on}) => (on ? showBars() : hideBars()));
    const offLabels = mv.on("labelschange", relabel);
    if (draw.editMode()) showBars();

    return {
      api: {
        fillHoles,
        setRemoveMode,
        removeMode: () => removing,
      },
      destroy() {
        if (destroyed) return;
        hideBars();
        destroyed = true;
        offEditMode();
        offLabels();
        offClicks.forEach(off => off());
        if (doc) doc.removeEventListener("keydown", onKey);
      },
    };
  },
};
