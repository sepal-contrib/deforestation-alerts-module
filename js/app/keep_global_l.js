// js/app/keep_global_l.js
// Evaluated before Leaflet (leaflet_shim.js imports it first): the page's own
// L -- jupyter-leaflet's, on the workflow map -- before ours overwrites it.
export const hadL = Object.prototype.hasOwnProperty.call(globalThis, "L");
export const outerL = globalThis.L;
