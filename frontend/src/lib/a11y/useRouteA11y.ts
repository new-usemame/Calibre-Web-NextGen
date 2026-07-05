/* SPA route-change accessibility (WCAG 2.2 SC 2.4.2 Page Titled, 2.4.3 Focus Order).
 *
 * A client-routed page swap is invisible to a screen reader: focus stays where
 * it was (often on a now-unmounted link) and the title never changes. On every
 * navigation this hook:
 *   1. moves focus to the <main> landmark (so the next Tab / SR read starts at
 *      the new content, not stranded mid-page), and
 *   2. sets document.title to "<page h1> · <instance>" so each view is
 *      distinctly titled in history and the SR announces the new page.
 *
 * Must be called inside a wouter <Router> (needs useLocation). Rendered once via
 * <RouteA11y> at the top of the routing tree. See patterns.md → "Route focus".
 */
import { useEffect, useRef } from 'react';
import { useLocation } from 'wouter';

export function useRouteA11y(instanceName?: string): void {
  const [location] = useLocation();
  const firstRun = useRef(true);

  useEffect(() => {
    const isFirst = firstRun.current;
    firstRun.current = false;

    // Don't steal focus on initial page load — only on client navigation.
    if (!isFirst) {
      const main = document.getElementById('main');
      main?.focus();
    }

    // Defer a frame so the destination route's <h1> has rendered before we read
    // it for the title.
    const raf = requestAnimationFrame(() => {
      const heading = document.querySelector('main h1')?.textContent?.trim();
      const base = instanceName || 'Calibre-Web NextGen';
      document.title = heading ? `${heading} · ${base}` : base;
    });
    return () => cancelAnimationFrame(raf);
  }, [location, instanceName]);
}

/** Zero-DOM component that runs route a11y side-effects. Mount inside <Router>. */
export function RouteA11y({ instanceName }: { instanceName?: string }) {
  useRouteA11y(instanceName);
  return null;
}
