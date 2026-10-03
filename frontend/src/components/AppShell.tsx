import { useState, useEffect, type ReactNode } from 'react';
import { useLocation } from 'wouter';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { ShelfDragProvider, useShelfDrag } from './ShelfDrag';
import { ContextSidebar } from './ContextSidebar';
import { AnnouncementBanner } from './AnnouncementBanner';
import { SkipLink } from './SkipLink';
import { UserNoticeBanner } from './UserNotices';
import { contextSidebarForLocation } from '../lib/contextSidebars';
import { useIsDrawerMode } from '../lib/a11y/useIsDrawerMode';
import styles from './AppShell.module.css';

interface AppShellProps {
  userName: string;
  instanceName?: string;
  onLogout: () => void;
  children: ReactNode;
}

export function AppShell(props: AppShellProps) {
  return <ShelfDragProvider><ShellContents {...props} /></ShelfDragProvider>;
}

function ShellContents({ userName, instanceName, onLogout, children }: AppShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const shelfDrag = useShelfDrag();
  const [location] = useLocation();
  const contextSidebar = contextSidebarForLocation(location);
  const isDrawerMode = useIsDrawerMode();
  const visibleDrawer = isDrawerMode && (drawerOpen || (!!shelfDrag?.drag && !contextSidebar));

  // Lock the page behind the mobile drawer: overscroll-behavior only stops scroll
  // chaining AT the drawer's edge, not touches on the scrim, so without this the
  // page still scrolled behind the open drawer (#576). Only affects the open state.
  useEffect(() => {
    if (!visibleDrawer) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [visibleDrawer]);

  return (
    <div className={styles.shell}>
      {/* First focusable element on the page (SC 2.4.1). */}
      <SkipLink />
      <TopBar userName={userName} instanceName={instanceName} onLogout={onLogout} onMenu={() => setDrawerOpen(true)} />
      <UserNoticeBanner />
      <AnnouncementBanner />
      <div className={styles.body}>
        {contextSidebar ? (
          <ContextSidebar
            context={contextSidebar}
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            onNavigate={() => setDrawerOpen(false)}
          />
        ) : (
          <Sidebar open={drawerOpen || !!shelfDrag?.drag} onClose={() => setDrawerOpen(false)} onNavigate={() => setDrawerOpen(false)} />
        )}
        {/* The one <main> landmark (SC 1.3.1); tabIndex=-1 lets route changes
            move focus here (see useRouteA11y). */}
        <main id="main" tabIndex={-1} className={styles.content}>
          {children}
        </main>
      </div>
    </div>
  );
}
