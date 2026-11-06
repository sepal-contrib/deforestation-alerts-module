// js/app/rail_collapse.js
// App plugin, installed through the adapter's ``always`` list. A click on a
// view's map collapses MapApp's left rail, as a click on the workflow's main
// map does. The rail state lives in pysepal's Vue (MapApp.vue: the
// #map-container click handler, which respects the pin), so replay that click:
// the container is hidden on the analysis screen, but click() still fires.
export const railCollapse = {
  name: "railCollapse",
  requires: [],
  defaults: {},
  labels: {},
  install(mv) {
    const collapse = () => document.getElementById("map-container")?.click();
    for (const view of mv.views) view.map.on("click", collapse);
    return {
      api: {},
      destroy() {
        for (const view of mv.views) view.map.off("click", collapse);
      },
    };
  },
};
