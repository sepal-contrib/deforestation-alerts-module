// Panes the package creates, kept in z order in the DOM too.
//
// A host may flatten every pane's z-index (pysepal's base.css:
// `.leaflet-pane { z-index: 2 !important }`); panes then stack by DOM order.
// So a new pane is inserted into the map pane before the first pane whose
// intended z-index is higher, and keeps its inline z-index for hosts without
// that rule. Leaflet's own panes get their z-index from leaflet.css, not
// inline, so their values are listed here.

const LEAFLET_PANE_Z = {tilePane: 200, overlayPane: 400, shadowPane: 500, markerPane: 600, tooltipPane: 650, popupPane: 700};

function intendedZ(map, el) {
  if (el.style.zIndex !== "") return Number(el.style.zIndex);
  for (const [name, pane] of Object.entries(map.getPanes())) {
    if (pane === el) return LEAFLET_PANE_Z[name];
  }
  return undefined;
}

// Creates (or reuses) the pane ``name`` with z-index ``zIndex`` and puts it in
// its z-ordered place among the map pane's children.
export function createOrderedPane(map, name, zIndex) {
  const pane = map.getPane(name) || map.createPane(name);
  pane.style.zIndex = String(zIndex);
  const mapPane = map.getPanes().mapPane;
  let before = null;
  for (const child of mapPane.children) {
    if (child === pane) continue;
    const z = intendedZ(map, child);
    if (z !== undefined && z > zIndex) {
      before = child;
      break;
    }
  }
  if (pane.nextSibling !== before || pane.parentNode !== mapPane) mapPane.insertBefore(pane, before);
  return pane;
}
