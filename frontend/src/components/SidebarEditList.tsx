import { useRef, useEffect, useLayoutEffect, useState, useCallback } from 'react';
import { GripVertical, X, RotateCcw } from 'lucide-react';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { ORDERABLE_ENTRIES } from '../lib/sidebarEntries';
import { arrayMove, slotForCenter, shiftMap } from '../lib/dragSort';
import styles from './Sidebar.module.css';

const ENTRY_BY_KEY = new Map(ORDERABLE_ENTRIES.map((e) => [e.key, e]));

// Motion tuning. The settle uses a decel curve (fast out, gentle in) so a
// dropped row glides home instead of snapping. Honoured only when the user
// hasn't asked for reduced motion.
const SETTLE_MS = 220;
const SETTLE_EASE = 'cubic-bezier(0.22, 1, 0.36, 1)';
const DRAG_THRESHOLD = 4; // px of travel before a press becomes a drag (vs a tap/click)

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;

interface Props {
  order: string[];
  setOrder: React.Dispatch<React.SetStateAction<string[]>>;
  vis: Record<string, boolean>;
  setVis: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;
}

interface DragState {
  key: string;
  fromIndex: number;
  pointerId: number;
  startY: number;
  activated: boolean;
  centers: number[]; // resting vertical centers (client coords), measured at activation
  slotSize: number; // mean row pitch (height + gap) — distance a displaced row travels
  target: number; // current computed destination index
  raf: number; // pending rAF id for coalesced move handling
  lastY: number; // most recent pointer clientY (for the coalesced frame)
}

/** #585 v3 / drag-polish — the inline sidebar edit list shown when the Customize
 *  capsule is active. Each row: drag handle + label + a delete/restore control.
 *
 *  Reorder is a proper free-drag: press anywhere on a row and it lifts and tracks
 *  the pointer 1:1, while the rows it passes slide out of the way to open a gap;
 *  on release it glides into its slot (FLIP). Keyboard reorder and delete/restore
 *  reflow animate through the same FLIP path. Deleting hides the entry (kept in
 *  the list, greyed, with a restore affordance). Shelves is always shown (only
 *  movable). Works with mouse, touch, pen, and keyboard; respects reduced motion. */
