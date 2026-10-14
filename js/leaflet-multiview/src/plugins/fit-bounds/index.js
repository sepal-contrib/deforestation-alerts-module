// fitBounds: a "zoom to area" button (⌖) at the top left of the chosen views.
// It fits the last bounds given to setBounds() and is hidden while there are
// none. The fit is a user move: every view follows, and "viewchange" fires.

export const fitBounds = {
  name: "fitBounds",
  requires: [],
  defaults: {views: "all"},
  labels: {button: "Zoom to area"},
  install(mv, options) {
    const L = mv.L;
    const buttons = []; // {bar, link}
    let current = null; // [[south, west], [north, east]] or null

    function refresh() {
      for (const {bar} of buttons) L.DomUtil[current ? "removeClass" : "addClass"](bar, "mv-fit-bounds--none");
    }

    function relabel() {
      const text = mv.label("fitBounds", "button");
      for (const {link} of buttons) {
        link.title = text;
        link.setAttribute("aria-label", text);
      }
    }

    const removeControls = mv.addControl(options.views, () => {
      const control = L.control({position: "topleft"});
      control.onAdd = () => {
        const bar = L.DomUtil.create("div", "leaflet-bar mv-fit-bounds-bar");
        const link = L.DomUtil.create("a", "mv-fit-bounds", bar);
        link.href = "#";
        link.setAttribute("role", "button");
        link.textContent = "⌖";
        L.DomEvent.disableClickPropagation(bar);
        L.DomEvent.on(link, "click", e => {
          L.DomEvent.preventDefault(e);
          if (current) mv.fitBounds(current);
        });
        buttons.push({bar, link});
        return bar;
      };
      return control;
    });
    relabel();
    refresh();
    const offLabels = mv.on("labelschange", relabel);

    return {
      api: {
        setBounds(bounds) {
          current = bounds || null;
          refresh();
        },
        bounds: () => current,
      },
      destroy() {
        offLabels();
        removeControls();
      },
    };
  },
};
