import { Fragment, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import {
  Library, BookCopy, UploadCloud, Shield,
  Info, ListChecks, Table2, Wand2, Files, Settings2, X,
} from 'lucide-react';
import { useShelves, useMe, useMagicShelves } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useIsMobile } from '../lib/a11y/useIsMobile';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { resolveSidebarOrder, type SidebarEntryDef } from '../lib/sidebarEntries';
import { SidebarCustomize } from './SidebarCustomize';
import styles from './Sidebar.module.css';

// Lower-frequency info pages (pinned; not customizable).
const SYSTEM = [
  { href: '/tasks', label: 'Tasks', icon: ListChecks },
  { href: '/about', label: 'About', icon: Info },
];

function isActive(location: string, href: string, exact?: boolean): boolean {
  if (exact) return location === href;
  return location === href || location.startsWith(href + '/');
}

interface SidebarProps {
  /** Mobile drawer open state. Ignored on desktop (always visible). */
  open: boolean;
  /** Close the mobile drawer (Escape, scrim click, close button). */
  onClose: () => void;
  onNavigate: () => void;
}

export function Sidebar({ open, onClose, onNavigate }: SidebarProps) {
  const [location] = useLocation();
  const t = useT();
  const isMobile = useIsMobile();
  const navRef = useRef<HTMLElement>(null);
  const [customizeOpen, setCustomizeOpen] = useState(false);

  // C5 (SC 2.1.1/2.1.2/2.4.3): on mobile the drawer is off-canvas. When CLOSED it
  // must leave the tab order + a11y tree; when OPEN it traps focus and Escape
  // closes it. On desktop it's a persistent rail — never inert, never trapped.
  // `inert` is set imperatively to avoid React-18 non-boolean-attr console warns.
  useEffect(() => {
    const node = navRef.current;
    if (!node) return;
    if (isMobile && !open) node.setAttribute('inert', '');
    else node.removeAttribute('inert');
  }, [isMobile, open]);

  useFocusTrap(navRef, { onClose, active: isMobile && open });
  const { data: shelvesData } = useShelves();
  const shelves = shelvesData?.items ?? [];
  const magicShelves = useMagicShelves().data?.items ?? [];
  const me = useMe().data;
  const canUpload = !!me?.role?.upload;
  const isAdmin = !!me?.role?.admin;
  const isAuthed = !!me?.id;

  // #585: honour the classic sidebar-visibility config. A key is hidden only
  // when the server explicitly reports it disabled (=== false); missing keys
  // (older server, or non-configurable entries) stay visible.
  const sidebarVis = me?.sidebar;
  const isVisible = (vis?: string) => !vis || sidebarVis?.[vis] !== false;
  const showList = isVisible('list');
  const showDuplicates = isVisible('duplicates');

  // #585 v2: the browse-by + discovery entries and the Shelves block render in
  // the user's saved order (default when unset). Contiguous runs of nav entries
  // are wrapped in their own <ul role="list"> (valid list semantics), and the
  // Shelves block is emitted inline at its ordered position.
  const orderedEntries = resolveSidebarOrder(me?.sidebar_order);

  const renderShelvesBlock = () => (
    <Fragment key="shelves-block">
      <div className={styles.sectionHeader}>
        <Link
          href="/shelves"
          className={isActive(location, '/shelves', true) ? styles.sectionTitleActive : styles.sectionTitle}
          onClick={onNavigate}
        >
          <BookCopy size={16} className={styles.icon} aria-hidden="true" focusable={false} />
          <span>{t('Shelves')}</span>
        </Link>
      </div>
      {shelves.length > 0 && (
        <ul className={styles.shelfList} role="list">
          {shelves.map((s) => {
            const href = `/shelf/${s.id}`;
            const active = location === href;
            return (
              <li key={s.id}>
                <Link
                  href={href}
                  className={active ? styles.shelfItemActive : styles.shelfItem}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                  title={s.name}
                >
                  <span className={styles.shelfName}>{s.name}</span>
                  <span className={styles.shelfCount}>{s.count}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </Fragment>
  );

  const renderOrderedRegion = () => {
    const nodes: React.ReactNode[] = [];
    let run: SidebarEntryDef[] = [];
    const flushRun = (id: string) => {
      if (run.length === 0) return;
      const items = run;
      run = [];
      nodes.push(
        <ul key={`run-${id}`} className={styles.list} role="list">
          {items.map(({ key, href, label, icon: Icon, exact }) => {
            const active = isActive(location, href, exact);
            return (
              <li key={key}>
                <Link
                  href={href}
                  className={active ? styles.itemActive : styles.item}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <Icon size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                  <span>{t(label)}</span>
                </Link>
              </li>
            );
          })}
        </ul>,
      );
    };
    orderedEntries.forEach((entry, idx) => {
      if (entry.isShelvesBlock) {
        flushRun(String(idx));
        nodes.push(renderShelvesBlock());
      } else if (isVisible(entry.key)) {
        run.push(entry);
      }
    });
    flushRun('end');
    return nodes;
  };

  const openCustomize = () => {
    onClose();            // close the mobile drawer first so only the modal traps focus
    setCustomizeOpen(true);
  };

  return (
    <>
      {open && <div className={styles.scrim} onClick={onClose} aria-hidden="true" />}
      <nav
        ref={navRef}
        className={open ? styles.navOpen : styles.nav}
        aria-label={t('Browse')}
        tabIndex={-1}
      >
        {/* Mobile-only close affordance (labelled); hidden on the desktop rail. */}
        <button type="button" className={styles.drawerClose} onClick={onClose} aria-label={t('Close menu')}>
          <X size={20} aria-hidden="true" focusable={false} />
        </button>

        {/* Library — pinned at the top, always shown. */}
        <ul className={styles.list} role="list">
          <li>
            <Link
              href="/"
              className={isActive(location, '/', true) ? styles.itemActive : styles.item}
              aria-current={isActive(location, '/', true) ? 'page' : undefined}
              onClick={onNavigate}
            >
              <Library size={18} className={styles.icon} aria-hidden="true" focusable={false} />
              <span>{t('Library')}</span>
            </Link>
          </li>
        </ul>

        {/* #585 v2: customizable region (browse-by + discovery + Shelves), in order. */}
        {renderOrderedRegion()}

        {(canUpload || isAdmin) && (
          <ul className={styles.list} role="list">
            {canUpload && (
              <li>
                <Link
                  href="/upload"
                  className={isActive(location, '/upload', true) ? styles.itemActive : styles.item}
                  aria-current={isActive(location, '/upload', true) ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <UploadCloud size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                  <span>{t('Upload')}</span>
                </Link>
              </li>
            )}
            {isAdmin && (
              <li>
                <Link
                  href="/admin"
                  className={isActive(location, '/admin', true) ? styles.itemActive : styles.item}
                  aria-current={isActive(location, '/admin', true) ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <Shield size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                  <span>{t('Admin')}</span>
                </Link>
              </li>
            )}
          </ul>
        )}

        {/* Smart shelves + power features (pinned). */}
        <ul className={styles.list} role="list">
          {showList && (
            <li>
              <Link
                href="/table"
                className={isActive(location, '/table', true) ? styles.itemActive : styles.item}
                aria-current={isActive(location, '/table', true) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Table2 size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                <span>{t('Table view')}</span>
              </Link>
            </li>
          )}
          <li>
            <Link
              href="/magic"
              className={isActive(location, '/magic', true) ? styles.itemActive : styles.item}
              aria-current={isActive(location, '/magic', true) ? 'page' : undefined}
              onClick={onNavigate}
            >
              <Wand2 size={18} className={styles.icon} aria-hidden="true" focusable={false} />
              <span>{t('Smart shelves')}</span>
            </Link>
          </li>
          {magicShelves.map((ms) => {
            const href = `/magic/${ms.id}`;
            const active = location === href;
            return (
              <li key={`ms-${ms.id}`}>
                <Link
                  href={href}
                  className={active ? styles.shelfItemActive : styles.shelfItem}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                  title={ms.name}
                >
                  <span className={styles.shelfName}>{ms.icon} {ms.name}</span>
                </Link>
              </li>
            );
          })}
          {(canUpload || isAdmin) && showDuplicates && (
            <li>
              <Link
                href="/duplicates"
                className={isActive(location, '/duplicates', true) ? styles.itemActive : styles.item}
                aria-current={isActive(location, '/duplicates', true) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Files size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                <span>{t('Duplicates')}</span>
              </Link>
            </li>
          )}
        </ul>

        {/* Low-frequency info pages, last. */}
        <ul className={styles.list} role="list">
          {SYSTEM.map(({ href, label, icon: Icon }) => {
            const active = isActive(location, href, true);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={active ? styles.itemActive : styles.item}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <Icon size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                  <span>{t(label)}</span>
                </Link>
              </li>
            );
          })}
        </ul>

        {/* #585 v2: open the Customize editor (visibility + order). Signed-in
            users only — the settings are per-user and saved server-side. */}
        {isAuthed && (
          <div className={styles.customizeRow}>
            <button type="button" className={styles.customizeBtn} onClick={openCustomize}>
              <Settings2 size={16} className={styles.icon} aria-hidden="true" focusable={false} />
              <span>{t('Customize sidebar')}</span>
            </button>
          </div>
        )}
      </nav>

      <SidebarCustomize open={customizeOpen} onClose={() => setCustomizeOpen(false)} />
    </>
  );
}
