// The layerMenu plugin <-> the widget's ``layer_menu_toggles`` trait
// ({viewId: {layerId: bool}}, frontend-owned; Python may seed it).
//
// - At bind time the trait's toggles become core overrides, so a re-render
//   keeps the viewer's menu choices.
// - Every toggle writes the core's override map back.
// - So does every "layerschange" that pruned an override (its layer left that
//   view): pruneOverrides returns the same object when nothing was dropped, so
//   a new object means there is something to write. Without this, Python would
//   keep the stale entry and the next re-render would resurrect it.

export function bind({model, mv}) {
  const toggles = model.get("layer_menu_toggles") || {};
  for (const [viewId, byId] of Object.entries(toggles)) {
    for (const [layerId, on] of Object.entries(byId || {})) {
      if (typeof on === "boolean") mv.setLayerOverride(viewId, layerId, on);
    }
  }
  let written = mv.overrides();
  const write = () => {
    written = mv.overrides();
    model.set("layer_menu_toggles", written);
    model.save_changes();
  };
  const offToggle = mv.on("layertoggle", ({view, id, on}) => {
    mv.setLayerOverride(view, id, on); // a no-op when the plugin has set it already
    write();
  });
  const offLayers = mv.on("layerschange", () => {
    if (mv.overrides() !== written) write();
  });
  return () => {
    offToggle();
    offLayers();
  };
}