export function SidebarEditList({ order, setOrder, vis, setVis }: Props) {
  const t = useT();
  const announce = useAnnouncer();

  const listRef = useRef<HTMLUListElement>(null);
  const rowRefs = useRef<Map<string, HTMLLIElement>>(new Map());
  const handleRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const refocusKey = useRef<string | null>(null);

  const drag = useRef<DragState | null>(null);
  // FLIP bookkeeping: resting client-tops from the previous committed layout, and
  // an optional override captured at drop (the lifted/gapped visual positions) so
  // the settle animates from where the finger left off, not from the old slot.
  const prevTops = useRef<Map<string, number>>(new Map());
  const flipFrom = useRef<Map<string, number> | null>(null);

  const [draggingKey, setDraggingKey] = useState<string | null>(null);

  const total = order.length;

  const measureTops = useCallback((): Map<string, number> => {
    const m = new Map<string, number>();
    for (const key of order) {
      const node = rowRefs.current.get(key);
      if (node) m.set(key, node.getBoundingClientRect().top);
    }
    return m;
  }, [order]);

  // ── FLIP: animate every committed layout change (drag drop, keyboard move,
  //    delete/restore) from its previous position to its new one. ────────────
  useLayoutEffect(() => {
    const now = measureTops();
    const from = flipFrom.current ?? prevTops.current;
    const reduce = prefersReducedMotion();

    if (from.size && !reduce) {
      const moved: HTMLLIElement[] = [];
      for (const [key, top] of now) {
        const start = from.get(key);
        const node = rowRefs.current.get(key);
        if (start === undefined || !node) continue;
        const dy = start - top;
        if (Math.abs(dy) < 0.5) continue;
        node.style.transition = 'none';
        node.style.transform = `translateY(${dy}px)`;
        moved.push(node);
      }
      if (moved.length) {
        // Force a reflow so the inverted transforms are the painted "First"
        // frame, then release them on the next frame to play the transition.
        void listRef.current?.offsetHeight;
        requestAnimationFrame(() => {
          for (const node of moved) {
            node.style.transition = `transform ${SETTLE_MS}ms ${SETTLE_EASE}`;
            node.style.transform = '';
          }
        });
      }
    }

    prevTops.current = now;
    flipFrom.current = null;
  }, [order, vis, measureTops]);

  // Keep keyboard focus on the item that just moved (its node is keyed by `key`).
  useEffect(() => {
    if (refocusKey.current) {
      handleRefs.current[refocusKey.current]?.focus();
      refocusKey.current = null;
    }
  }, [order]);

  // Commit a reorder + announce. Shared by keyboard arrows and drag drop.
  const move = useCallback((from: number, to: number, key: string) => {
    if (to < 0 || to >= total || from === to) return;
    setOrder((prev) => arrayMove(prev, from, to));
    const label = ENTRY_BY_KEY.get(key)?.label ?? key;
    announce(t('{label} moved to position {pos} of {total}', {
      label: t(label), pos: to + 1, total,
    }));
  }, [total, setOrder, announce, t]);

  const onKeyDown = (e: React.KeyboardEvent, i: number, key: string) => {
    if (e.key === 'ArrowUp') { e.preventDefault(); refocusKey.current = key; move(i, i - 1, key); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); refocusKey.current = key; move(i, i + 1, key); }
  };

  // ── pointer drag ──────────────────────────────────────────────────────────
  const clearRowStyles = () => {
    for (const node of rowRefs.current.values()) {
      node.style.transition = '';
      node.style.transform = '';
      node.style.zIndex = '';
    }
  };

  // Paint the current drag frame: dragged row glued to the pointer, the rows it
  // has crossed shifted by one slot to open the gap, everyone else at rest.
  const paintDrag = (d: DragState) => {
    const dy = d.lastY - d.startY;
    d.target = slotForCenter(d.centers, d.centers[d.fromIndex] + dy);
    const shifts = shiftMap(d.fromIndex, d.target);
    order.forEach((key, i) => {
      const node = rowRefs.current.get(key);
      if (!node) return;
      if (i === d.fromIndex) {
        node.style.transform = `translateY(${dy}px)`;
      } else {
        const sign = shifts.get(i);
        node.style.transform = sign ? `translateY(${sign * d.slotSize}px)` : '';
      }
    });
  };

  const activate = (d: DragState, key: string) => {
    // Measure the resting layout once, at the moment the drag begins.
    const centers: number[] = [];
    let firstTop = 0;
    let lastBottom = 0;
    order.forEach((k, i) => {
      const node = rowRefs.current.get(k);
      const r = node!.getBoundingClientRect();
      centers[i] = r.top + r.height / 2;
      if (i === 0) firstTop = r.top;
      lastBottom = r.bottom;
    });
    d.centers = centers;
    d.slotSize = total > 1 ? (lastBottom - firstTop) / total : 44;
    d.activated = true;
    const node = rowRefs.current.get(key);
    if (node) node.style.zIndex = '5';
    setDraggingKey(key);
    const label = ENTRY_BY_KEY.get(key)?.label ?? key;
    announce(t('Picked up {label}. Drag to reorder, release to drop.', { label: t(label) }));
  };

  const onPointerDown = (e: React.PointerEvent<HTMLLIElement>, i: number, key: string) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return; // left / primary only
    if ((e.target as HTMLElement).closest('[data-nodrag]')) return; // let the ✕/restore click through
    drag.current = {
      key, fromIndex: i, pointerId: e.pointerId, startY: e.clientY,
      activated: false, centers: [], slotSize: 44, target: i, raf: 0, lastY: e.clientY,
    };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* capture unsupported */ }
  };

  const onPointerMove = (e: React.PointerEvent<HTMLLIElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== e.pointerId) return;
    d.lastY = e.clientY;
    if (!d.activated) {
      if (Math.abs(e.clientY - d.startY) < DRAG_THRESHOLD) return;
      activate(d, d.key);
    }
    e.preventDefault(); // suppress scroll / text-selection once we're dragging
    if (d.raf) return; // coalesce to one paint per frame
    d.raf = requestAnimationFrame(() => {
      d.raf = 0;
      if (drag.current === d && d.activated) paintDrag(d);
    });
  };

  const endDrag = (e: React.PointerEvent<HTMLLIElement>) => {
    const d = drag.current;
    if (!d || d.pointerId !== e.pointerId) return;
    drag.current = null;
    if (d.raf) cancelAnimationFrame(d.raf);
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }

    if (!d.activated) { setDraggingKey(null); return; } // it was a tap, not a drag

    const { fromIndex, target, key } = d;
    if (target !== fromIndex) {
      // Reorder: hand the settle to FLIP by capturing the lifted/gapped frame as
      // its "First", then commit the new order and let the layout effect glide
      // every row into place.
      flipFrom.current = measureTops();
      clearRowStyles();
      setDraggingKey(null);
      move(fromIndex, target, key);
    } else {
      // No reorder — ease the lifted row back into its own slot.
      setDraggingKey(null);
      const node = rowRefs.current.get(key);
      if (node) {
        if (prefersReducedMotion()) {
          node.style.transition = '';
          node.style.transform = '';
          node.style.zIndex = '';
        } else {
          node.style.zIndex = '';
          node.style.transition = `transform ${SETTLE_MS}ms ${SETTLE_EASE}`;
          node.style.transform = '';
          const settle = () => {
            node.style.transition = '';
            node.removeEventListener('transitionend', settle);
          };
          node.addEventListener('transitionend', settle);
        }
      }
    }
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
    <ul className={styles.editList} role="list" ref={listRef}>
      {order.map((key, i) => {
        const entry = ENTRY_BY_KEY.get(key);
        if (!entry) return null;
        const Icon = entry.icon;
        const isShelves = !!entry.isShelvesBlock;
        const hidden = !isShelves && vis[key] === false;
        const dragging = draggingKey === key;
        return (
          <li
            key={key}
            data-key={key}
            ref={(el) => {
              if (el) rowRefs.current.set(key, el);
              else rowRefs.current.delete(key);
            }}
            className={`${hidden ? styles.editRowHidden : styles.editRow}${dragging ? ` ${styles.editRowDragging}` : ''}`}
            onPointerDown={(e) => onPointerDown(e, i, key)}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
          >
            <button
              type="button"
              ref={(el) => { handleRefs.current[key] = el; }}
              className={styles.editHandle}
              aria-label={t('Reorder {label} (position {pos} of {total}). Use arrow keys to move.', {
                label: t(entry.label), pos: i + 1, total,
              })}
              onKeyDown={(e) => onKeyDown(e, i, key)}
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
                data-nodrag
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
