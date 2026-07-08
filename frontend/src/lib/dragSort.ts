// Pure geometry helpers for the Customize sidebar drag-reorder. No DOM, no React
// — the single source of truth for "where does the dragged row belong now", and
// unit-testable in isolation. This replaces the old elementFromPoint()-per-pixel
// approach, whose reorder-on-every-frame caused visible jitter at row boundaries.

/** Immutable array move: returns a copy with `from` spliced out and re-inserted
 *  at `to`. Out-of-range / no-op moves return a shallow copy unchanged. */
export function arrayMove<T>(list: readonly T[], from: number, to: number): T[] {
  const next = list.slice();
  if (
    from < 0 || from >= next.length ||
    to < 0 || to >= next.length ||
    from === to
  ) {
    return next;
  }
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/**
 * Given the resting vertical centers of every row (ascending) and the dragged
 * row's *live* center (its resting center + the pointer's delta), return the
 * slot index the dragged row should now occupy.
 *
 * It is a pure, monotonic function of `liveCenter`: as the pointer moves down
 * the result only ever increases, as it moves up it only ever decreases. That
 * monotonicity is what kills the oscillation the old code suffered — there is a
 * single deterministic answer for any pointer position, so a row never flickers
 * back and forth across a boundary.
 *
 * The dragged row's own center is included in `centers`; while the live center
 * sits inside its home slot the function returns the dragged row's own index
 * (i.e. no reorder). `centers` must be sorted ascending (natural DOM order).
 */
export function slotForCenter(centers: readonly number[], liveCenter: number): number {
  if (centers.length === 0) return 0;
  let k = 0;
  while (k < centers.length - 1 && liveCenter > (centers[k] + centers[k + 1]) / 2) {
    k++;
  }
  return k;
}

/**
 * The set of resting indices that must visually shift by one slot to open a gap
 * for a drag from `fromIndex` to `targetIndex`, plus the sign of that shift.
 *   - dragging down (target > from): rows (from+1 … target) slide UP  (sign -1)
 *   - dragging up   (target < from): rows (target … from-1) slide DOWN (sign +1)
 * The dragged row itself is never in the returned set (it tracks the pointer).
 * Returned as a map of index → sign so the caller can translateY(sign * slot).
 */
export function shiftMap(fromIndex: number, targetIndex: number): Map<number, -1 | 1> {
  const out = new Map<number, -1 | 1>();
  if (targetIndex > fromIndex) {
    for (let i = fromIndex + 1; i <= targetIndex; i++) out.set(i, -1);
  } else if (targetIndex < fromIndex) {
    for (let i = targetIndex; i < fromIndex; i++) out.set(i, 1);
  }
  return out;
}
