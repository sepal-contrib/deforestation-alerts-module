// js/leaflet-multiview/src/core/tile_queue.js
// Tiles from a rate-limited server (Earth Engine: a project in restricted
// mode answers a burst with 429), fetched through one queue for the page.
//
// - At most ``limit`` requests are in flight. The limit adapts: a 429 halves
//   it (once per second at most, down to ``minLimit``), each success raises it
//   by 1/limit (up to ``maxLimit``), so the queue settles at what the server
//   accepts.
// - A 429, a 5xx or a network error is retried after a back-off (``baseDelay``
//   doubled per attempt, capped at ``maxDelay``, with jitter), up to
//   ``attempts`` tries; a waiting retry holds no slot. Any other status fails
//   at once.
// - ``cancel()`` drops a waiting job and aborts one in flight; its callbacks
//   never run.
//
// ``fetch``, ``setTimeout``, ``random`` and ``now`` are injectable (tests).
// Pure: no Leaflet, no DOM.

const RETRY = new Set([429, 500, 502, 503, 504]);

export function createTileQueue({
  limit = 6,
  minLimit = 2,
  maxLimit = 8,
  attempts = 6,
  baseDelay = 1000,
  maxDelay = 30000,
  fetch: fetchFn = (...a) => globalThis.fetch(...a),
  setTimeout: later = (...a) => globalThis.setTimeout(...a),
  clearTimeout: cancelLater = (...a) => globalThis.clearTimeout(...a),
  random = Math.random,
  now = () => Date.now(),
} = {}) {
  let current = limit;
  let lastCut = -Infinity;
  let active = 0;
  const waiting = []; // jobs ready to start, in arrival order

  const delayFor = attempt => Math.min(maxDelay, baseDelay * 2 ** attempt) * (0.5 + random());

  function slow() {
    if (now() - lastCut < 1000) return;
    lastCut = now();
    current = Math.max(minLimit, Math.floor(current / 2));
  }

  function pump() {
    while (active < Math.floor(current) && waiting.length) start(waiting.shift());
  }

  function retryOrFail(job, error) {
    job.attempt += 1;
    if (job.attempt >= attempts) {
      job.done = true;
      job.onError(error);
      return;
    }
    job.timer = later(() => {
      job.timer = null;
      if (job.done) return;
      waiting.push(job);
      pump();
    }, delayFor(job.attempt - 1));
  }

  function start(job) {
    active += 1;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    job.running = controller;
    let released = false;
    const release = () => { // the slot, once per attempt
      if (released) return;
      released = true;
      active -= 1;
      job.running = null;
      pump();
    };
    fetchFn(job.url, controller ? {signal: controller.signal} : {})
      .then(async response => {
        if (!response.ok) {
          release();
          if (job.done) return;
          const error = new Error(`tile ${response.status}`);
          error.status = response.status;
          if (response.status === 429) slow();
          if (RETRY.has(response.status)) retryOrFail(job, error);
          else {
            job.done = true;
            job.onError(error);
          }
          return;
        }
        const blob = await response.blob();
        release();
        if (job.done) return;
        current = Math.min(maxLimit, current + 1 / current);
        job.done = true;
        job.onLoad(blob);
      })
      .catch(error => { // a network error, or the abort of a cancelled job
        release();
        if (!job.done) retryOrFail(job, error);
      });
  }

  return {
    // ``onLoad(blob)`` or ``onError(error)`` runs once, unless cancelled.
    fetch(url, onLoad, onError) {
      const job = {url, onLoad, onError, attempt: 0, done: false, running: null, timer: null};
      waiting.push(job);
      pump();
      return {
        cancel() {
          if (job.done) return;
          job.done = true;
          const at = waiting.indexOf(job);
          if (at >= 0) waiting.splice(at, 1);
          if (job.timer !== null) cancelLater(job.timer);
          if (job.running) job.running.abort();
        },
      };
    },
    stats: () => ({limit: current, active, waiting: waiting.length}),
  };
}
