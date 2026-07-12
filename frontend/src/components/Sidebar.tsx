import { Fragment, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'wouter';
import {
  Library, BookCopy,
  Info, ListChecks, Table2, Wand2, Files, SlidersHorizontal, Check, RotateCcw, X,
} from 'lucide-react';
import { useShelves, useMe, useMagicShelves, useUpdateSidebar } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useIsMobile } from '../lib/a11y/useIsMobile';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useAnnouncer } from '../lib/a11y/announcer';
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
  const announce = useAnnouncer();
  const navRef = useRef<HTMLElement>(null);
  const update = useUpdateSidebar();

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
                  <span>{t('Library')}</span>
                </Link>
              </li>
            </ul>

            {/* Customizable region (browse-by + discovery + Shelves), in saved order. */}
            {renderOrderedRegion()}

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
    </>
  );
}
