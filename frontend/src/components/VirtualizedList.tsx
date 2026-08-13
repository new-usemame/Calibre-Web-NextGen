import { useCallback, useLayoutEffect, useRef, useState, type ReactNode, type UIEvent } from 'react';
import styles from './VirtualizedList.module.css';

interface VirtualizedListProps<T> {
  items: T[];
  itemKey: (item: T) => string;
  renderItem: (item: T, index: number) => ReactNode;
  ariaLabel: string;
  rowHeight?: number;
  overscanViewports?: number;
  className?: string;
}

/** Fixed-row local-data virtualization shared by annotation surfaces.
 *
 * `useIntersectionObserver` deliberately does not fit this job: it appends
 * pages and leaves earlier DOM mounted. This window removes off-screen rows,
 * keeping a 595-row annotation set bounded to the viewport plus overscan.
 */
export function VirtualizedList<T>({
  items,
  itemKey,
  renderItem,
  ariaLabel,
  rowHeight = 72,
  overscanViewports = 2,
  className = '',
}: VirtualizedListProps<T>) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportHeight, setViewportHeight] = useState(600);
  const [scrollTop, setScrollTop] = useState(0);

  useLayoutEffect(() => {
    const node = viewportRef.current;
    if (!node) return;
    const measure = () => setViewportHeight(node.clientHeight || 600);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(node);
    return () => observer?.disconnect();
  }, []);

  const onScroll = useCallback((event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  }, []);

  const visibleCount = Math.max(1, Math.ceil(viewportHeight / rowHeight));
  const overscan = visibleCount * overscanViewports;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(items.length, start + visibleCount + overscan * 2);
  const visible = items.slice(start, end);

  return (
    <div
      ref={viewportRef}
      className={`${styles.viewport} ${className}`.trim()}
      onScroll={onScroll}
      data-virtualized-list
    >
      <div
        className={styles.canvas}
        role="list"
        aria-label={ariaLabel}
        style={{ height: `${items.length * rowHeight}px` }}
      >
        {visible.map((item, offset) => {
          const index = start + offset;
          return (
            <div
              key={itemKey(item)}
              className={styles.row}
              role="listitem"
              aria-posinset={index + 1}
              aria-setsize={items.length}
              data-virtual-row
              style={{ height: `${rowHeight}px`, transform: `translateY(${index * rowHeight}px)` }}
            >
              {renderItem(item, index)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
