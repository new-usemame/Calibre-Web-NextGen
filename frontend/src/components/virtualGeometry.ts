/** Unmeasured rows are estimates only; measured border boxes own all offsets. */
export function rowOffsets(keys: readonly string[], heights: ReadonlyMap<string, number>, estimate: number): number[] {
  const offsets = [0];
  for (const key of keys) offsets.push(offsets[offsets.length - 1] + (heights.get(key) ?? estimate));
  return offsets;
}

/** Index containing top, clamped at either end (including an empty list). */
export function rowAt(offsets: readonly number[], top: number): number {
  let low = 0;
  let high = Math.max(0, offsets.length - 2);
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (offsets[middle] <= top) low = middle;
    else high = middle - 1;
  }
  return low;
}

export function rowWindow(offsets: readonly number[], top: number, height: number, overscan: number) {
  return {
    start: rowAt(offsets, Math.max(0, top - height * overscan)),
    end: Math.min(offsets.length - 1, rowAt(offsets, top + height * (1 + overscan)) + 1),
  };
}
