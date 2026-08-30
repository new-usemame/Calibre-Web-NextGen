export interface GridWindow {
  startRow: number;
  endRow: number;
  topSpacer: number;
  bottomSpacer: number;
  totalHeight: number;
}

/** Build cumulative row offsets from the heights learned by ResizeObserver.
 * Unknown rows use the current measured average (or the initial estimate), so
 * the scrollbar exists before a user has visited every part of the catalog. */
export function buildGridRowOffsets(
  rowCount: number,
  measuredHeights: ReadonlyMap<number, number>,
  estimatedRowHeight: number,
): number[] {
  const offsets = new Array<number>(rowCount + 1);
  offsets[0] = 0;
  for (let row = 0; row < rowCount; row += 1) {
    offsets[row + 1] = offsets[row] + (measuredHeights.get(row) ?? estimatedRowHeight);
  }
  return offsets;
}

function rowAtOffset(offsets: readonly number[], offset: number): number {
  const rowCount = Math.max(0, offsets.length - 1);
  if (rowCount === 0) return 0;

  let low = 0;
  let high = rowCount;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (offsets[middle + 1] <= offset) low = middle + 1;
    else high = middle;
  }
  return Math.min(low, rowCount - 1);
}

export function calculateGridWindow(
  offsets: readonly number[],
  viewportTop: number,
  viewportHeight: number,
  overscanViewports = 2,
): GridWindow {
  const rowCount = Math.max(0, offsets.length - 1);
  const totalHeight = offsets[rowCount] ?? 0;
  if (rowCount === 0) {
    return { startRow: 0, endRow: 0, topSpacer: 0, bottomSpacer: 0, totalHeight: 0 };
  }

  const overscan = Math.max(0, viewportHeight * overscanViewports);
  const startOffset = Math.max(0, viewportTop - overscan);
  const endOffset = Math.min(totalHeight, viewportTop + viewportHeight + overscan);
  const startRow = rowAtOffset(offsets, startOffset);
  // The end is exclusive. At an exact row boundary the row beginning there is
  // outside the requested viewport/overscan and should not be mounted merely
  // because the binary search assigns boundaries to the following row.
  const inclusiveEndOffset = Math.max(startOffset, endOffset - 0.001);
  const endRow = Math.min(rowCount, rowAtOffset(offsets, inclusiveEndOffset) + 1);

  return {
    startRow,
    endRow,
    topSpacer: offsets[startRow],
    bottomSpacer: totalHeight - offsets[endRow],
    totalHeight,
  };
}
