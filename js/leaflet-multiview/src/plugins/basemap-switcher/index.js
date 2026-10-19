// basemapSwitcher: a row of basemap thumbnails at the bottom left of every
// view. One basemap shows at a time, the same in every view (mv.setBasemap).
//
// options.basemaps: [{id, name, url, attribution?, maxZoom?, thumbnail?}],
// in display order; options.selected: the id shown first (else the first).
// A click on another thumbnail switches every view and emits "basemapchange"
// {id}; api.select(id) switches without an event (a host restoring a choice).
// Labels: "basemapSwitcher.title" for the row, "basemapSwitcher.<id>" for a
// basemap's name (else its ``name``).

export const basemapSwitcher = {
  name: "basemapSwitcher",
  requires: [],
  defaults: {basemaps: [], selected: null, views: "all"},
  labels: {title: "Basemap"},
  install(mv, options) {
    const L = mv.L;
    const basemaps = (Array.isArray(options.basemaps) ? options.basemaps : []).filter(b => b && b.id && b.url);
    const byId = new Map(basemaps.map(b => [b.id, b]));
    const rows = []; // {row, buttons: Map<id, {button, name}>}
    let current = null;

    function nameOf(basemap) {
      const text = mv.label("basemapSwitcher", basemap.id);
      return text === basemap.id ? basemap.name || basemap.id : text;
    }

    function refresh() {
      for (const {buttons} of rows) {
        for (const [id, {button}] of buttons) {
          L.DomUtil[id === current ? "addClass" : "removeClass"](button, "mv-basemap--on");
          button.setAttribute("aria-pressed", String(id === current));
        }
      }
    }

    function relabel() {
      const title = mv.label("basemapSwitcher", "title");
      for (const {row, buttons} of rows) {
        row.setAttribute("aria-label", title);
        for (const [id, {button, name}] of buttons) {
          const text = nameOf(byId.get(id));
          name.textContent = text;
          button.title = text;
        }
      }
    }

    function select(id) {
      const basemap = byId.get(id);
      if (!basemap || id === current) return false;
      current = id;
      mv.setBasemap({url: basemap.url, attribution: basemap.attribution, maxZoom: basemap.maxZoom});
      refresh();
      return true;
    }

    let removeControls = () => {};
    if (basemaps.length) {
      removeControls = mv.addControl(options.views, () => {
        const control = L.control({position: "bottomleft"});
        control.onAdd = () => {
          const row = L.DomUtil.create("div", "mv-basemaps");
          row.setAttribute("role", "group");
          L.DomEvent.disableClickPropagation(row);
          L.DomEvent.disableScrollPropagation(row);
          const buttons = new Map();
          for (const basemap of basemaps) {
            const button = L.DomUtil.create("button", "mv-basemap", row);
            button.type = "button";
            button.dataset.id = basemap.id;
            if (basemap.thumbnail) {
              const img = L.DomUtil.create("img", "mv-basemap-thumb", button);
              img.src = basemap.thumbnail;
              img.alt = "";
              img.draggable = false;
            }
            const name = L.DomUtil.create("span", "mv-basemap-name", button);
            L.DomEvent.on(button, "click", e => {
              L.DomEvent.preventDefault(e);
              if (select(basemap.id)) mv.emit("basemapchange", {id: basemap.id});
            });
            buttons.set(basemap.id, {button, name});
          }
          rows.push({row, buttons});
          return row;
        };
        return control;
      });
      select(byId.has(options.selected) ? options.selected : basemaps[0].id);
      relabel();
    }
    const offLabels = mv.on("labelschange", relabel);

    return {
      api: {
        select(id) {
          select(id);
        },
        selected: () => current,
      },
      destroy() {
        offLabels();
        removeControls();
      },
    };
  },
};
