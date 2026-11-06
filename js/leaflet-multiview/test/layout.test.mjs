import test from "node:test";
import assert from "node:assert/strict";
import {buildLayout, gridColumns} from "../src/core/layout.js";

// Just enough DOM for buildLayout.
function fakeElement(tag) {
  const classes = new Set();
  return {
    tagName: tag.toUpperCase(),
    children: [],
    style: {},
    dataset: {},
    textContent: "",
    parentNode: null,
    classList: {
      add: (...names) => names.forEach(n => classes.add(n)),
      contains: n => classes.has(n),
    },
    get className() {
      return [...classes].join(" ");
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
  };
}
const fakeDocument = {createElement: fakeElement};

test("gridColumns is ceil(sqrt(n))", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 9, 10].map(gridColumns), [1, 2, 2, 2, 3, 3, 4]);
});

test("a row: the root classes, one .mv-view per view with a header and a map", () => {
  const el = fakeElement("div");
  const cells = buildLayout(el, [{id: "before", title: "Before"}, {id: "after"}], "row", fakeDocument);
  assert.equal(el.className, "mv-root mv-layout-row");
  const body = el.children[0];
  assert.equal(body.className, "mv-views");
  assert.equal(body.style.gridTemplateColumns, undefined);
  assert.deepEqual(cells.map(c => c.id), ["before", "after"]);
  for (const cell of cells) {
    assert.equal(cell.container.className, "mv-view");
    assert.equal(cell.container.dataset.view, cell.id);
    assert.deepEqual(cell.container.children, [cell.header, cell.mapEl]);
    assert.equal(cell.header.className, "mv-header");
    assert.equal(cell.mapEl.className, "mv-map");
    assert.equal(cell.container.parentNode, body);
  }
  assert.equal(cells[0].header.children[0].className, "mv-title");
  assert.equal(cells[0].header.children[0].textContent, "Before");
  assert.equal(cells[1].header.children.length, 0, "no title: the header stays empty (and collapses)");
});

test("a grid: ceil(sqrt(n)) columns, and the last view spans what its row leaves", () => {
  const el = fakeElement("div");
  const cells = buildLayout(el, [{id: "a"}, {id: "b"}, {id: "c"}], "grid", fakeDocument);
  assert.equal(el.className, "mv-root mv-layout-grid");
  assert.equal(el.children[0].style.gridTemplateColumns, "repeat(2, minmax(0, 1fr))");
  assert.equal(cells[2].container.style.gridColumn, "span 2");
  assert.equal(cells[0].container.style.gridColumn, undefined);

  const five = buildLayout(fakeElement("div"), ["a", "b", "c", "d", "e"].map(id => ({id})), "grid", fakeDocument);
  assert.equal(five[4].container.style.gridColumn, "span 2");
  const four = buildLayout(fakeElement("div"), ["a", "b", "c", "d"].map(id => ({id})), "grid", fakeDocument);
  assert.equal(four[3].container.style.gridColumn, undefined, "a full last row spans nothing");
});
