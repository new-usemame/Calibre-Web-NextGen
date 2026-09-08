import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { rowAt, rowOffsets, rowWindow } from './virtualGeometry';
import styles from './VirtualizedList.module.css';

interface VirtualizedListProps<T> {
  items: T[];
  itemKey: (item: T) => string;
  renderItem: (item: T, index: number) => ReactNode;
  ariaLabel: string;
  estimatedRowHeight?: number;
  overscanViewports?: number;
  className?: string;
}

/** Measured local-data virtualization shared by annotation surfaces.
 *
 * `useIntersectionObserver` deliberately does not fit this job: it appends
 * pages and leaves earlier DOM mounted. This window removes off-screen rows,
 * keeping a 595-row annotation set bounded to the viewport plus overscan.
 * Unmounted rows use cached measurements (or an estimate until first mounted).
 */
export function VirtualizedList<T>({
  items, itemKey, renderItem, ariaLabel, estimatedRowHeight = 72,
  overscanViewports = 2, className = '',
}: VirtualizedListProps<T>) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const heights = useRef(new Map<string, number>());
  const width = useRef(0);
  const pendingTop = useRef<number | null>(null);
  const frame = useRef(0);
  const [, setMeasurement] = useState(0);
  // Only window boundaries enter scroll state, never the pixel offset (#1813).
  const [window, setWindow] = useState({ start: 0, end: 1 });
  const keys = items.map(itemKey);
  const offsets = rowOffsets(keys, heights.current, estimatedRowHeight);
  const start = Math.min(window.start, Math.max(0, items.length - 1));
  const end = Math.min(items.length, Math.max(start + 1, window.end));

  // Remeasure after every commit: renderItem can change content or selection
  // without changing keys. Layout effects settle new windows before paint.
  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    if (pendingTop.current !== null) {
      viewport.scrollTop = pendingTop.current;
      pendingTop.current = null;
    }
    const updateWindow = (table: number[]) => {
      const next = rowWindow(table, viewport.scrollTop, viewport.clientHeight || 600, overscanViewports);
      setWindow(current => current.start === next.start && current.end === next.end ? current : next);
    };
    const measure = () => {
      // Anchor the first visible row, including the pixel position within it.
      // Changing estimates above it must not move the passage being read.
      const anchor = rowAt(offsets, viewport.scrollTop);
      const within = viewport.scrollTop - offsets[anchor];
      let changed = false;
      if (width.current !== viewport.clientWidth) {
        width.current = viewport.clientWidth;
        heights.current.clear();
        changed = true;
      }
      const liveKeys = new Set(keys);
      for (const key of heights.current.keys()) if (!liveKeys.has(key)) heights.current.delete(key);
      viewport.querySelectorAll<HTMLElement>('[data-virtual-row]').forEach(row => {
        const key = keys[Number(row.getAttribute('aria-posinset')) - 1];
        const height = row.getBoundingClientRect().height;
        // jsdom has no layout. Keep estimates there, without requiring RO.
        if (height > 0 && heights.current.get(key) !== height) {
          heights.current.set(key, height);
          changed = true;
        }
      });
      if (changed) {
        const next = rowOffsets(keys, heights.current, estimatedRowHeight);
        pendingTop.current = next[anchor] + within;
        setMeasurement(value => value + 1);
      } else updateWindow(offsets);
    };
    measure();
    // RO runs outside React's commit. Flush its offsets/anchor correction
    // before the browser paints the resized content at stale positions.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => flushSync(measure));
    observer?.observe(viewport);
    viewport.querySelectorAll('[data-virtual-row]').forEach(row => observer?.observe(row));
    const onScroll = () => {
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => updateWindow(offsets));
    };
    viewport.addEventListener('scroll', onScroll);
    // Also provides width/viewport remeasurement without ResizeObserver.
    globalThis.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      cancelAnimationFrame(frame.current);
      viewport.removeEventListener('scroll', onScroll);
      globalThis.removeEventListener('resize', measure);
    };
  });

  return (
    <div ref={viewportRef} className={`${styles.viewport} ${className}`.trim()} data-virtualized-list>
      <div className={styles.canvas} role="list" aria-label={ariaLabel} style={{ height: offsets[items.length] }}>
        {items.slice(start, end).map((item, offset) => {
          const index = start + offset;
          return (
            <div key={keys[index]} className={styles.row} role="listitem"
              aria-posinset={index + 1} aria-setsize={items.length} data-virtual-row
              style={{ transform: `translateY(${offsets[index]}px)` }}>
              {renderItem(item, index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
