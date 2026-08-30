import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { buildGridRowOffsets, calculateGridWindow } from '../lib/gridWindow';

interface VirtualizedGridRowsProps<T> {
  items: T[];
  columnCount: number;
  gridNode: HTMLElement | null;
  gridTop: number;
  itemKey: (item: T) => string | number;
  renderItem: (item: T, index: number) => ReactNode;
  rowClassName: string;
  spacerClassName: string;
  /** Changes whenever card height rules change without the item set changing. */
  layoutKey: string;
  estimatedRowHeight?: number;
  overscanViewports?: number;
}

/** Window complete rows in a document-scrolling CSS grid.
 *
 * The parent owns the real grid tracks. Each mounted row spans those tracks and
 * uses `subgrid`, while spacers preserve the height of unmounted rows. Keeping
 * full rows mounted is load-bearing: slicing arbitrary cards would change CSS
 * auto-placement and no longer match the measured column count.
 *
 * Catalog is the first integration because it already has a reliable measured
 * column count. TODO(#1813): Shelf, AdvancedSearch, and MagicShelfView can adopt
 * this shared primitive once they expose the same measurement rather than
 * duplicating density/breakpoint guesses here.
 */
export function VirtualizedGridRows<T>({
  items,
  columnCount,
  gridNode,
  gridTop,
  itemKey,
  renderItem,
  rowClassName,
  spacerClassName,
  layoutKey,
  estimatedRowHeight = 360,
  overscanViewports = 2,
}: VirtualizedGridRowsProps<T>) {
  const safeColumnCount = Math.max(1, columnCount);
  const rowCount = Math.ceil(items.length / safeColumnCount);
  const measuredHeights = useRef(new Map<number, number>());
  const [measurementVersion, setMeasurementVersion] = useState(0);
  const [viewport, setViewport] = useState(() => ({
    scrollY: typeof window === 'undefined' ? 0 : window.scrollY,
    height: typeof window === 'undefined' ? 800 : window.innerHeight,
  }));

  useEffect(() => {
    measuredHeights.current.clear();
    setMeasurementVersion((version) => version + 1);
  }, [safeColumnCount, layoutKey]);

  useEffect(() => {
    let frame = 0;
    const sample = () => {
      frame = 0;
      setViewport((current) => {
        const next = { scrollY: window.scrollY, height: window.innerHeight };
        return current.scrollY === next.scrollY && current.height === next.height ? current : next;
      });
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(sample);
    };
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    schedule();
    return () => {
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      cancelAnimationFrame(frame);
    };
  }, []);

  const averageMeasuredHeight = useMemo(() => {
    const heights = [...measuredHeights.current.values()];
    if (heights.length === 0) return estimatedRowHeight;
    return heights.reduce((sum, height) => sum + height, 0) / heights.length;
  }, [estimatedRowHeight, measurementVersion]);

  const offsets = useMemo(
    () => buildGridRowOffsets(rowCount, measuredHeights.current, averageMeasuredHeight),
    [rowCount, averageMeasuredHeight, measurementVersion],
  );
  const viewportTop = Math.max(0, viewport.scrollY - gridTop);
  const virtualizationAvailable = typeof ResizeObserver !== 'undefined';
  const gridWindow = virtualizationAvailable
    ? calculateGridWindow(offsets, viewportTop, viewport.height, overscanViewports)
    : {
        startRow: 0,
        endRow: rowCount,
        topSpacer: 0,
        bottomSpacer: 0,
        totalHeight: offsets[rowCount] ?? 0,
      };

  useEffect(() => {
    if (!gridNode || typeof ResizeObserver === 'undefined') return;
    const rows = gridNode.querySelectorAll<HTMLElement>('[data-virtual-grid-row]');
    if (rows.length === 0) return;

    const observer = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const row = Number((entry.target as HTMLElement).dataset.virtualGridRow);
        if (!Number.isInteger(row)) continue;
        // This read is inside ResizeObserver's post-layout delivery. It includes
        // the row's bottom padding, which represents the original grid row gap.
        const height = entry.target.getBoundingClientRect().height;
        if (height <= 0 || Math.abs((measuredHeights.current.get(row) ?? 0) - height) < 0.5) continue;
        measuredHeights.current.set(row, height);
        changed = true;
      }
      if (changed) setMeasurementVersion((version) => version + 1);
    });
    rows.forEach((row) => observer.observe(row));
    return () => observer.disconnect();
  }, [gridNode, gridWindow.startRow, gridWindow.endRow, safeColumnCount, layoutKey]);

  const rows: ReactNode[] = [];
  for (let row = gridWindow.startRow; row < gridWindow.endRow; row += 1) {
    const first = row * safeColumnCount;
    const last = Math.min(items.length, first + safeColumnCount);
    rows.push(
      <div
        key={`row-${row}`}
        className={rowClassName}
        data-virtual-grid-row={row}
      >
        {items.slice(first, last).map((item, offset) => (
          <Fragment key={itemKey(item)}>{renderItem(item, first + offset)}</Fragment>
        ))}
      </div>,
    );
  }

  return (
    <>
      <div
        className={spacerClassName}
        data-testid="catalog-grid-spacer-before"
        aria-hidden="true"
        style={{ height: `${gridWindow.topSpacer}px` }}
      />
      {rows}
      <div
        className={spacerClassName}
        data-testid="catalog-grid-spacer-after"
        aria-hidden="true"
        style={{ height: `${gridWindow.bottomSpacer}px` }}
      />
    </>
  );
}
