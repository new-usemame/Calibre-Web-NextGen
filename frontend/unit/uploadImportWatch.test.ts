import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAX_WATCH_MS,
  POLL_FAST_MS,
  POLL_MEDIUM_MS,
  POLL_SLOW_MS,
  LARGE_BATCH,
  REFRESH_THROTTLE_LARGE_MS,
  REFRESH_THROTTLE_SMALL_MS,
  STALL_MS,
  observeLibraryTotal,
  pollIntervalMs,
  startImportWatch,
  type ImportWatch,
} from '../src/lib/uploadImportWatch.ts';

const T0 = 1_000_000;

// Feed one reading and hand back the new watch plus whether views refresh.
function step(watch: ImportWatch, total: number, now: number) {
  return observeLibraryTotal(watch, total, now);
}

test('a single upload is followed until its book appears, then refreshes once and stops', () => {
  let w = startImportWatch(null, 1, 500, T0);

  // Ingest has not run yet: nothing to show, keep watching.
  let r = step(w, 500, T0 + 3_000);
  assert.equal(r.refresh, false);
  assert.equal(r.watch.status, 'watching');
  w = r.watch;

  r = step(w, 501, T0 + 6_000);
  assert.equal(r.refresh, true);
  assert.equal(r.watch.status, 'done');
  assert.equal(r.watch.imported, 1);
  w = r.watch;

  // A finished watch ignores further readings, so no stray refreshes.
  r = step(w, 502, T0 + 9_000);
  assert.equal(r.refresh, false);
  assert.equal(r.watch, w);
});

test('a large batch is followed to the end without refreshing on every probe', () => {
  const files = 300;
  let w = startImportWatch(null, files, 1_000, T0);
  let now = T0;
  let total = 1_000;
  let refreshes = 0;
  let probes = 0;

  // One book lands every 12 seconds, so the whole batch takes an hour.
  const ARRIVAL_MS = 12_000;
  let nextArrival = T0 + ARRIVAL_MS;
  while (w.status === 'watching') {
    now += pollIntervalMs(w, now);
    while (nextArrival <= now && total < 1_000 + files) {
      total += 1;
      nextArrival += ARRIVAL_MS;
    }
    const r = step(w, total, now);
    w = r.watch;
    probes += 1;
    if (r.refresh) refreshes += 1;
    assert.ok(now - T0 < MAX_WATCH_MS, 'must finish well inside the ceiling');
  }

  assert.equal(w.status, 'done', 'steady progress never trips the stall limit');
  assert.equal(w.imported, files);
  // At most one refresh per throttle window, plus the final one.
  const hour = files * ARRIVAL_MS;
  assert.ok(refreshes <= Math.ceil(hour / REFRESH_THROTTLE_LARGE_MS) + 1, `refreshed ${refreshes} times`);
  assert.ok(refreshes < probes / 2, 'far fewer refreshes than probes');
  assert.ok(refreshes >= 2, 'the catalogue fills in during the import, not only at the end');
});

test('the first arrival refreshes immediately but the next is held back until the throttle passes', () => {
  let w = startImportWatch(null, LARGE_BATCH, 0, T0);

  let r = step(w, 1, T0 + 3_000);
  assert.equal(r.refresh, true);
  w = r.watch;

  r = step(w, 2, T0 + 6_000);
  assert.equal(r.refresh, false, 'inside the throttle window');
  w = r.watch;

  // Nothing new, but the held-back book must not be forgotten.
  r = step(w, 2, T0 + 3_000 + REFRESH_THROTTLE_SMALL_MS);
  assert.equal(r.refresh, true);
  w = r.watch;

  r = step(w, 2, T0 + 3_000 + REFRESH_THROTTLE_SMALL_MS + 5_000);
  assert.equal(r.refresh, false, 'nothing left to show');
});

test('files that never arrive end the watch after the stall window, keeping what did arrive', () => {
  let w = startImportWatch(null, 5, 100, T0);

  let r = step(w, 103, T0 + 10_000);
  assert.equal(r.refresh, true);
  w = r.watch;
  assert.equal(w.imported, 3);

  r = step(w, 103, T0 + 10_000 + STALL_MS - 1);
  assert.equal(r.watch.status, 'watching');
  w = r.watch;

  r = step(w, 103, T0 + 10_000 + STALL_MS);
  assert.equal(r.watch.status, 'stalled');
  assert.equal(r.watch.imported, 3);
  assert.equal(r.refresh, false, 'already refreshed for these three');
});

