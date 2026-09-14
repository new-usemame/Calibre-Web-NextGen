import { useState, useRef, useEffect, useLayoutEffect, useCallback, useId, type ReactNode } from 'react';
import { Link } from 'wouter';
import styles from './Menu.module.css';

/* APG menu-button dropdown. The top-bar menus (TopBar.useMenu/MenuItem) are
   disclosure popovers by their own design note — plain links in a panel — and
   AddToShelf is a disclosure for the same reason (it holds toggles + a form).
   This component is the one place that implements the actual menu contract:
   role=menu/menuitem, roving tabindex, arrow/Home/End navigation, Escape and
   outside-pointer close, focus restore to the trigger. */

export interface MenuItemDef {
  id: string;
  label: string;
  /** Decorative leading icon. */
  icon?: ReactNode;
  /** Accessible name override when the visible label is not enough. */
  ariaLabel?: string;
  /** SPA route (wouter Link, client-side nav). */
  to?: string;
  /** Action callback; the menu closes first. */
  onSelect?: () => void;
  /** aria-disabled (stays focusable per APG), activation blocked. */
  disabled?: boolean;
  danger?: boolean;
  /** Trailing element pinned right (e.g. a count badge). */
  trailing?: ReactNode;
  testId?: string;
}

export interface MenuSectionDef {
  id: string;
  /** Small caption above the group (e.g. the admin-only marker); also the
   *  group's accessible label. */
  label?: string;
  items: MenuItemDef[];
  /** Separated destructive zone at the menu's foot. */
  danger?: boolean;
}

interface MenuProps {
  /** Accessible name of the trigger button (icon-only triggers need it). */
  label: string;
  /** Tooltip on the trigger. */
  title?: string;
  icon: ReactNode;
  sections: MenuSectionDef[];
  /** aria-label for the menu itself; defaults to the trigger label. */
  menuLabel?: string;
  triggerTestId?: string;
  menuTestId?: string;
  triggerClassName?: string;
}

export function Menu({ label, title, icon, sections, menuLabel, triggerTestId, menuTestId, triggerClassName }: MenuProps) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Horizontal position as an explicit px offset from the wrap's left edge,
  // computed once per open: right-anchored by default, left-anchored when that
  // would cross the left edge (wrapped row on a narrow phone), and clamped so
  // the right edge never crosses the viewport — a page a few px wider than the
  // viewport (CI #2237) must not take the menu off-screen with it.
  const [left, setLeft] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const close = useCallback((refocus = false) => {
    setOpen(false);
    setActive(-1);
    if (refocus) triggerRef.current?.focus();
  }, []);

  const openMenu = useCallback((fromEnd = false) => {
    setOpen(true);
    const count = sections.reduce((n, s) => n + s.items.length, 0);
    setActive(fromEnd ? count - 1 : 0);
  }, [sections]);

  // Outside pointer closes. Escape is handled on the wrapper keydown below, so
  // it works whether focus sits on the trigger or inside the menu.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) close();
    };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, [open, close]);

  const clamp = useCallback(() => {
    const menu = menuRef.current;
    const wrap = wrapRef.current;
    if (!menu || !wrap) return;
    const vw = document.documentElement.clientWidth;
    const gutter = 8;
    const wrapRect = wrap.getBoundingClientRect();
    const width = menu.offsetWidth;
    // wrapRect.width - width is the stylesheet's right:0 anchor.
    let rel = wrapRect.right - width < gutter ? 0 : wrapRect.width - width;
    rel = Math.min(rel, vw - gutter - wrapRect.left - width);
    setLeft(rel);
  }, []);

  // Layout effect: the clamp must land BEFORE the first paint of the open
  // menu — useEffect would flash the unclamped position (and any synchronous
  // measurement, like an e2e boundingBox, would read it).
  useLayoutEffect(() => {
    if (open) clamp();
  }, [open, clamp]);

  // …and the first pass races late webfont swaps (CI's cold contexts load the
  // font after measuring: the panel grows a few px and escapes again — PR
  // #2237). Re-clamp on any resize of the open panel and once fonts settle.
  useEffect(() => {
    if (!open) return;
    const menu = menuRef.current;
    if (!menu) return;
    const observer = new ResizeObserver(() => clamp());
    observer.observe(menu);
    void document.fonts?.ready.then(() => clamp());
    return () => observer.disconnect();
  }, [open, clamp]);

  // Roving tabindex: move DOM focus to the active item whenever it changes.
  useEffect(() => {
    if (!open || active < 0) return;
    const items = menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]');
    items?.[active]?.focus();
  }, [open, active]);

  const itemCount = sections.reduce((n, s) => n + s.items.length, 0);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      // APG menu button: Down/Enter/Space open to the first item, Up to the last.
      if (e.key === 'ArrowDown') { e.preventDefault(); openMenu(false); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); openMenu(true); }
      return;
    }
    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        close(true);
        break;
      case 'ArrowDown':
        e.preventDefault();
        setActive((i) => (i + 1) % itemCount);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive((i) => (i - 1 + itemCount) % itemCount);
        break;
      case 'Home':
        e.preventDefault();
        setActive(0);
        break;
      case 'End':
        e.preventDefault();
        setActive(itemCount - 1);
        break;
      case 'Tab':
        // Menus are transient: Tab closes and lets focus move on naturally.
        close();
        break;
      case ' ':
        // Space activates a menuitem; on a link that is not native behaviour.
        if ((e.target as HTMLElement).getAttribute('role') === 'menuitem') {
          e.preventDefault();
          (e.target as HTMLElement).click();
        }
        break;
    }
  };

  let index = -1;
  const renderItem = (item: MenuItemDef) => {
    index += 1;
    const idx = index;
    const common = {
      role: 'menuitem' as const,
      tabIndex: idx === active ? 0 : -1,
      className: `${styles.item} ${item.danger ? styles.itemDanger : ''}`,
      'aria-disabled': item.disabled || undefined,
      'aria-label': item.ariaLabel,
      'data-testid': item.testId,
    };
    const inner = (
      <>
        {item.icon && <span className={styles.itemIcon} aria-hidden="true">{item.icon}</span>}
        <span className={styles.itemLabel}>{item.label}</span>
        {item.trailing}
      </>
    );
    if (item.to && !item.disabled) {
      return (
        <Link key={item.id} href={item.to} {...common} onClick={() => close()}>
          {inner}
        </Link>
      );
    }
    return (
      <button key={item.id} type="button" {...common}
        onClick={() => { if (item.disabled) return; close(); item.onSelect?.(); }}>
        {inner}
      </button>
    );
  };

  return (
    <div className={styles.wrap} ref={wrapRef} onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName ?? styles.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={title}
        data-testid={triggerTestId}
        onClick={() => (open ? close() : openMenu(false))}
      >
        {icon}
      </button>
      {open && itemCount > 0 && (
        <div ref={menuRef} role="menu" aria-label={menuLabel ?? label}
          className={styles.menu}
          style={left !== null ? { left, right: 'auto' } : undefined}
          data-testid={menuTestId}>
          {sections.map((section) => {
            if (section.items.length === 0) return null;
            const labelId = section.label ? `${menuId}-${section.id}` : undefined;
            return (
              <div key={section.id} role={section.label ? 'group' : undefined}
                aria-labelledby={labelId}
                className={section.danger ? styles.sectionDanger : styles.section}>
                {section.label && (
                  <div className={styles.sectionLabel} id={labelId}>{section.label}</div>
                )}
                {section.items.map(renderItem)}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
