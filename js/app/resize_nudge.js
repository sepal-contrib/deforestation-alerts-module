// js/app/resize_nudge.js
// App plugin (spec §4.6), installed through the adapter's ``always`` list.
// When the views are hidden -- the reviewer left the analysis screen -- the
// workflow's main map is shown in their place. Leaflet re-measures a map on a
// window "resize", so dispatch one (Review Focus #5). The multiview's own maps
// do not listen to it: the core owns their re-measuring.
export const resizeNudge = {
  name: "resizeNudge",
  requires: [],
  defaults: {},
  labels: {},
  install(mv) {
    const off = mv.on("visibilitychange", ({shown}) => {
      if (!shown) window.dispatchEvent(new Event("resize"));
    });
    return {api: {}, destroy: off};
  },
};
