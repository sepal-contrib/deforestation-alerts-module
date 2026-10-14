// The draw plugin <-> the widget's traits (spec §5.2):
// - ``draw``: the envelope {fc, gen, seq, origin}, written by both sides.
//   * A host write (origin "host") with a ``gen`` above the one adopted
//     replaces the drawing (setFeatures) and is adopted: ``seq`` restarts.
//     Anything else arriving on the trait -- our own write, its echo, an
//     older host write -- is ignored.
//   * Every "drawingchange" (a finished user edit) is written as
//     {fc, gen: <adopted>, seq: <previous + 1>, origin: "user"}. Python
//     rejects it when its ``gen`` is behind and re-asserts its own drawing as
//     a newer host write, which lands here as above.
// - ``draw_edit_mode`` (Python -> JS) -> setEditMode.

const EMPTY = {type: "FeatureCollection", features: []};

export function bind({model, api, mv}) {
  const first = model.get("draw") || {};
  let gen = Number(first.gen) || 0;
  let seq = Number(first.seq) || 0;
  api.setFeatures(first.fc || EMPTY);
  api.setEditMode(!!model.get("draw_edit_mode"));

  const onDraw = () => {
    const env = model.get("draw") || {};
    if (env.origin !== "host" || !(Number(env.gen) > gen)) return;
    gen = Number(env.gen);
    seq = 0;
    api.setFeatures(env.fc || EMPTY);
  };
  const onEditMode = () => api.setEditMode(!!model.get("draw_edit_mode"));
  model.on("change:draw", onDraw);
  model.on("change:draw_edit_mode", onEditMode);
  const offChange = mv.on("drawingchange", ({features}) => {
    seq += 1;
    model.set("draw", {fc: features, gen, seq, origin: "user"});
    model.save_changes();
  });
  return () => {
    model.off("change:draw", onDraw);
    model.off("change:draw_edit_mode", onEditMode);
    offChange();
  };
}