test('arrivals held back by the throttle are still refreshed when the watch stalls', () => {
  let w = startImportWatch(null, 5, 100, T0);
  w = step(w, 101, T0 + 3_000).watch;      // refreshes
  const held = step(w, 102, T0 + 6_000);   // throttled
  assert.equal(held.refresh, false);

  const end = step(held.watch, 102, T0 + 6_000 + STALL_MS);
  assert.equal(end.watch.status, 'stalled');
  assert.equal(end.refresh, true);
});

test('nothing arriving at all stalls without a pointless refresh', () => {
  const w = startImportWatch(null, 2, 100, T0);
  const r = step(w, 100, T0 + STALL_MS);
  assert.equal(r.watch.status, 'stalled');
  assert.equal(r.refresh, false);
});

test('progress resets the stall clock, so a slow but moving batch is not abandoned', () => {
  let w = startImportWatch(null, 10, 0, T0);
  let now = T0;
  for (let i = 1; i <= 9; i += 1) {
    now += STALL_MS - 1_000; // each book arrives just inside the window
    const r = step(w, i, now);
    assert.equal(r.watch.status, 'watching');
    w = r.watch;
  }
});

test('an upload made while another is being followed joins it', () => {
  let w = startImportWatch(null, 10, 200, T0);
  w = step(w, 204, T0 + 30_000).watch;

  w = startImportWatch(w, 20, 204, T0 + 40_000);
  assert.equal(w.expected, 30);
  assert.equal(w.baseline, 200, 'the baseline stays, so the earlier arrivals still count');
  assert.equal(w.imported, 4);
  assert.equal(w.lastGrowthAt, T0 + 40_000, 'the stall clock restarts');

  const r = step(w, 230, T0 + 90_000);
  assert.equal(r.watch.status, 'done');
});

test('an upload after a finished or stalled watch starts afresh', () => {
  let w = startImportWatch(null, 1, 10, T0);
  w = step(w, 11, T0 + 3_000).watch;
  assert.equal(w.status, 'done');

  const next = startImportWatch(w, 2, 11, T0 + 60_000);
  assert.equal(next.status, 'watching');
  assert.equal(next.expected, 2);
  assert.equal(next.baseline, 11);
  assert.equal(next.imported, 0);
});

test('without a baseline the first reading becomes it', () => {
  let w = startImportWatch(null, 2, null, T0);
  let r = step(w, 400, T0 + 3_000);
  assert.equal(r.watch.baseline, 400);
  assert.equal(r.watch.imported, 0);
  assert.equal(r.refresh, false);
  w = r.watch;

  r = step(w, 402, T0 + 6_000);
  assert.equal(r.watch.status, 'done');
});

test('books deleted during the wait do not hide arrivals', () => {
  let w = startImportWatch(null, 2, 100, T0);
  w = step(w, 97, T0 + 3_000).watch; // three deleted elsewhere
  assert.equal(w.baseline, 97);

  const r = step(w, 98, T0 + 6_000);
  assert.equal(r.watch.imported, 1);
  assert.equal(r.refresh, true);
});

test('other people adding books cannot push the count past what was queued', () => {
  const r = step(startImportWatch(null, 2, 100, T0), 150, T0 + 3_000);
  assert.equal(r.watch.imported, 2);
  assert.equal(r.watch.status, 'done');
});

test('polling is quick for the first minute, then eases off', () => {
  const w = startImportWatch(null, 1, 0, T0);
  assert.equal(pollIntervalMs(w, T0), POLL_FAST_MS);
  assert.equal(pollIntervalMs(w, T0 + 59_999), POLL_FAST_MS);
  assert.equal(pollIntervalMs(w, T0 + 60_000), POLL_MEDIUM_MS);
  assert.equal(pollIntervalMs(w, T0 + 10 * 60_000), POLL_SLOW_MS);
});

test('a large batch is refreshed less often than a small one', () => {
  const small = startImportWatch(null, LARGE_BATCH, 0, T0);
  const large = startImportWatch(null, LARGE_BATCH + 1, 0, T0);

  for (const [w, window] of [[small, REFRESH_THROTTLE_SMALL_MS], [large, REFRESH_THROTTLE_LARGE_MS]] as const) {
    let cur = step(w, 1, T0 + 3_000).watch;      // first arrival refreshes
    cur = step(cur, 2, T0 + 6_000).watch;        // held back
    assert.equal(step(cur, 2, T0 + 3_000 + window - 1).refresh, false);
    assert.equal(step(cur, 2, T0 + 3_000 + window).refresh, true);
  }
});
