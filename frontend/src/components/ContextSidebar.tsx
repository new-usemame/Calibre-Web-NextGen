import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ChevronLeft, X } from 'lucide-react';
import { Link, useLocation } from 'wouter';
import type { ContextSidebarDefinition, ContextSidebarItem } from '../lib/contextSidebars';
import { resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useIsDrawerMode } from '../lib/a11y/useIsDrawerMode';
import styles from './ContextSidebar.module.css';

interface ContextSidebarProps {
  context: ContextSidebarDefinition;
  /** Off-canvas drawer state. Desktop context navigation is always visible. */
  open: boolean;
  onClose: () => void;
  onNavigate: () => void;
}

function itemHash(item: ContextSidebarItem): string {
  const index = item.href.indexOf('#');
  return index === -1 ? '' : item.href.slice(index);
}

function itemPath(item: ContextSidebarItem): string {
  return item.href.split('#', 1)[0];
}

export function ContextSidebar({ context, open, onClose, onNavigate }: ContextSidebarProps) {
  const [location] = useLocation();
  const [hash, setHash] = useState(() => window.location.hash);
  const railRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const t = useT();
  const isDrawerMode = useIsDrawerMode();

  useEffect(() => {
    const syncHash = () => setHash(window.location.hash);
    window.addEventListener('hashchange', syncHash);
    window.addEventListener('popstate', syncHash);
    return () => {
      window.removeEventListener('hashchange', syncHash);
      window.removeEventListener('popstate', syncHash);
    };
  }, []);

  // Deep links can arrive before the admin forms finish loading. Observe the
  // main landmark until the target appears, then honor the requested section.
  useEffect(() => {
    if (!hash) return;
    const id = decodeURIComponent(hash.slice(1));
    const scrollToTarget = () => {
      const target = document.getElementById(id);
      if (!target) return false;
      target.scrollIntoView({ block: 'start' });
      return true;
    };
    if (scrollToTarget()) return;
    const main = document.getElementById('main');
    if (!main) return;
    const observer = new MutationObserver(() => {
      if (scrollToTarget()) observer.disconnect();
    });
    observer.observe(main, { childList: true, subtree: true });
    const timeout = window.setTimeout(() => observer.disconnect(), 5000);
    return () => {
      observer.disconnect();
      window.clearTimeout(timeout);
    };
  }, [hash, location]);

  useEffect(() => {
    const node = navRef.current;
    if (!node) return;
    if (isDrawerMode && !open) node.setAttribute('inert', '');
    else node.removeAttribute('inert');
  }, [isDrawerMode, open]);

  useFocusTrap(navRef, { onClose, active: isDrawerMode && open });

  // The shell can render notice banners between the fixed top bar and this
  // rail. Size from the rail's *rendered* top, not only --topbar-h, otherwise
  // that banner height sits below the viewport and traps the final links behind
  // an internal scroller whose overscroll intentionally cannot reach the page.
  useLayoutEffect(() => {
    const rail = railRef.current;
    if (!rail || isDrawerMode) {
      rail?.style.removeProperty('--context-sidebar-available-height');
      return;
    }

    let frame = 0;
    const measure = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const top = Math.max(0, rail.getBoundingClientRect().top);
        rail.style.setProperty(
          '--context-sidebar-available-height',
          `${Math.max(0, window.innerHeight - top)}px`,
        );
      });
    };

    measure();
    window.addEventListener('resize', measure);
    window.addEventListener('scroll', measure, { passive: true });

    // Notices load asynchronously and can be dismissed without a viewport
    // resize. Watching the shell siblings catches either height change.
    const body = rail.parentElement;
    const shell = body?.parentElement;
    const observer = new ResizeObserver(measure);
    if (shell && body) {
      for (const sibling of shell.children) {
        if (sibling === body) break;
        observer.observe(sibling);
      }
    }

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure);
      observer.disconnect();
    };
  }, [isDrawerMode]);

  const namedHashes = new Set(
    context.groups.flatMap((group) => group.items.map(itemHash)).filter(Boolean),
  );
  const isActive = (item: ContextSidebarItem) => {
    if (!item.spa) return false;
    const path = itemPath(item);
    const targetHash = itemHash(item);
    if (targetHash) {
      if (item.defaultForPath && location === path && (!hash || !namedHashes.has(hash))) return true;
      return location === path && hash === targetHash;
    }
    return location === path;
  };

  const followItem = (item: ContextSidebarItem) => {
    const targetHash = itemHash(item);
    if (targetHash) setHash(targetHash);
    else setHash('');
    onNavigate();
  };

  return (
    <>
      {open && <div className={styles.scrim} onClick={onClose} aria-hidden="true" />}
      <div ref={railRef} className={styles.rail} data-context-sidebar={context.key}>
        <nav
          ref={navRef}
          className={open ? styles.navOpen : styles.nav}
          aria-label={t(context.label)}
          tabIndex={-1}
        >
          <div className={styles.topControls}>
            <button type="button" className={styles.drawerClose} onClick={onClose} aria-label={t('Close menu')}>
              <X size={20} aria-hidden="true" focusable={false} />
            </button>
            <Link href="/" className={styles.libraryLink} onClick={onNavigate}>
              <ChevronLeft size={16} aria-hidden="true" focusable={false} />
              <span>{t('Back to library')}</span>
            </Link>
          </div>

          <div className={styles.groups}>
            {context.groups.map((group) => {
              const headingId = `${context.key}-${group.key}-heading`;
              return (
                <section key={group.key} className={styles.group} aria-labelledby={headingId}>
                  <h2 id={headingId} className={styles.groupLabel}>{t(group.label)}</h2>
                  <ul className={styles.list} role="list">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const active = isActive(item);
                      const content = (
                        <>
                          <Icon size={16} className={styles.icon} aria-hidden="true" focusable={false} />
                          <span className={styles.itemLabel} data-context-sidebar-label>{t(item.label)}</span>
                          {item.classic && (
                            <>
                              <small className={styles.classic} aria-hidden="true">{t('Classic')}</small>
                              <span className={styles.srOnly}>{t('Opens in classic view')}</span>
                            </>
                          )}
                        </>
                      );
                      return (
                        <li key={item.key}>
                          {item.spa ? (
                            <Link
                              href={item.href}
                              className={active ? styles.itemActive : styles.item}
                              aria-current={active ? 'page' : undefined}
                              onClick={() => followItem(item)}
                            >
                              {content}
                            </Link>
                          ) : (
                            <a href={resourceUrl(item.href)} className={styles.item} onClick={onNavigate}>
                              {content}
                            </a>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              );
            })}
          </div>
        </nav>
      </div>
    </>
  );
}
