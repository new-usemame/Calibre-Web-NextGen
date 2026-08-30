import { Fragment, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import {
  Library, Globe, BookCopy,
  Info, ListChecks, Table2, Wand2, Files, SlidersHorizontal, Check, RotateCcw, X, Pin, PinOff,
} from 'lucide-react';
import { useShelves, useMe, useMagicShelves, useUpdateSidebar } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useIsDrawerMode } from '../lib/a11y/useIsDrawerMode';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useMediaQuery } from '../lib/useMediaQuery';
import { usePersistentBool } from '../lib/usePersistentBool';
import {
  resolveSidebarOrder, ORDERABLE_ENTRIES, DEFAULT_SIDEBAR_ORDER, type SidebarEntryDef,
} from '../lib/sidebarEntries';
import { SidebarEditList } from './SidebarEditList';
import styles from './Sidebar.module.css';

// Lower-frequency info pages (pinned; not customizable).
const SYSTEM = [
  { href: '/tasks', label: 'Tasks', icon: ListChecks },
  { href: '/about', label: 'About', icon: Info },
];

const DESKTOP_RAIL_QUERY = '(min-width: 768px) and (hover: hover) and (pointer: fine)';
const SIDEBAR_PIN_KEY = 'cwng:sidebar-pinned';

function isActive(location: string, href: string, exact?: boolean): boolean {
  if (exact) return location === href;
  return location === href || location.startsWith(href + '/');
}

interface SidebarProps {
  /** Off-canvas drawer open state. Ignored by the persistent desktop rail. */
  open: boolean;
  /** Close the drawer (Escape, scrim click, close button). */
  onClose: () => void;
  onNavigate: () => void;
}

