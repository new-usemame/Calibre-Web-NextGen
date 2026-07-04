import { useEffect, useRef, useState } from 'react';
import { X, GripVertical, ChevronUp, ChevronDown, RotateCcw, Eye, EyeOff } from 'lucide-react';
import { useMe, useUpdateSidebar } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { ORDERABLE_ENTRIES, DEFAULT_SIDEBAR_ORDER, resolveSidebarOrder } from '../lib/sidebarEntries';
import { Button } from './Button';
import styles from './SidebarCustomize.module.css';

const ENTRY_BY_KEY = new Map(ORDERABLE_ENTRIES.map((e) => [e.key, e]));

interface Props {
  open: boolean;
  onClose: () => void;
}

/** #585 v2 — the in-SPA "Customize sidebar" editor: toggle section visibility
 *  and reorder entries (incl. the Shelves block), then save. Reorder is operable
 *  by mouse/touch (up/down buttons + pointer drag) AND keyboard (arrow keys on
 *  the focused handle, with a screen-reader position announcement). */
export function SidebarCustomize({ open, onClose }: Props) {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const update = useUpdateSidebar();
  const modalRef = useRef<HTMLDivElement>(null);
  const handleRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const refocusKey = useRef<string | null>(null);

  const [order, setOrder] = useState<string[]>([]);
  const [vis, setVis] = useState<Record<string, boolean>>({});
  const [dragKey, setDragKey] = useState<string | null>(null);
  const seededRef = useRef(false);

  // Seed local edit state from the server ONCE per open — after `me` is present.
  // Keeping `me` out of the seed would race a not-yet-loaded profile; re-seeding
  // on every `me` change would let a background refetch (TanStack's window-focus
  // default) silently discard the user's unsaved reorder/toggle edits. So: wait
  // for `me`, seed once, and reset the latch on close.
  useEffect(() => {
    if (!open) { seededRef.current = false; return; }
    if (seededRef.current || !me) return;
    seededRef.current = true;
    setOrder(resolveSidebarOrder(me.sidebar_order).map((e) => e.key));
    const v: Record<string, boolean> = {};
    for (const e of ORDERABLE_ENTRIES) {
      if (e.isShelvesBlock) continue; // Shelves is always visible, only movable
      v[e.key] = me.sidebar?.[e.key] !== false;
    }
    setVis(v);
  }, [open, me]);

  useFocusTrap(modalRef, { onClose, active: open });

  // Keep keyboard focus on the item that just moved (its DOM node is keyed by
  // `key`, so it persists across the reorder — refocus after React commits).
  useEffect(() => {
    if (refocusKey.current) {
      handleRefs.current[refocusKey.current]?.focus();
      refocusKey.current = null;
    }
  }, [order]);

  if (!open) return null;

  const total = order.length;

  const move = (from: number, to: number, key: string) => {
    if (to < 0 || to >= total || from === to) return;
    setOrder((prev) => {
      const next = [...prev];
      const [it] = next.splice(from, 1);
      next.splice(to, 0, it);
      return next;
    });
    const label = ENTRY_BY_KEY.get(key)?.label ?? key;
    announce(t('{label} moved to position {pos} of {total}', {
      label: t(label), pos: to + 1, total,
    }));
  };

  const onHandleKeyDown = (e: React.KeyboardEvent, i: number, key: string) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      refocusKey.current = key;
      move(i, i - 1, key);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      refocusKey.current = key;
      move(i, i + 1, key);
    }
  };

  // Pointer drag (progressive enhancement over the always-present up/down buttons).
  const onListPointerMove = (e: React.PointerEvent) => {
    if (!dragKey) return;
    const overRow = (document.elementFromPoint(e.clientX, e.clientY) as Element | null)
      ?.closest('[data-key]');
    const overKey = overRow?.getAttribute('data-key');
    if (!overKey || overKey === dragKey) return;
    setOrder((prev) => {
      const from = prev.indexOf(dragKey);
      const to = prev.indexOf(overKey);
      if (from < 0 || to < 0 || from === to) return prev;
      const next = [...prev];
      const [it] = next.splice(from, 1);
      next.splice(to, 0, it);
      return next;
    });
  };

  const toggleVis = (key: string) =>
    setVis((v) => ({ ...v, [key]: !v[key] }));

  const reset = () => {
    setOrder([...DEFAULT_SIDEBAR_ORDER]);
    const v: Record<string, boolean> = {};
    for (const e of ORDERABLE_ENTRIES) if (!e.isShelvesBlock) v[e.key] = true;
    setVis(v);
    announce(t('Sidebar reset to default.'));
  };

  const save = () => {
    update.mutate({ visibility: vis, order }, {
      onSuccess: () => { announce(t('Sidebar saved.')); onClose(); },
      onError: () => announce(t('Could not save sidebar. Please try again.'), { assertive: true }),
    });
  };

  return (
    <div className={styles.overlay} onClick={onClose} role="presentation">
      <div
        className={styles.modal}
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sidebar-customize-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.header}>
          <h2 id="sidebar-customize-title" className={styles.title}>{t('Customize sidebar')}</h2>
          <button type="button" className={styles.close} onClick={onClose} aria-label={t('Close')}>
            <X size={18} aria-hidden="true" focusable={false} />
          </button>
        </div>

        <p className={styles.hint}>
          {t('Show or hide sections and drag to reorder them. Use the arrow keys on a handle to move an entry.')}
        </p>

        <ul className={styles.list} role="list" onPointerMove={onListPointerMove}>
          {order.map((key, i) => {
            const entry = ENTRY_BY_KEY.get(key);
            if (!entry) return null;
            const Icon = entry.icon;
            const isShelves = !!entry.isShelvesBlock;
            const visible = isShelves ? true : vis[key] !== false;
            return (
              <li
                key={key}
                data-key={key}
                className={dragKey === key ? styles.rowDragging : styles.row}
              >
                <button
                  type="button"
                  ref={(el) => { handleRefs.current[key] = el; }}
                  className={styles.handle}
                  aria-label={t('Reorder {label} (position {pos} of {total}). Use arrow keys to move.', {
                    label: t(entry.label), pos: i + 1, total,
                  })}
                  onKeyDown={(e) => onHandleKeyDown(e, i, key)}
                  onPointerDown={(e) => {
                    setDragKey(key);
                    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
                  }}
                  onPointerUp={() => setDragKey(null)}
                  onPointerCancel={() => setDragKey(null)}
                >
                  <GripVertical size={16} aria-hidden="true" focusable={false} />
                </button>

                <Icon size={16} className={styles.rowIcon} aria-hidden="true" focusable={false} />
                <span className={visible ? styles.rowLabel : styles.rowLabelHidden}>{t(entry.label)}</span>

                <div className={styles.rowActions}>
                  <button
                    type="button"
                    className={styles.moveBtn}
                    onClick={() => move(i, i - 1, key)}
                    disabled={i === 0}
                    aria-label={t('Move {label} up', { label: t(entry.label) })}
                  >
                    <ChevronUp size={16} aria-hidden="true" focusable={false} />
                  </button>
                  <button
                    type="button"
                    className={styles.moveBtn}
                    onClick={() => move(i, i + 1, key)}
                    disabled={i === total - 1}
                    aria-label={t('Move {label} down', { label: t(entry.label) })}
                  >
                    <ChevronDown size={16} aria-hidden="true" focusable={false} />
                  </button>
                  {isShelves ? (
                    <span className={styles.alwaysOn}>{t('Always shown')}</span>
                  ) : (
                    <button
                      type="button"
                      className={styles.visBtn}
                      onClick={() => toggleVis(key)}
                      aria-pressed={visible}
                      aria-label={visible
                        ? t('Hide {label}', { label: t(entry.label) })
                        : t('Show {label}', { label: t(entry.label) })}
                    >
                      {visible
                        ? <Eye size={16} aria-hidden="true" focusable={false} />
                        : <EyeOff size={16} aria-hidden="true" focusable={false} />}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>

        <div className={styles.footer}>
          <Button variant="ghost" onClick={reset}>
            <RotateCcw size={15} aria-hidden="true" focusable={false} /> {t('Reset to default')}
          </Button>
          <div className={styles.footerRight}>
            <Button variant="ghost" onClick={onClose}>{t('Cancel')}</Button>
            <Button onClick={save} disabled={update.isPending}>
              {update.isPending ? t('Saving…') : t('Save')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
