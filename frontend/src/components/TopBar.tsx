import { useState, useRef, useEffect, useCallback, type ReactNode } from 'react';
import { BookMarked, LogOut, Menu, Search, ChevronDown, User, Bug, BookOpen, Undo2, Sparkles } from 'lucide-react';
import { Link, useLocation } from 'wouter';
import { GithubMark, DiscordMark } from './BrandIcons';
import { BrandName } from './BrandName';
import { BASE_PREFIX } from '../lib/api';
import { useT } from '../lib/i18n';
import { useWhatsNewUnread } from '../lib/whatsNew';
import styles from './TopBar.module.css';

interface TopBarProps {
  userName: string;
  instanceName?: string;
  onLogout: () => void;
  onMenu?: () => void;
}

/** Project support channels surfaced in the Help menu — the fork's own GitHub
 *  tracker + Discord (already shipped in the legacy admin page) + README. */
const HELP_LINKS = {
  issue: 'https://github.com/new-usemame/Calibre-Web-NextGen/issues/new',
  discord: 'https://discord.gg/B8NXZmcp32',
  docs: 'https://github.com/new-usemame/Calibre-Web-NextGen#readme',
};

/** Shared open/close behaviour for the top-bar menus: opens on hover (desktop)
 *  AND on click/tap (so it works on touch devices with no hover), pins open once
 *  clicked, and closes on outside-click, Escape, or pointer-leave (when not
 *  pinned). Returns the props to spread on the wrapper + trigger. */
function useMenu() {
  const [open, setOpen] = useState(false);
  const pinnedRef = useRef(false);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    pinnedRef.current = false;
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDocPointer = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('pointerdown', onDocPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDocPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);

  const clearClose = () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  const onMouseEnter = () => { clearClose(); setOpen(true); };
  const onMouseLeave = () => {
    if (pinnedRef.current) return;
    clearClose();
    closeTimer.current = setTimeout(() => setOpen(false), 140);
  };
  const onTriggerClick = () => {
    const next = !open;
    pinnedRef.current = next;
    setOpen(next);
  };

  return { open, close, ref, wrapperProps: { ref, onMouseEnter, onMouseLeave }, onTriggerClick };
}

/** A primary glyph with a small brand sub-badge pinned bottom-right — used for the
 *  "Report Issue on …" items (a bug + the GitHub/Discord mark it routes to). */
function IconWithBadge({ base, badge }: { base: ReactNode; badge: ReactNode }) {
  return (
    <span className={styles.iconBadgeWrap}>
      {base}
      <span className={styles.iconBadge}>{badge}</span>
    </span>
  );
}

interface MenuItemProps {
  icon: ReactNode;
  label: string;
  /** Internal SPA route (wouter, relative to the /app base) — client-side nav. */
  to?: string;
  /** External URL — opens in a new tab. */
  href?: string;
  danger?: boolean;
  /** Optional trailing element (e.g. an unread dot), pinned to the right. */
  trailing?: ReactNode;
  onClick?: () => void;
  onSelect: () => void;
}

function MenuItem({ icon, label, to, href, danger, trailing, onClick, onSelect }: MenuItemProps) {
  const cls = danger ? `${styles.menuItem} ${styles.menuItemDanger}` : styles.menuItem;
  const handle = () => { onClick?.(); onSelect(); };
  const inner = (
    <>
      <span className={styles.menuItemIcon}>{icon}</span>
      <span className={styles.menuItemLabel}>{label}</span>
      {trailing}
    </>
  );
  if (to) {
    // Internal: wouter Link keeps it client-side (no full reload) and respects the base.
    return (
      <Link href={to} role="menuitem" className={cls} onClick={onSelect}>
        {inner}
      </Link>
    );
  }
  if (href) {
    return (
      <a role="menuitem" className={cls} href={href} target="_blank" rel="noopener noreferrer" onClick={onSelect}>
        {inner}
      </a>
    );
  }
  return (
    <button role="menuitem" type="button" className={cls} onClick={handle}>
      {inner}
    </button>
  );
}

