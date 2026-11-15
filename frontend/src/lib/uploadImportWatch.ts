// Follows uploaded files into the library (#1044).
//
// An upload only drops the file in the ingest folder; the import happens later
// in a separate process, and nothing tells the browser when it is done. So the
// SPA watches the library's book total instead: the total before the upload is
// the baseline, and every book above it is one of ours arriving.
//
// This is deliberately free of React and fetch so the timing rules can be
// tested directly. They have to cope with one file landing in a few seconds and
// with a few hundred taking the better part of an hour:
//
//  - polling slows down the longer the import runs, and each probe is a
//    one-row request, so a long import costs almost nothing;
//  - it stops as soon as every queued file has appeared, or once nothing new
//    has appeared for STALL_MS (a file that is rejected or fails to import
//    never arrives, so waiting for the full count would never end). Progress
//    resets that clock, so a big batch that keeps landing keeps being followed;
//  - the library views are refreshed when books arrive, but only every so
//    often while a batch is still landing, because a refresh resets a
//    catalogue the reader may be scrolling through: every 20 seconds for a
//    handful of files, every minute for a large batch. A final refresh always
//    follows the last arrival.

export const POLL_FAST_MS = 3_000;
export const POLL_MEDIUM_MS = 5_000;
export const POLL_SLOW_MS = 10_000;
export const STALL_MS = 5 * 60_000;
export const MAX_WATCH_MS = 3 * 60 * 60_000;
export const REFRESH_THROTTLE_SMALL_MS = 20_000;
export const REFRESH_THROTTLE_LARGE_MS = 60_000;
/** Batches above this many files use the slower refresh. */
export const LARGE_BATCH = 25;

export type ImportWatchStatus = 'watching' | 'done' | 'stalled';

export interface ImportWatch {
  /** Files queued across every upload still being followed. */
  expected: number;
  /** Library total before the first of them was queued; null until known. */
  baseline: number | null;
  /** Books seen arriving above the baseline, never more than `expected`. */
  imported: number;
  startedAt: number;
  lastGrowthAt: number;
  lastRefreshAt: number;
  /** `imported` as of the last refresh, so a throttled arrival is not lost. */
  refreshedImported: number;
  status: ImportWatchStatus;
}

/** Begin following `queued` files. An upload made while an earlier one is
 *  still being followed joins it: the baseline stays and the target grows. */
export function startImportWatch(
  previous: ImportWatch | null,
  queued: number,
  baseline: number | null,
  now: number,
  atUploadStart: ImportWatch | null = null,
): ImportWatch {
  // The earlier import can finish while the next upload request is in flight.
  // Keep those arrivals in the same batch rather than counting them again
  // against the older total read before this upload.
  const joinsEarlier = atUploadStart?.status === 'watching'
    && previous?.startedAt === atUploadStart.startedAt;
  if (previous && (previous.status === 'watching' || joinsEarlier)) {
    return { ...previous, expected: previous.expected + queued, lastGrowthAt: now, status: 'watching' };
  }
  return {
    expected: queued,
    baseline,
    imported: 0,
    startedAt: now,
    lastGrowthAt: now,
    lastRefreshAt: 0,
    refreshedImported: 0,
    status: 'watching',
  };
}

export interface ObserveResult {
  watch: ImportWatch;
  /** True when the library views should be refetched now. */
  refresh: boolean;
}

/** Fold in a freshly read total, or null to check the deadline without a reading. */
export function observeLibraryTotal(watch: ImportWatch, total: number | null, now: number): ObserveResult {
  if (watch.status !== 'watching') return { watch, refresh: false };

  // No baseline could be read before the upload, so the first reading is it.
  // Refresh that first reading even without counted growth: it may already
  // include all our books. The count remains an estimate in this case.
  const firstReading = watch.baseline === null && total !== null;
  let baseline = watch.baseline;
  let imported = watch.imported;
  if (total !== null) {
    baseline = Math.min(baseline ?? total, total - imported);
    // Follow deletions down without undoing imports already observed, so the
    // next arrival is counted and refreshed even after books were deleted.
    imported = Math.min(Math.max(total - baseline, 0), watch.expected);
  }

  const lastGrowthAt = imported > watch.imported ? now : watch.lastGrowthAt;

  let status: ImportWatchStatus = 'watching';
  if (imported >= watch.expected) status = 'done';
  else if (now - lastGrowthAt >= STALL_MS || now - watch.startedAt >= MAX_WATCH_MS) status = 'stalled';

  const unrefreshed = imported > watch.refreshedImported;
  const refresh = firstReading || (unrefreshed && (status !== 'watching' || now - watch.lastRefreshAt >= refreshThrottleMs(watch.expected)));

  return {
    refresh,
    watch: {
      ...watch,
      baseline,
      imported,
      lastGrowthAt,
      status,
      lastRefreshAt: refresh ? now : watch.lastRefreshAt,
      refreshedImported: refresh ? imported : watch.refreshedImported,
    },
  };
}

export function refreshThrottleMs(expected: number): number {
  return expected > LARGE_BATCH ? REFRESH_THROTTLE_LARGE_MS : REFRESH_THROTTLE_SMALL_MS;
}

/** Delay before the next probe: quick at first, when a single book usually
 *  lands, then easing off for a long batch. */
export function pollIntervalMs(watch: ImportWatch, now: number): number {
  const elapsed = now - watch.startedAt;
  if (elapsed < 60_000) return POLL_FAST_MS;
  if (elapsed < 10 * 60_000) return POLL_MEDIUM_MS;
  return POLL_SLOW_MS;
}
