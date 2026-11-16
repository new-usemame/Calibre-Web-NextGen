import { useEffect, useRef, useState } from 'react';
import { measureCatalogColumnCount } from './catalogGridMeasurement';

interface UseMeasuredGridColumnsOptions {
  /** Column count to assume until the real grid has been measured. */
  fallbackColumns: number;
  /** CSS custom property that holds the grid's minimum track width. */
  minWidthVar: string;
  /** Changes whenever the track geometry changes without a resize (density). */
  layoutKey?: string;
}

/** Measure how many columns a CSS grid actually renders.
 *
 * Mirrors the Library's measurement (Catalog.tsx): a ResizeObserver owns the
 * live sample, one next-frame self-heal covers browsers that serialise the
 * resolved tracks late, and a short fail-open timer guarantees `measured`
 * eventually turns true so a page that cannot be measured still loads (with the
 * fallback) instead of waiting forever. Pass `gridRef` as the grid's `ref`.
 */
export function useMeasuredGridColumns({
  fallbackColumns, minWidthVar, layoutKey = '',
}: UseMeasuredGridColumnsOptions) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  const [columns, setColumns] = useState(() => Math.max(1, fallbackColumns));
  const [measured, setMeasured] = useState(false);
  const measureRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!node) return;
    let frame = 0;
    let cancelled = false;

    const measure = () => {
      const style = getComputedStyle(node);
      const next = measureCatalogColumnCount({
        gridTemplateColumns: style.gridTemplateColumns,
        gridWidth: node.getBoundingClientRect().width,
        minColumnWidth: Number.parseFloat(style.getPropertyValue(minWidthVar)),
        columnGap: Number.parseFloat(style.columnGap),
      });
      // Zero width or no resolved tracks is not a measurement: keep the last
      // known count and let a later sample correct it.
      if (next !== null) {
        setColumns(next);
        setMeasured(true);
      }
    };
    measureRef.current = measure;
    const scheduleMeasure = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        measure();
      });
    };

    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      measure();
      scheduleMeasure();
    });
    observer?.observe(node);
    scheduleMeasure();
    void document.fonts?.ready.then(() => {
      if (!cancelled) scheduleMeasure();
    });

    return () => {
      cancelled = true;
      observer?.disconnect();
      cancelAnimationFrame(frame);
      if (measureRef.current === measure) measureRef.current = () => {};
    };
  }, [node, minWidthVar, layoutKey]);

  // Fail-open: never let the measurement gate stand between a reader and their
  // books. Recheck once more immediately before opening it.
  useEffect(() => {
    if (measured) return;
    const timer = setTimeout(() => {
      measureRef.current();
      setMeasured(true);
    }, 150);
    return () => clearTimeout(timer);
  }, [measured]);

  return { gridRef: setNode, columns, measured };
}
