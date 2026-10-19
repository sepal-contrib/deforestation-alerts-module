// The basemapSwitcher plugin <-> the widget's traits:
// - ``basemap_switcher_basemaps`` (an option: the basemaps, read once at
//   install; a change takes effect at the next re-render);
// - ``basemap_switcher_selected`` (the shown basemap's id): read at install,
//   written on a viewer's click, applied when Python changes it.

export function options(model) {
  return {
    basemaps: model.get("basemap_switcher_basemaps") || [],
    selected: model.get("basemap_switcher_selected") || null,
  };
}

export function bind({model, api, mv}) {
  const apply = () => api.select(model.get("basemap_switcher_selected"));
  model.on("change:basemap_switcher_selected", apply);
  const offChange = mv.on("basemapchange", ({id}) => {
    model.set("basemap_switcher_selected", id);
    model.save_changes();
  });
  return () => {
    model.off("change:basemap_switcher_selected", apply);
    offChange();
  };
}
