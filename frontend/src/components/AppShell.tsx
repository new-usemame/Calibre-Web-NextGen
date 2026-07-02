import { useState, useEffect, type ReactNode } from 'react';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { HelpBanner } from './HelpBanner';
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
      <TopBar userName={userName} instanceName={instanceName} onLogout={onLogout} onMenu={() => setDrawerOpen(true)} />
      <HelpBanner />
      <div className={styles.body}>
        <Sidebar open={drawerOpen} onNavigate={() => setDrawerOpen(false)} />
        <div className={styles.content}>{children}</div>
      </div>
    </div>
  );
}