export function Sidebar({ open, onClose, onNavigate }: SidebarProps) {
  const [location] = useLocation();
  const t = useT();
  const isDrawerMode = useIsDrawerMode();
  const announce = useAnnouncer();
  const navRef = useRef<HTMLElement>(null);
  const update = useUpdateSidebar();
  const isDesktopRail = useMediaQuery(DESKTOP_RAIL_QUERY);
  const [sidebarPinned, setSidebarPinned] = usePersistentBool(SIDEBAR_PIN_KEY, false);
  const [hoverSuppressed, setHoverSuppressed] = useState(false);
  const hoverExitObserved = useRef(false);

  // #585 v3: inline sidebar edit mode (toggled by the Customize capsule).
  const [editMode, setEditMode] = useState(false);
  const [order, setOrder] = useState<string[]>([]);
  const [vis, setVis] = useState<Record<string, boolean>>({});
  const capsuleRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLButtonElement>(null);
  const editModeMounted = useRef(false);

  // Move keyboard focus with the mode change (the clicked control unmounts):
  // entering edit → the Done pill; leaving → back to the Customize capsule.
  // Skip the first run so we don't steal focus on initial mount.
  useEffect(() => {
    if (!editModeMounted.current) { editModeMounted.current = true; return; }
    if (editMode) doneRef.current?.focus();
    else capsuleRef.current?.focus();
  }, [editMode]);

  useEffect(() => {
    const node = navRef.current;
    if (!node) return;
    if (isDrawerMode && !open) node.setAttribute('inert', '');
    else node.removeAttribute('inert');
  }, [isDrawerMode, open]);

  useEffect(() => {
    if (!hoverSuppressed) return;

    // A route click can leave the pointer either inside the 64px rail or over
    // the overlay strip that is about to disappear. Keep stale hover suppressed
    // for the pointer's whole journey out; only a later return to the nav is
    // fresh hover intent. A layout-induced pointerleave is deliberately ignored.
    const trackPointerJourney = (event: PointerEvent) => {
      const node = navRef.current;
      if (!node) return;
      const pointerInsideNav = event.composedPath().includes(node);

      if (!hoverExitObserved.current) {
        if (!pointerInsideNav) hoverExitObserved.current = true;
        return;
      }

      if (pointerInsideNav) setHoverSuppressed(false);
    };

    window.addEventListener('pointermove', trackPointerJourney, true);
    return () => window.removeEventListener('pointermove', trackPointerJourney, true);
  }, [hoverSuppressed]);

  useFocusTrap(navRef, { onClose, active: isDrawerMode && open });
  const { data: shelvesData } = useShelves();
  const shelves = shelvesData?.items ?? [];
  const magicShelves = useMagicShelves().data?.items ?? [];
  const me = useMe().data;
  const canEdit = !!me?.role?.edit;
  const isAdmin = !!me?.role?.admin;
  const isAuthed = !!me?.id;
  const personalLibrary = me?.library_mode === 'personal_library';
  const showGlobalLibrary = personalLibrary && !!me?.role?.browse_global;
  const pinActive = isDesktopRail && sidebarPinned;
  const pinLabel = sidebarPinned ? t('Unpin sidebar') : t('Pin sidebar');

  const toggleSidebarPin = () => {
    const next = !sidebarPinned;
    setSidebarPinned(next);
    if (next) {
      setHoverSuppressed(false);
    } else {
      // The unpin click leaves both the pointer and focus inside the rail. Move
      // focus to the page and suppress hover so it collapses now. Unlike a route
      // click, shrinking the reserved flow box from 220px to 64px guarantees the
      // pointer has geometrically exited the rail; record that layout-induced
      // exit so the next sampled pointermove can restore hover immediately.
      hoverExitObserved.current = true;
      setHoverSuppressed(true);
      document.getElementById('main')?.focus();
    }
    announce(next ? t('Sidebar pinned.') : t('Sidebar unpinned.'));
  };

  const sidebarVis = me?.sidebar;
  const isVisible = (v?: string) => !v || sidebarVis?.[v] !== false;
  const showList = isVisible('list');
  const showDuplicates = isVisible('duplicates');
  const orderedEntries = resolveSidebarOrder(me?.sidebar_order);

  // ── edit-mode lifecycle ──────────────────────────────────────────────────
  const enterEdit = () => {
    setOrder(resolveSidebarOrder(me?.sidebar_order).map((e) => e.key));
    const v: Record<string, boolean> = {};
    for (const e of ORDERABLE_ENTRIES) {
      if (!e.isShelvesBlock) v[e.key] = me?.sidebar?.[e.key] !== false;
    }
    v.list = me?.sidebar?.list !== false;
    setVis(v);
    setEditMode(true);
    announce(t('Editing sidebar. Reorder or hide sections, then tap Done.'));
  };
  const saveEdit = () => {
    update.mutate({ visibility: vis, order }, {
      onSuccess: () => announce(t('Sidebar saved.')),
      onError: () => announce(t('Could not save sidebar. Please try again.'), { assertive: true }),
    });
    setEditMode(false);
  };
  const cancelEdit = () => { setEditMode(false); announce(t('Editing cancelled.')); };
  const resetEdit = () => {
    setOrder([...DEFAULT_SIDEBAR_ORDER]);
    const v: Record<string, boolean> = {};
    for (const e of ORDERABLE_ENTRIES) if (!e.isShelvesBlock) v[e.key] = true;
    v.list = true;
    setVis(v);
    announce(t('Sidebar reset to default.'));
  };

  // ── normal-mode ordered region (browse/discovery + Shelves block, in order) ─
  const renderShelvesBlock = () => (
    <Fragment key="shelves-block">
      <div>
        <Link
          href="/shelves"
          className={isActive(location, '/shelves', true) ? styles.itemActive : styles.item}
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

  return (
    <>
      {open && <div className={styles.scrim} onClick={onClose} aria-hidden="true" />}
      <div className={`${styles.rail}${pinActive ? ` ${styles.railPinned}` : ''}`}>
        <nav
          ref={navRef}
          className={`${open ? styles.navOpen : styles.nav}${hoverSuppressed ? ` ${styles.hoverSuppressed}` : ''}${pinActive ? ` ${styles.pinned}` : ''}`}
          aria-label={t('Browse')}
          tabIndex={-1}
          onClickCapture={(event) => {
            if (event.target instanceof Element && event.target.closest('a[href]')) {
              hoverExitObserved.current = false;
              setHoverSuppressed(true);
            }
          }}
        >
        {/* Mobile-only close affordance (labelled); hidden on the desktop rail. */}
        <button type="button" className={styles.drawerClose} onClick={onClose} aria-label={t('Close menu')}>
          <X size={20} aria-hidden="true" focusable={false} />
        </button>

        {isDesktopRail && (
          <div className={styles.pinRow}>
            <button
              type="button"
              className={styles.pinButton}
              onClick={toggleSidebarPin}
              aria-label={pinLabel}
              aria-pressed={sidebarPinned}
              title={pinLabel}
            >
              {sidebarPinned
                ? <PinOff size={16} aria-hidden="true" focusable={false} />
                : <Pin size={16} aria-hidden="true" focusable={false} />}
              <span>{pinLabel}</span>
            </button>
          </div>
        )}

        {/* #585 v3: liquid-glass Customize capsule, pinned at the top. Tapping it
            turns the sidebar into an editable list (reorder + hide entries). */}
        {isAuthed && editMode && (
          <div className={styles.capsuleWrap}>
              <div className={styles.capsuleActive} role="group" aria-label={t('Customize navigation')}>
                <button type="button" ref={doneRef} className={styles.capsuleDone} onClick={saveEdit}>
                  <Check size={16} aria-hidden="true" focusable={false} />
                  <span>{t('Done')}</span>
                </button>
                <button type="button" className={styles.capsuleGhost} onClick={resetEdit} aria-label={t('Reset to default')}>
                  <RotateCcw size={15} aria-hidden="true" focusable={false} />
                </button>
                <button type="button" className={styles.capsuleGhost} onClick={cancelEdit} aria-label={t('Cancel')}>
                  <X size={16} aria-hidden="true" focusable={false} />
                </button>
              </div>
          </div>
        )}

        {editMode ? (
          <>
            <p className={styles.editHint}>
              {t('Drag to reorder. Tap ✕ to hide a section. Arrow keys move the focused handle.')}
            </p>
            <SidebarEditList order={order} setOrder={setOrder} vis={vis} setVis={setVis} />
            <label className={styles.tableVisibility}>
              <input type="checkbox" checked={vis.list !== false}
                onChange={(event) => setVis((current) => ({ ...current, list: event.target.checked }))} />
              <Table2 size={16} aria-hidden="true" focusable={false} />
              <span>{t('Show Table view')}</span>
            </label>
          </>
        ) : (
          <>
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
                  <span>{t(personalLibrary ? 'My Library' : 'Library')}</span>
                </Link>
              </li>
              {showGlobalLibrary && (
                <li>
                  <Link href="/global"
                    className={isActive(location, '/global') ? styles.itemActive : styles.item}
                    aria-current={isActive(location, '/global') ? 'page' : undefined}
                    onClick={onNavigate}>
                    <Globe size={18} className={styles.icon} aria-hidden="true" focusable={false} />
                    <span>{t('Global Library')}</span>
                  </Link>
                </li>
              )}
            </ul>

            {/* Customizable region (browse-by + discovery + Shelves), in saved order. */}
            {renderOrderedRegion()}

            {/* Smart shelves + power features (pinned). */}
            <ul className={styles.list} role="list">
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
                      className={`${active ? styles.shelfItemActive : styles.shelfItem} ${styles.magicShelfItem}`}
                      aria-current={active ? 'page' : undefined}
                      aria-label={ms.name}
                      onClick={onNavigate}
                      title={ms.name}
                    >
                      <span className={styles.magicShelfIcon} aria-hidden="true">{ms.icon}</span>
                      <span className={styles.magicShelfName}>{ms.name}</span>
                    </Link>
                  </li>
                );
              })}
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
              {(canEdit || isAdmin) && showDuplicates && (
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
            {isAuthed && (
              <div className={styles.customizeRow}>
                <button
                  type="button"
                  ref={capsuleRef}
                  className={styles.customizeBtn}
                  onClick={enterEdit}
                  aria-label={t('Customize navigation')}
                  title={t('Customize navigation')}
                >
                  <SlidersHorizontal size={16} aria-hidden="true" focusable={false} />
                  <span>{t('Customize navigation')}</span>
                </button>
              </div>
            )}
          </>
        )}
        </nav>
      </div>
    </>
  );
}
