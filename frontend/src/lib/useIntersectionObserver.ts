import { useEffect, useState } from 'react';

interface UseIntersectionObserverProps {
  onIntersect: () => void;
  enabled: boolean;
  threshold?: number;
  rootMargin?: string;
}

/*
 * Observe a sentinel element and call onIntersect when it scrolls into view —
 * the auto-load half of every paged list (library, shelf, table, magic shelf,
 * advanced search).
 *
 * The sentinel is tracked as STATE, not a ref, and that is load-bearing. The
 * previous version read `sentinelRef.current` at the moment the effect ran,
 * with deps of [enabled, onIntersect, threshold, rootMargin] — none of which
 * change when the sentinel element itself mounts. Every one of these pages
 * renders its sentinel only after results arrive, so whenever `enabled` had
 * already turned true while the list was still empty, the observer attached to
 * nothing and never looked again: scrolling silently stopped loading and only
 * the "Load more" button still worked. A state setter used as the ref callback
 * re-runs the effect when the element actually appears (and again with null
 * when it goes away), so the observer always tracks what is rendered (#1144).
 */
export function useIntersectionObserver({
  onIntersect,
  enabled,
  threshold = 0.1,
  rootMargin = '200px',
}: UseIntersectionObserverProps) {
  const [sentinel, setSentinel] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!enabled || !sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            onIntersect();
          }
        });
      },
      { threshold, rootMargin }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [sentinel, enabled, onIntersect, threshold, rootMargin]);

  return setSentinel;
}
