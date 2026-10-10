// The fitBounds plugin <-> the widget's traits:
// - ``fit_bounds_views`` (an option: a view id, a list of ids or "all"), read
//   once at install; a change takes effect at the next re-render;
// - ``fit_bounds_bounds`` ([[south, west], [north, east]] or null) -> setBounds.

export function options(model) {
  const views = model.get("fit_bounds_views");
  return {views: views == null ? "all" : views};
}

export function bind({model, api}) {
  const apply = () => api.setBounds(model.get("fit_bounds_bounds") ?? null);
  apply(); // before the listener: a throw here leaves nothing to clean up
  model.on("change:fit_bounds_bounds", apply);
  return () => model.off("change:fit_bounds_bounds", apply);
}
