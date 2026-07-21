import { useState, useRef, useEffect, useCallback, type ReactNode } from 'react';
import { BookMarked, LogIn, LogOut, Menu, Search, ChevronDown, User, Bug, BookOpen, Undo2, Sparkles, Shield } from 'lucide-react';
import { Link, useLocation, useSearch } from 'wouter';
import { GithubMark, DiscordMark } from './BrandIcons';
import { KofiMark, KOFI_URL } from './KofiMark';
import { BrandName } from './BrandName';
import { Avatar } from './Avatar';
import { BASE_PREFIX } from '../lib/api';
import { useMe } from '../lib/queries';
import { AUTH_ROUTES } from '../lib/routes';
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
  // /issues/new/choose lands on the template picker (Bug report / Feature
  // request) instead of a blank issue — the chooser lists the forms in
  // .github/ISSUE_TEMPLATE/ (#799, adopts @chloeroform's #800).
  issue: 'https://github.com/new-usemame/Calibre-Web-NextGen/issues/new/choose',
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
  const triggerRef = useRef<HTMLButtonElement>(null);
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
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      close();
      triggerRef.current?.focus();
    };
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

  return {
    open,
    close,
    ref,
    triggerRef,
    wrapperProps: { ref, onMouseEnter, onMouseLeave },
    onTriggerClick,
  };
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
  // Disclosure pattern (not an ARIA menu): plain links/buttons, icons decorative.
  const inner = (
    <>
      <span className={styles.menuItemIcon} aria-hidden="true">{icon}</span>
      <span className={styles.menuItemLabel}>{label}</span>
      {trailing}
    </>
  );
  if (to) {
    // Internal: wouter Link keeps it client-side (no full reload) and respects the base.
    return (
      <Link href={to} className={cls} onClick={onSelect}>
        {inner}
      </Link>
    );
  }
  if (href) {
    return (
      <a className={cls} href={href} target="_blank" rel="noopener noreferrer" onClick={onSelect}>
        {inner}
      </a>
    );
  }
  return (
    <button type="button" className={cls} onClick={handle}>
      {inner}
    </button>
  );
}

function HelpMenu() {
  const t = useT();
  const { open, close, triggerRef, wrapperProps, onTriggerClick } = useMenu();
  const unread = useWhatsNewUnread();
  return (
    <div className={styles.menu} {...wrapperProps}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.triggerSquare}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={unread ? t('Help — new updates available') : t('Help')}
        onClick={onTriggerClick}
      >
        <span className={styles.qmark} aria-hidden="true">?</span>
        {unread && <span className={styles.triggerDot} aria-hidden="true" />}
      </button>
      {open && (
        <div className={`${styles.panel} ${styles.panelHelp}`}>
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
          <MenuItem icon={<KofiMark size={16} />} label={t('Support on Ko-fi →')} href={KOFI_URL} onSelect={close} />
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
  const { open, close, triggerRef, wrapperProps, onTriggerClick } = useMenu();
  const me = useMe().data;
  const avatar = me?.avatar;
  // #659/#720: admins looked for the Admin/Settings entry in the account menu (the
  // conventional place) and only found it buried in the sidebar rail, so several
  // switched back to the classic UI. Surface it here too, role-gated, pointing at
  // the SPA's own /admin route (same target as the sidebar link).
  const isAdmin = !!me?.role?.admin;
  // #1023: with anonymous browsing on, /me now answers for the Guest row, so a
  // visitor who has NOT signed in still reaches this menu. "My account" 401s for
  // them and "Sign out" has no session to end -- offer the one action that makes
  // sense instead. `me` being non-null no longer implies "signed in"; role
  // .anonymous is the discriminator.
  const isGuest = !!me?.role?.anonymous;
  return (
    <div className={styles.menu} {...wrapperProps}>
      <button
        ref={triggerRef}
        type="button"
        className={`${styles.trigger} ${open ? styles.triggerOpen : ''}`}
        aria-haspopup="true"
        aria-expanded={open}
        // The visible userName label is display:none <420px; keep a stable name.
        aria-label={t('Account: {name}', { name: userName })}
        onClick={onTriggerClick}
      >
        <Avatar src={avatar} size={20} className={styles.triggerLeadIcon} />
        <span className={styles.triggerLabel}>{userName}</span>
        <ChevronDown size={15} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} aria-hidden="true" focusable={false} />
      </button>
      {open && (
        <div className={styles.panel}>
          {!isGuest && (
            <MenuItem icon={<User size={15} />} label={t('My account')} to="/account" onSelect={close} />
          )}
          {isAdmin && (
            <MenuItem icon={<Shield size={15} />} label={t('Admin')} to="/admin" onSelect={close} />
          )}
          <MenuItem icon={<Undo2 size={15} />} label={t('Back to the classic view')} onClick={backToClassicView} onSelect={close} />
          {isGuest ? (
            <MenuItem icon={<LogIn size={15} />} label={t('Sign in')} to={AUTH_ROUTES.login} onSelect={close} />
          ) : (
            <MenuItem icon={<LogOut size={15} />} label={t('Sign out')} danger onClick={onLogout} onSelect={close} />
          )}
        </div>
      )}
    </div>
  );
}

export function TopBar({ userName, instanceName, onLogout, onMenu }: TopBarProps) {
  const t = useT();
  const [, setLocation] = useLocation();
  const rawSearch = useSearch();
  const urlQ = new URLSearchParams(rawSearch).get('q') || '';
  const [q, setQ] = useState(urlQ);
  useEffect(() => setQ(urlQ), [urlQ]);
  // The search bar is hidden on narrow screens; a toggle button reveals it as an
  // overlay row so mobile users can still search (Tier-3 gap).
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const onSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const term = q.trim();
    setLocation(term ? `/?q=${encodeURIComponent(term)}` : '/');
    setMobileSearchOpen(false);
  };
  const toggleMobileSearch = () => {
    setMobileSearchOpen((open) => {
      const next = !open;
      if (next) setTimeout(() => searchInputRef.current?.focus(), 0);
      return next;
    });
  };
  return (
    <header className={styles.bar}>
      <div className={styles.left}>
        {onMenu && (
          <button className={styles.menuBtn} onClick={onMenu} aria-label={t('Open navigation')}>
            <Menu size={20} aria-hidden="true" focusable={false} />
          </button>
        )}
        <Link href="/" className={styles.brand}>
          <BookMarked size={22} className={styles.brandIcon} aria-hidden="true" focusable={false} />
          <span className={`${styles.brandText} ${styles.brandMain}`}>
            <BrandName name={instanceName} accentClassName={styles.brandAccent} />
          </span>
        </Link>
      </div>
      <form
        className={`${styles.search} ${mobileSearchOpen ? styles.searchMobileOpen : ''}`}
        onSubmit={onSearch}
        role="search"
      >
        <Search size={16} className={styles.searchIcon} aria-hidden="true" focusable={false} />
        <input
          ref={searchInputRef}
          type="search"
          className={styles.searchInput}
          placeholder={t('Search title, author…')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') setMobileSearchOpen(false); }}
          aria-label={t('Search the library')}
        />
      </form>
      <div className={styles.right}>
        <button
          type="button"
          className={styles.mobileSearchBtn}
          onClick={toggleMobileSearch}
          aria-label={t('Search the library')}
          aria-expanded={mobileSearchOpen}
        >
          <Search size={20} aria-hidden="true" focusable={false} />
        </button>
        <HelpMenu />
        <UserMenu userName={userName} onLogout={onLogout} />
      </div>
    </header>
  );
}
