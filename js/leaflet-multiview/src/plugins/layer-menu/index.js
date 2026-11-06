// layerMenu: a menu of each view's layers, listed by name, in every view.
//
// It reuses the CSS classes of Leaflet's layers control, so it looks like
// L.control.layers (an icon at the top right that expands on hover), plus a
// title row. It is built by hand so that only a click on a checkbox counts as a
// toggle: L.control.layers also reports the adds and removes the core makes.
// A toggle sets a per-view override (mv.setLayerOverride) and emits
// "layertoggle" {view, id, on}.

//
// Sections: a spec's meta.menuSection puts it under that section's heading,
// labelled "layerMenu.section.<id>" (else the id). Layers with none come
// first, with no heading; then the sections of options.sections in that
// order, then any others in the order they first appear. A section with no
// layer in the view is not shown.

export const layerMenu = {
  name: "layerMenu",
  requires: [],
  defaults: {sections: []},
  labels: {title: "Layers"},
  install(mv, options) {
    const L = mv.L;
    const menus = [];
    const order = Array.isArray(options.sections) ? options.sections : [];

    function sectionLabel(section) {
      const key = `section.${section}`;
      const text = mv.label("layerMenu", key);
      return text === key ? section : text;
    }

    // [[section or "", specs]] in display order, each non-empty.
    function sectioned(specs) {
      const groups = new Map([["", []], ...order.map(id => [id, []])]);
      for (const spec of specs) {
        const section = (spec.meta && spec.meta.menuSection) || "";
        if (!groups.has(section)) groups.set(section, []);
        groups.get(section).push(spec);
      }
      return [...groups].filter(([, members]) => members.length);
    }

    function createMenu(view) {
      const container = L.DomUtil.create("div", "leaflet-control-layers mv-layer-menu");
      container.setAttribute("aria-haspopup", "true");
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.disableScrollPropagation(container);
      const toggle = L.DomUtil.create("a", "leaflet-control-layers-toggle", container);
      toggle.href = "#";
      toggle.setAttribute("role", "button");
      const list = L.DomUtil.create("section", "leaflet-control-layers-list", container);
      const title = L.DomUtil.create("div", "mv-layers-title", list);
      const overlays = L.DomUtil.create("div", "leaflet-control-layers-overlays", list);

      // Leaflet 1.9.4's L.Control.Layers expand/collapse, ported: a list taller
      // than the map scrolls, and the touch that opens the menu cannot also
      // tick the checkbox under the finger.
      function expand() {
        L.DomUtil.addClass(container, "leaflet-control-layers-expanded");
        list.style.height = null;
        const acceptableHeight = view.map.getSize().y - (container.offsetTop + 50);
        if (acceptableHeight < list.clientHeight) {
          L.DomUtil.addClass(list, "leaflet-control-layers-scrollbar");
          list.style.height = `${acceptableHeight}px`;
        } else {
          L.DomUtil.removeClass(list, "leaflet-control-layers-scrollbar");
        }
      }
      function expandSafely() {
        L.DomEvent.on(list, "click", L.DomEvent.preventDefault);
        expand();
        setTimeout(() => L.DomEvent.off(list, "click", L.DomEvent.preventDefault));
      }
      const collapse = () => L.DomUtil.removeClass(container, "leaflet-control-layers-expanded");
      L.DomEvent.on(container, "mouseenter", expandSafely);
      L.DomEvent.on(container, "mouseleave", collapse);
      L.DomEvent.on(toggle, "keydown", e => {
        if (e.keyCode === 13) expandSafely();
      });
      L.DomEvent.on(toggle, "click", e => {
        L.DomEvent.preventDefault(e);
        expandSafely();
      });

      // Ticks follow what the map shows: an override, a rule or spec.visible.
      function refreshChecks() {
        for (const input of overlays.querySelectorAll("input[data-id]")) {
          const layer = mv.layer(view.id, input.dataset.id);
          input.checked = !!layer && view.map.hasLayer(layer);
        }
      }

      function relabel() {
        const text = mv.label("layerMenu", "title");
        title.textContent = text;
        toggle.title = text;
        toggle.setAttribute("aria-label", text);
        for (const heading of overlays.querySelectorAll(".mv-layers-section")) {
          heading.textContent = sectionLabel(heading.dataset.section);
        }
      }

      // The view's layers by name, in spec order within each section.
      function render() {
        overlays.textContent = "";
        const specs = mv.layers().filter(spec => mv.layer(view.id, spec.id) !== undefined);
        L.DomUtil[specs.length ? "removeClass" : "addClass"](container, "mv-layer-menu--empty");
        sectioned(specs).forEach(([section, members], i) => {
          if (i > 0) L.DomUtil.create("div", "leaflet-control-layers-separator", overlays);
          if (section) {
            const heading = L.DomUtil.create("div", "mv-layers-section", overlays);
            heading.dataset.section = section;
            heading.textContent = sectionLabel(section);
          }
          for (const spec of members) {
            const label = L.DomUtil.create("label", "", overlays);
            const row = L.DomUtil.create("span", "", label);
            const input = L.DomUtil.create("input", "leaflet-control-layers-selector", row);
            input.type = "checkbox";
            input.dataset.id = spec.id;
            const name = L.DomUtil.create("span", "", row);
            name.textContent = ` ${spec.name || spec.id}`;
          }
        });
        refreshChecks();
      }

      // A user's click (a "change" event): never the core's own adds/removes.
      L.DomEvent.on(overlays, "change", e => {
        const input = e.target;
        if (!input || !input.dataset || input.dataset.id === undefined) return;
        const id = input.dataset.id;
        const on = input.checked;
        mv.setLayerOverride(view.id, id, on);
        refreshChecks(); // a deny rule may keep it hidden
        mv.emit("layertoggle", {view: view.id, id, on});
      });
      view.map.on("click", collapse);
      view.map.on("layeradd layerremove", refreshChecks);

      relabel();
      render();
      return {
        container,
        relabel,
        render,
        detach() {
          view.map.off("click", collapse);
          view.map.off("layeradd layerremove", refreshChecks);
        },
      };
    }

    const removeControls = mv.addControl("all", view => {
      const menu = createMenu(view);
      menus.push(menu);
      const control = L.control({position: "topright"});
      control.onAdd = () => menu.container;
      return control;
    });
    const offs = [
      mv.on("layerschange", () => menus.forEach(m => m.render())),
      mv.on("labelschange", () => menus.forEach(m => m.relabel())),
    ];

    return {
      api: {},
      destroy() {
        offs.forEach(off => off());
        menus.forEach(m => m.detach());
        removeControls();
      },
    };
  },
};
