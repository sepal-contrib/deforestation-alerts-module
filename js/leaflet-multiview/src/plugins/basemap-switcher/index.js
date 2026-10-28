// basemapSwitcher: a basemap thumbnail, over a metric scale bar, at the bottom
// left of every view. One
// basemap shows at a time, the same in every view (mv.setBasemap).
//
// Collapsed, a view shows only the selected basemap's thumbnail; the row of
// all of them opens while a mouse is over it, or on a click (tap, Enter) on
// that thumbnail, and closes once a basemap is picked, the mouse leaves, the
// focus moves out or on Escape. Open, the selected one stays first, so the
// thumbnail under the pointer does not move.
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
    const rows = []; // {row, buttons: Map<id, {button, name}>, setOpen}
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
      for (const {setOpen} of rows) setOpen(false);
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
      // Added first: Leaflet stacks later bottom controls above, so the scale sits under the row.
      const removeScale = mv.addControl(options.views, () => L.control.scale({position: "bottomleft", imperial: false}));
      const removeRow = mv.addControl(options.views, () => {
        const control = L.control({position: "bottomleft"});
        control.onAdd = () => {
          const row = L.DomUtil.create("div", "mv-basemaps");
          row.setAttribute("role", "group");
          L.DomEvent.disableClickPropagation(row);
          L.DomEvent.disableScrollPropagation(row);
          const buttons = new Map();
          let open = false;
          const setOpen = value => {
            open = value;
            L.DomUtil[open ? "addClass" : "removeClass"](row, "mv-basemaps--open");
            for (const [id, {button}] of buttons) {
              if (id === current) button.setAttribute("aria-expanded", String(open));
              else button.removeAttribute("aria-expanded");
            }
          };
          // A mouse opens it by hovering; a touch's emulated hover would open it
          // just before the tap that toggles it, so only a real mouse counts.
          row.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") setOpen(true); });
          row.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") setOpen(false); });
          row.addEventListener("focusout", e => { if (!row.contains(e.relatedTarget)) setOpen(false); });
          row.addEventListener("keydown", e => {
            if (e.key === "Escape" && open) {
              setOpen(false);
              const on = buttons.get(current);
              if (on) on.button.focus();
            }
          });
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
              if (basemap.id === current) setOpen(!open);
              else if (select(basemap.id)) mv.emit("basemapchange", {id: basemap.id});
            });
            buttons.set(basemap.id, {button, name});
          }
          rows.push({row, buttons, setOpen});
          return row;
        };
        return control;
      });
      removeControls = () => {
        removeRow();
        removeScale();
      };
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
