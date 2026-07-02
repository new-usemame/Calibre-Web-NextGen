import { Link, useLocation } from 'wouter';
import {
  Library, Users, Layers, Tag, Building2, Languages, BookCopy, UploadCloud, Shield,
  Flame, Shuffle, Star, Archive, Info, ListChecks, Table2, Wand2, Files, FileType,
} from 'lucide-react';
import { useShelves, useMe, useMagicShelves } from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './Sidebar.module.css';

// `vis` is the sidebar-visibility key (#585) the entry is gated on, matching a
// key in Me.sidebar (server-side check_visibility on the sidebar_view bitmask).
// Entries without a `vis` (e.g. Library) are always shown.
interface NavEntry {
  href: string;
  label: string;
  icon: typeof Library;
  exact?: boolean;
  vis?: string;
}

const NAV: NavEntry[] = [
  { href: '/', label: 'Library', icon: Library, exact: true },
  { href: '/authors', label: 'Authors', icon: Users, vis: 'author' },
  { href: '/series', label: 'Series', icon: Layers, vis: 'series' },
  { href: '/tags', label: 'Tags', icon: Tag, vis: 'category' },
  { href: '/publishers', label: 'Publishers', icon: Building2, vis: 'publisher' },
  { href: '/languages', label: 'Languages', icon: Languages, vis: 'language' },
  { href: '/ratings', label: 'Ratings', icon: Star, vis: 'rating' },
  { href: '/formats', label: 'Formats', icon: FileType, vis: 'format' },
];

// Discovery views — fixed server-side filter categories (parity with the
// legacy sidebar's Hot/Discover/Rated + per-user Favorites/Archived).
const DISCOVER: NavEntry[] = [
  { href: '/favorites', label: 'Favorites', icon: Star, vis: 'favorites' },
  { href: '/hot', label: 'Hot', icon: Flame, vis: 'hot' },
  { href: '/discover', label: 'Discover', icon: Shuffle, vis: 'random' },
  { href: '/rated', label: 'Top Rated', icon: Star, vis: 'best_rated' },
  { href: '/archived', label: 'Archived', icon: Archive, vis: 'archived' },
];

// Lower-frequency info pages.
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
  onNavigate: () => void;
}

export function Sidebar({ open, onNavigate }: SidebarProps) {
  const [location] = useLocation();
  const t = useT();
  const { data: shelvesData } = useShelves();
  const shelves = shelvesData?.items ?? [];
  const magicShelves = useMagicShelves().data?.items ?? [];
  const me = useMe().data;
  const canUpload = !!me?.role?.upload;
  const isAdmin = !!me?.role?.admin;

  // #585: honour the classic sidebar-visibility config. A key is hidden only
  // when the server explicitly reports it disabled (=== false); missing keys
  // (older server, or non-configurable entries) stay visible.
  const sidebarVis = me?.sidebar;
  const isVisible = (vis?: string) => !vis || sidebarVis?.[vis] !== false;
  const navEntries = NAV.filter((n) => isVisible(n.vis));
  const discoverEntries = DISCOVER.filter((n) => isVisible(n.vis));
  const showList = isVisible('list');
  const showDuplicates = isVisible('duplicates');

  return (
    <>
      {open && <div className={styles.scrim} onClick={onNavigate} aria-hidden="true" />}
      <nav className={open ? styles.navOpen : styles.nav} aria-label="Browse">
        <ul className={styles.list}>
          {navEntries.map(({ href, label, icon: Icon, exact }) => {
            const active = isActive(location, href, exact);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={active ? styles.itemActive : styles.item}
                  aria-current={active ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <Icon size={18} className={styles.icon} />
                  <span>{t(label)}</span>
                </Link>
              </li>
            );
          })}
        </ul>

        {discoverEntries.length > 0 && (
          <ul className={styles.list}>
            {discoverEntries.map(({ href, label, icon: Icon }) => {
              const active = isActive(location, href, true);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    className={active ? styles.itemActive : styles.item}
                    aria-current={active ? 'page' : undefined}
                    onClick={onNavigate}
                  >
                    <Icon size={18} className={styles.icon} />
                    <span>{t(label)}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}

        {(canUpload || isAdmin) && (
          <ul className={styles.list}>
            {canUpload && (
              <li>
                <Link
                  href="/upload"
                  className={isActive(location, '/upload', true) ? styles.itemActive : styles.item}
                  aria-current={isActive(location, '/upload', true) ? 'page' : undefined}
                  onClick={onNavigate}
                >
                  <UploadCloud size={18} className={styles.icon} />
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
                  <Shield size={18} className={styles.icon} />
                  <span>{t('Admin')}</span>
                </Link>
              </li>
            )}
          </ul>
        )}

        {/* Shelves: header links to the manage page; user's shelves listed below. */}
        <div className={styles.sectionHeader}>
          <Link
            href="/shelves"
            className={isActive(location, '/shelves', true) ? styles.sectionTitleActive : styles.sectionTitle}
            onClick={onNavigate}
          >
            <BookCopy size={16} className={styles.icon} />
            <span>{t('Shelves')}</span>
          </Link>
        </div>

        <ul className={styles.list}>
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
                  <Icon size={18} className={styles.icon} />
                  <span>{t(label)}</span>
                </Link>
              </li>
            );
          })}
        </ul>

        {/* Power features served by the legacy UI under the hybrid cutover —
            plain <a> so they leave the SPA. Reachable, not omitted. */}
        <ul className={styles.list}>
          {showList && (
            <li>
              <Link
                href="/table"
                className={isActive(location, '/table', true) ? styles.itemActive : styles.item}
                aria-current={isActive(location, '/table', true) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Table2 size={18} className={styles.icon} />
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
              <Wand2 size={18} className={styles.icon} />
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
                <Files size={18} className={styles.icon} />
                <span>{t('Duplicates')}</span>
              </Link>
            </li>
          )}
        </ul>

        {shelves.length > 0 && (
          <ul className={styles.shelfList}>
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
      </nav>
    </>
  );
}
