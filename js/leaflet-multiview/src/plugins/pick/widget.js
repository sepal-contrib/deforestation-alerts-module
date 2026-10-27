// The pick plugin <-> the widget's traits:
// - ``pick_tag`` (Python -> browser): read at each click, so Python can tag
//   the picks it is waiting for;
// - ``pick_event`` (browser -> Python): ``{tag, view, lat, lng, zoom, seq}``,
//   ``seq`` counting up from 1 so two identical clicks are two changes.

export function bind({model, mv}) {
  let seq = 0;
  return mv.on("pick", payload => {
    seq += 1;
    model.set("pick_event", {tag: model.get("pick_tag") ?? "", ...payload, seq});
    model.save_changes();
  });
}
