import { useRef, useEffect } from 'react';
import { GripVertical, X, RotateCcw } from 'lucide-react';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { ORDERABLE_ENTRIES } from '../lib/sidebarEntries';
import styles from './Sidebar.module.css';

const ENTRY_BY_KEY = new Map(ORDERABLE_ENTRIES.map((e) => [e.key, e]));

interface Props {
  order: string[];
  setOrder: React.Dispatch<React.SetStateAction<string[]>>;
  vis: Record<string, boolean>;
  setVis: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
}

/** #585 v3 — the inline sidebar edit list shown when the Customize capsule is
 *  active. Each row: drag handle (pointer + keyboard reorder) + label + a
 *  delete/restore control. Deleting hides the entry (visibility off); it stays
 *  in the list, greyed, with a restore affordance so nothing is lost. Shelves is
 *  always shown (only movable). Reorder works by mouse, touch, and keyboard. */
export function SidebarEditList({ order, setOrder, vis, setVis }: Props) {
  const t = useT();
  const announce = useAnnouncer();
  const handleRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const refocusKey = useRef<string | null>(null);
  const dragKey = useRef<string | null>(null);

  const total = order.length;

  // Keep keyboard focus on the item that just moved (its node is keyed by `key`).
  useEffect(() => {
    if (refocusKey.current) {
      handleRefs.current[refocusKey.current]?.focus();
      refocusKey.current = null;
    }
  }, [order]);

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

  const onKeyDown = (e: React.KeyboardEvent, i: number, key: string) => {
    if (e.key === 'ArrowUp') { e.preventDefault(); refocusKey.current = key; move(i, i - 1, key); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); refocusKey.current = key; move(i, i + 1, key); }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragKey.current) return;
    const over = (document.elementFromPoint(e.clientX, e.clientY) as Element | null)?.closest('[data-key]');
    const overKey = over?.getAttribute('data-key');
    if (!overKey || overKey === dragKey.current) return;
    setOrder((prev) => {
      const from = prev.indexOf(dragKey.current as string);
      const to = prev.indexOf(overKey);
      if (from < 0 || to < 0 || from === to) return prev;
      const next = [...prev];
      const [it] = next.splice(from, 1);
      next.splice(to, 0, it);
      return next;
    });
  };

  const toggleDelete = (key: string, label: string) => {
    setVis((v) => {
      const hidden = v[key] === false;
      announce(hidden ? t('{label} restored', { label: t(label) })
                      : t('{label} hidden', { label: t(label) }));
      return { ...v, [key]: hidden };
    });
  };

  return (
    <ul className={styles.editList} role="list" onPointerMove={onPointerMove}>
      {order.map((key, i) => {
        const entry = ENTRY_BY_KEY.get(key);
        if (!entry) return null;
        const Icon = entry.icon;
        const isShelves = !!entry.isShelvesBlock;
        const hidden = !isShelves && vis[key] === false;
        return (
          <li
            key={key}
            data-key={key}
            className={hidden ? styles.editRowHidden : styles.editRow}
          >
            <button
              type="button"
              ref={(el) => { handleRefs.current[key] = el; }}
              className={styles.editHandle}
              aria-label={t('Reorder {label} (position {pos} of {total}). Use arrow keys to move.', {
                label: t(entry.label), pos: i + 1, total,
              })}
              onKeyDown={(e) => onKeyDown(e, i, key)}
              onPointerDown={(e) => {
                dragKey.current = key;
                (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
              }}
              onPointerUp={() => { dragKey.current = null; }}
              onPointerCancel={() => { dragKey.current = null; }}
            >
              <GripVertical size={15} aria-hidden="true" focusable={false} />
            </button>

            <Icon size={16} className={styles.editIcon} aria-hidden="true" focusable={false} />
            <span className={styles.editLabel}>{t(entry.label)}</span>

            {isShelves ? (
              <span className={styles.editAlways}>{t('Always shown')}</span>
            ) : (
              <button
                type="button"
                className={hidden ? styles.editRestoreBtn : styles.editDeleteBtn}
                onClick={() => toggleDelete(key, entry.label)}
                aria-pressed={hidden}
                aria-label={hidden ? t('Restore {label}', { label: t(entry.label) })
                                   : t('Delete {label}', { label: t(entry.label) })}
              >
                {hidden ? <RotateCcw size={15} aria-hidden="true" focusable={false} />
                        : <X size={16} aria-hidden="true" focusable={false} />}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
