import test from "node:test";
import assert from "node:assert/strict";
import {createEmitter} from "../src/core/events.js";

function captureConsoleError(fn) {
  const calls = [];
  const original = console.error;
  console.error = (...args) => calls.push(args);
  try {
    fn();
  } finally {
    console.error = original;
  }
  return calls;
}

test("on delivers the payload; the returned function and off both unsubscribe", () => {
  const e = createEmitter();
  const seen = [];
  const offA = e.on("x", p => seen.push(["a", p]));
  const b = p => seen.push(["b", p]);
  e.on("x", b);
  e.emit("x", 1);
  offA();
  e.off("x", b);
  e.emit("x", 2);
  assert.deepEqual(seen, [["a", 1], ["b", 1]]);
});

test("emitting an event nobody listens to is a no-op", () => {
  const e = createEmitter();
  e.emit("nobody", {});
  e.off("nobody", () => {});
});

test("a listener that throws is logged and the others still run", () => {
  const e = createEmitter();
  const seen = [];
  e.on("x", () => {
    throw new Error("boom");
  });
  e.on("x", p => seen.push(p));
  const errors = captureConsoleError(() => e.emit("x", 7));
  assert.deepEqual(seen, [7]);
  assert.equal(errors.length, 1);
  assert.match(String(errors[0][0]), /"x" listener failed/);
  assert.equal(errors[0][1].message, "boom");
});

test("a listener removed by an earlier one in the same emit is not called", () => {
  const e = createEmitter();
  const seen = [];
  const second = () => seen.push("second");
  e.on("x", () => {
    seen.push("first");
    e.off("x", second);
  });
  e.on("x", second);
  e.emit("x");
  assert.deepEqual(seen, ["first"]);
});

test("a listener must be a function", () => {
  assert.throws(() => createEmitter().on("x", null), TypeError);
});
