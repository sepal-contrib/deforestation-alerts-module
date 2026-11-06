// The pick plugin: a map click on any view emits "pick" with {view, lat, lng,
// zoom}. What a click picks is the host's business; the widget binding
// (widget.js) tags the event and writes it to the ``pick_event`` trait.
//
// A click is not a pick while the draw plugin is in edit mode, or while
// Geoman's own draw or removal mode is on (those clicks belong to the tool).
// A click on a drawing polygon or a layer's own handler still reaches the map.

// Whether a map click picks, given the draw plugin's edit mode and the map's
// Geoman (``map.pm``, absent when Geoman is not loaded).
export function shouldPick(editMode, pm) {
  if (editMode) return false;
  if (pm && (pm.globalDrawModeEnabled() || pm.globalRemovalModeEnabled())) return false;
  return true;
}

export const pick = {
  name: "pick",
  requires: ["draw"],
  defaults: {},

  install(mv) {
    const draw = mv.plugin("draw");
    let destroyed = false;

    const offClicks = mv.views.map(v => {
      const map = v.map;
      const onClick = e => {
        if (destroyed || !shouldPick(draw.editMode(), map.pm)) return;
        mv.emit("pick", {view: v.id, lat: e.latlng.lat, lng: e.latlng.lng, zoom: map.getZoom()});
      };
      map.on("click", onClick);
      return () => map.off("click", onClick);
    });

    return {
      api: {},
      destroy() {
        if (destroyed) return;
        destroyed = true;
        offClicks.forEach(off => off());
      },
    };
  },
};
