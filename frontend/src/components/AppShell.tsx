import { useState, useEffect, type ReactNode } from 'react';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { HelpBanner } from './HelpBanner';
import { SkipLink } from './SkipLink';
import styles from './AppShell.module.css';

interface AppShellProps {
  userName: string;
  instanceName?: string;
  onLogout: () => void;
  children: ReactNode;
}

export function AppShell({ userName, instanceName, onLogout, children }: AppShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Lock the page behind the mobile drawer: overscroll-behavior only stops scroll
  // chaining AT the drawer's edge, not touches on the scrim, so without this the
  // page still scrolled behind the open drawer (#576). Only affects the open state.
  useEffect(() => {
    if (!drawerOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [drawerOpen]);

  return (
    <div className={styles.shell}>
      {/* First focusable element on the page (SC 2.4.1). */}
      <SkipLink />
      <TopBar userName={userName} instanceName={instanceName} onLogout={onLogout} onMenu={() => setDrawerOpen(true)} />
      <HelpBanner />
      <div className={styles.body}>
        <Sidebar open={drawerOpen} onClose={() => setDrawerOpen(false)} onNavigate={() => setDrawerOpen(false)} />
        {/* The one <main> landmark (SC 1.3.1); tabIndex=-1 lets route changes
            move focus here (see useRouteA11y). */}
        <main id="main" tabIndex={-1} className={styles.content}>
          {children}
        </main>
      </div>
    </div>
  );
}
