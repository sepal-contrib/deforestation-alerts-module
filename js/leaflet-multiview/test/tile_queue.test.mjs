// js/leaflet-multiview/test/tile_queue.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {createTileQueue} from "../src/core/tile_queue.js";

// A fake server: each fetch waits until the test answers it.
function harness(options = {}) {
  const pending = []; // {url, signal, resolve, reject}
  const timers = []; // {fn, ms, cleared}
  let clock = 0;
  const queue = createTileQueue({
    random: () => 0.5, // no jitter: delay = baseDelay * 2 ** attempt
    now: () => clock,
    fetch: (url, {signal} = {}) => new Promise((resolve, reject) => {
      const call = {url, signal, resolve, reject};
      if (signal) {
        signal.addEventListener("abort", () => {
          pending.splice(pending.indexOf(call), 1);
          reject(new Error("aborted"));
        });
      }
      pending.push(call);
    }),
    setTimeout: (fn, ms) => {
      const timer = {fn, ms, cleared: false};
      timers.push(timer);
      return timer;
    },
    clearTimeout: timer => { timer.cleared = true; },
    ...options,
  });
  const flush = () => new Promise(r => setImmediate(r));
  const answer = async (status, at = 0) => {
    const [call] = pending.splice(at, 1);
    call.resolve({ok: status >= 200 && status < 300, status, blob: async () => `blob:${call.url}`});
    await flush();
  };
  const fireTimers = async () => {
    for (const timer of timers.splice(0)) if (!timer.cleared) timer.fn();
    await flush();
  };
  return {queue, pending, timers, answer, fireTimers, flush, tick: ms => { clock += ms; }};
}

function job(queue, url) {
  const out = {loaded: null, error: null};
  out.handle = queue.fetch(url, blob => { out.loaded = blob; }, error => { out.error = error; });
  return out;
}

test("no more than the limit in flight; the rest start as slots free", async () => {
  const h = harness({limit: 2});
  const jobs = ["a", "b", "c"].map(u => job(h.queue, u));
  assert.deepEqual(h.pending.map(c => c.url), ["a", "b"]);
  await h.answer(200);
  assert.equal(jobs[0].loaded, "blob:a");
  assert.deepEqual(h.pending.map(c => c.url), ["b", "c"]);
});

test("a 429 is retried after a back-off that holds no slot, and halves the limit", async () => {
  const h = harness({limit: 4, baseDelay: 1000});
  const a = job(h.queue, "a");
  await h.answer(429);
  assert.equal(a.error, null);
  assert.equal(h.pending.length, 0);
  assert.deepEqual(h.timers.map(t => t.ms), [1000]);
  assert.equal(h.queue.stats().limit, 2);
  assert.equal(h.queue.stats().active, 0);
  await h.fireTimers();
  assert.deepEqual(h.pending.map(c => c.url), ["a"]);
  await h.answer(200);
  assert.equal(a.loaded, "blob:a");
});

test("the back-off doubles per attempt, and the last attempt reports the error", async () => {
  const h = harness({attempts: 3, baseDelay: 100});
  const a = job(h.queue, "a");
  await h.answer(503);
  assert.deepEqual(h.timers.map(t => t.ms), [100]);
  await h.fireTimers();
  await h.answer(503);
  assert.deepEqual(h.timers.map(t => t.ms), [200]);
  await h.fireTimers();
  await h.answer(503);
  assert.equal(a.error.status, 503);
  assert.equal(h.timers.length, 0);
});

test("a 404 fails at once", async () => {
  const h = harness();
  const a = job(h.queue, "a");
  await h.answer(404);
  assert.equal(a.error.status, 404);
  assert.equal(h.timers.length, 0);
});

test("a network error is retried", async () => {
  const h = harness();
  const a = job(h.queue, "a");
  h.pending.shift().reject(new TypeError("Failed to fetch"));
  await h.flush();
  assert.equal(a.error, null);
  await h.fireTimers();
  await h.answer(200);
  assert.equal(a.loaded, "blob:a");
});

test("the limit falls once per second at most, and climbs back with successes", async () => {
  const h = harness({limit: 8, minLimit: 2, maxLimit: 8});
  for (const u of ["a", "b", "c"]) job(h.queue, u);
  await h.answer(429);
  await h.answer(429);
  assert.equal(h.queue.stats().limit, 4); // the second 429 came within the second
  h.tick(1000);
  await h.answer(429);
  assert.equal(h.queue.stats().limit, 2);
  await h.fireTimers();
  await h.answer(200);
  assert.equal(h.queue.stats().limit, 2.5);
});

test("cancel drops a waiting job, aborts one in flight and stops a pending retry", async () => {
  const h = harness({limit: 1});
  const a = job(h.queue, "a");
  const b = job(h.queue, "b");
  b.handle.cancel();
  a.handle.cancel(); // in flight: aborted
  await h.flush();
  assert.equal(h.pending.length, 0);
  assert.equal(h.queue.stats().active, 0);
  assert.equal(a.error, null);
  assert.equal(b.loaded, null);

  const c = job(h.queue, "c");
  await h.answer(429);
  c.handle.cancel();
  assert.equal(h.timers[0].cleared, true);
  await h.fireTimers();
  assert.equal(h.pending.length, 0);
  assert.equal(c.error, null);
});
