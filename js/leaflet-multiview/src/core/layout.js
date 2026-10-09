// The multiview's DOM: a root holding the views in a row or an auto grid.
//
//   el.mv-root.mv-layout-<layout>
//     div.mv-views                      (flex row, or a CSS grid)
//       div.mv-view[data-view=<id>]
//         div.mv-header                 (the title; collapsed while empty)
//         div.mv-map                    (the Leaflet container)
//
// In a grid the last view spans the columns left over in its row, so 3 views
// are two cells on top and one wide cell below.

export const gridColumns = n => Math.ceil(Math.sqrt(n));

export function buildLayout(el, views, layout, document) {
  el.classList.add("mv-root", `mv-layout-${layout}`);
  const body = document.createElement("div");
  body.classList.add("mv-views");
  const cols = gridColumns(views.length);
  if (layout === "grid") body.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  el.appendChild(body);

  const cells = views.map(view => {
    const container = document.createElement("div");
    container.classList.add("mv-view");
    container.dataset.view = view.id;
    const header = document.createElement("div");
    header.classList.add("mv-header");
    if (view.title) {
      const title = document.createElement("span");
      title.classList.add("mv-title");
      title.textContent = view.title;
      header.appendChild(title);
    }
    const mapEl = document.createElement("div");
    mapEl.classList.add("mv-map");
    container.appendChild(header);
    container.appendChild(mapEl);
    body.appendChild(container);
    return {id: view.id, container, header, mapEl};
  });

  const rest = views.length % cols;
  if (layout === "grid" && rest) cells[cells.length - 1].container.style.gridColumn = `span ${cols - rest + 1}`;
  return cells;
}