function HelpMenu() {
  const t = useT();
  const { open, close, wrapperProps, onTriggerClick } = useMenu();
  const unread = useWhatsNewUnread();
  return (
    <div className={styles.menu} {...wrapperProps}>
      <button
        type="button"
        className={styles.triggerSquare}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={unread ? t('Help — new updates available') : t('Help')}
        onClick={onTriggerClick}
      >
        <span className={styles.qmark} aria-hidden="true">?</span>
        {unread && <span className={styles.triggerDot} aria-hidden="true" />}
      </button>
      {open && (
        <div className={`${styles.panel} ${styles.panelHelp}`} role="menu">
          <p className={styles.panelHead}>{t('Help & support')}</p>
          <MenuItem
            icon={<Sparkles size={15} />}
            label={t("What's new")}
            to="/whats-new"
            trailing={unread ? <span className={styles.itemDot} aria-hidden="true" /> : undefined}
            onSelect={close} />
          <MenuItem
            icon={<IconWithBadge base={<Bug size={16} />} badge={<GithubMark />} />}
            label={t('Report Issue on GitHub')} href={HELP_LINKS.issue} onSelect={close} />
          <MenuItem
            icon={<IconWithBadge base={<Bug size={16} />} badge={<DiscordMark />} />}
            label={t('Report Issue on Discord')} href={HELP_LINKS.discord} onSelect={close} />
          <MenuItem icon={<DiscordMark size={15} />} label={t('Ask in Discord')} href={HELP_LINKS.discord} onSelect={close} />
          <MenuItem icon={<BookOpen size={15} />} label={t('Documentation')} href={HELP_LINKS.docs} onSelect={close} />
        </div>
      )}
    </div>
  );
}

/** Leave the SPA and return to the classic (legacy) interface. Uses a full-page
 *  navigation (not wouter) because the classic UI is a separate server-rendered
 *  surface, and appends a one-shot marker so the classic page offers the short
 *  "what made you switch back?" feedback prompt on arrival. The base prefix (if
 *  the app is served under a reverse-proxy subpath, before /app) is preserved. */
function backToClassicView() {
  window.location.assign(BASE_PREFIX + '/?cwng_feedback=newui');
}

function UserMenu({ userName, onLogout }: { userName: string; onLogout: () => void }) {
  const t = useT();
  const { open, close, wrapperProps, onTriggerClick } = useMenu();
  return (
    <div className={styles.menu} {...wrapperProps}>
      <button
        type="button"
        className={`${styles.trigger} ${open ? styles.triggerOpen : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={onTriggerClick}
      >
        <User size={15} className={styles.triggerLeadIcon} />
        <span className={styles.triggerLabel}>{userName}</span>
        <ChevronDown size={15} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} />
      </button>
      {open && (
        <div className={styles.panel} role="menu">
          <MenuItem icon={<User size={15} />} label={t('My account')} to="/account" onSelect={close} />
          <MenuItem icon={<Undo2 size={15} />} label={t('Back to the classic view')} onClick={backToClassicView} onSelect={close} />
          <MenuItem icon={<LogOut size={15} />} label={t('Sign out')} danger onClick={onLogout} onSelect={close} />
        </div>
      )}
    </div>
  );
}

export function TopBar({ userName, instanceName, onLogout, onMenu }: TopBarProps) {
  const t = useT();
  const [, setLocation] = useLocation();
  const [q, setQ] = useState('');
  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    setLocation(term ? `/?q=${encodeURIComponent(term)}` : '/');
  };
  return (
    <header className={styles.bar}>
      <div className={styles.left}>
        {onMenu && (
          <button className={styles.menuBtn} onClick={onMenu} aria-label={t('Open navigation')}>
            <Menu size={20} />
          </button>
        )}
        <Link href="/" className={styles.brand}>
          <BookMarked size={22} className={styles.brandIcon} />
          <span className={`${styles.brandText} ${styles.brandMain}`}>
            <BrandName name={instanceName} accentClassName={styles.brandAccent} />
          </span>
        </Link>
      </div>
      <form className={styles.search} onSubmit={onSearch} role="search">
        <Search size={16} className={styles.searchIcon} />
        <input
          type="search"
          className={styles.searchInput}
          placeholder={t('Search title, author…')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label={t('Search the library')}
        />
      </form>
      <div className={styles.right}>
        <HelpMenu />
        <UserMenu userName={userName} onLogout={onLogout} />
      </div>
    </header>
  );
}
