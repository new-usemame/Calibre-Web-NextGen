/* Tracks whether the viewport is in the mobile (drawer) breakpoint.
 *
 * The sidebar is a persistent rail on desktop but an off-canvas drawer on
 * mobile. Focus-management differs: the closed drawer must be removed from the
 * tab order on mobile (it's off-screen) but stays visible/tabbable on desktop.
 * This hook is the single source of that breakpoint so the two never drift.
 * Keep the query in sync with Sidebar.module.css's 767px cutover.
 */
import { useEffect, useState } from 'react';

const MOBILE_QUERY = '(max-width: 767px)';

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return isMobile;
}
