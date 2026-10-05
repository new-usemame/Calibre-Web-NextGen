import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type PointerEvent as ReactPointerEvent, type DragEvent as ReactDragEvent } from 'react';
import { useLocation, useSearch } from 'wouter';
import { X, GripVertical } from 'lucide-react';
import { useBulkActions, useMe, useShelves } from '../lib/queries';
import { canEditShelf } from '../lib/permissions';
import { draggedBookIds } from '../lib/shelfAdd';
import { joinBulkSentences } from '../lib/bulkResults';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import type { Book } from '../lib/api';
import styles from './ShelfDrag.module.css';

type Selection = { ids: number[]; busy?: boolean; onFailed: (ids: number[]) => void };
type Payload = { ids: number[]; owner?: React.MutableRefObject<Selection> };
type Drag = Payload & { x: number; y: number; shelfId?: number };
const MIME = 'application/x-cwng-shelf-books';
const Context = createContext<{
  drag: Drag | null; busy: boolean; available: boolean;
  register: (selection: React.MutableRefObject<Selection>) => () => void;
  pointerStart: (book: Book, event: ReactPointerEvent<HTMLButtonElement>) => void;
  nativeStart: (book: Book, event: ReactDragEvent<HTMLElement>) => void;
  nativeOver: (shelfId: number, event: ReactDragEvent<HTMLElement>) => void;
  nativeDrop: (shelfId: number, event: ReactDragEvent<HTMLElement>) => void;
  cancel: () => void; pick: (book: Book, keyboard?: boolean) => void;
} | null>(null);

export function useShelfDrag() { return useContext(Context); }

/** Register only the mounted source page's selection; dragging snapshots it. */
export function useShelfDragSelection(selection: Selection, scope = '') {
  const context = useShelfDrag();
  const latest = useRef(selection);
  latest.current = selection;
  const register = context?.register;
  useLayoutEffect(() => register?.(latest), [register, scope]);
}

export function ShelfDragProvider({ children }: { children: ReactNode }) {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const shelves = (useShelves().data?.items ?? []).filter(s => canEditShelf(me, s));
  const { addToShelf } = useBulkActions();
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  const [picker, setPicker] = useState<Payload | null>(null);
  const [message, setMessage] = useState('');
  const selection = useRef<React.MutableRefObject<Selection> | null>(null);
  const ignoreClick = useRef(false);
  const stopPointer = useRef<(() => void) | null>(null);
  const pending = useRef(false);
  const shelvesRef = useRef(shelves);
  shelvesRef.current = shelves;
  const dialogRef = useRef<HTMLDivElement>(null);
  const [location] = useLocation();
  const search = useSearch();
  const viewVersion = useRef(0);
  const publish = (value: Drag | null) => { dragRef.current = value; setDrag(value); };
  const cancel = useCallback(() => {
    stopPointer.current?.(); stopPointer.current = null;
    dragRef.current = null; setDrag(null);
  }, []);
  useLayoutEffect(() => { viewVersion.current++; cancel(); setPicker(null); setMessage(''); }, [location, search, me?.id, cancel]);
  useEffect(() => () => { viewVersion.current++; cancel(); }, [cancel]);
  const register = useCallback((value: React.MutableRefObject<Selection>) => {
    selection.current = value;
    return () => {
      if (selection.current !== value) return;
      selection.current = null;
      // A mounted page can change its data source without changing the URL.
      // Its old drag/picker and pending outcomes no longer own that selection.
      viewVersion.current++;
      cancel(); setPicker(null); setMessage('');
    };
  }, [cancel]);
  const payload = (id: number): Payload => {
    const source = selection.current?.current;
    return { ids: draggedBookIds(id, source?.ids ?? []), owner: selection.current ?? undefined };
  };
  const canStart = () => shelvesRef.current.length > 0 && !pending.current && !selection.current?.current.busy;
  const complete = async (value: Payload, shelfId: number) => {
    cancel();
    const shelf = shelvesRef.current.find(s => s.id === shelfId);
    if (!shelf || pending.current || selection.current?.current.busy) return;
    pending.current = true;
    const startedInView = viewVersion.current;
    setMessage('');
    try {
      const result = await addToShelf.mutateAsync({ ids: value.ids, shelfId });
      let text = result.failedIds.length
        ? t('{succeeded} added to {shelf}; {failed} failed. Failed books remain selected for retry.', {
          succeeded: result.succeededIds.length, shelf: shelf.name, failed: result.failedIds.length,
        })
        : t('{n} book(s) added to {shelf}.', { n: result.succeededIds.length, shelf: shelf.name });
      if (result.failedIds.length) text = joinBulkSentences(text, ...result.failureDetails.map(f => t('Book {id}: {message}', { id: f.id, message: f.message })));
      announce(text, { assertive: result.failedIds.length > 0 });
      // The write may finish after navigation; its old picker and selection
      // must not appear on the newly opened page.
      if (viewVersion.current !== startedInView) return;
      setMessage(text);
      // An outgoing page no longer owns selection state.
      if (result.failedIds.length && value.owner && selection.current === value.owner) value.owner.current.onFailed(result.failedIds);
      if (!result.failedIds.length) setPicker(null);
      else setPicker({ ...value, ids: result.failedIds });
    } catch {
      if (viewVersion.current !== startedInView) return;
      const text = t('Could not add books to the shelf. Please try again.');
      setMessage(text); announce(text, { assertive: true });
      setPicker(value);
    } finally { pending.current = false; }
  };
  const targetAt = (x: number, y: number) => {
    const node = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-shelf-drop]');
    const id = Number(node?.dataset.shelfDrop);
    return shelvesRef.current.some(s => s.id === id) ? id : undefined;
  };
  const pointerStart = (book: Book, event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!canStart() || event.button !== 0) return;
    cancel(); ignoreClick.current = false;
    const id = event.pointerId; const start = { x: event.clientX, y: event.clientY };
    const value = payload(book.id);
    event.currentTarget.setPointerCapture(id);
    const move = (e: PointerEvent) => {
      if (e.pointerId !== id) return;
      if (!dragRef.current && Math.hypot(e.clientX - start.x, e.clientY - start.y) < 8) return;
      e.preventDefault(); ignoreClick.current = true;
      publish({ ...value, x: e.clientX, y: e.clientY, shelfId: targetAt(e.clientX, e.clientY) });
    };
    const up = (e: PointerEvent) => {
      if (e.pointerId !== id) return;
      const current = dragRef.current;
      const shelfId = targetAt(e.clientX, e.clientY);
      cancel();
      if (current && shelfId) void complete(current, shelfId);
    };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') { ignoreClick.current = true; cancel(); } };
    const abort = () => { ignoreClick.current = true; cancel(); };
    window.addEventListener('pointermove', move, { passive: false });
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', abort);
    window.addEventListener('keydown', escape);
    window.addEventListener('blur', abort);
    stopPointer.current = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', abort); window.removeEventListener('keydown', escape);
      window.removeEventListener('blur', abort);
    };
  };
  // Continue scrolling the target list while a pointer is held at its edge.
  useEffect(() => {
    if (!drag) return;
    let frame: number;
    const scroll = () => {
      const value = dragRef.current;
      const nav = document.querySelector<HTMLElement>('[data-shelf-drag-nav]');
      if (value && nav) {
        const box = nav.getBoundingClientRect();
        if (value.x >= box.left && value.x <= box.right) {
          if (value.y < box.top + 48) nav.scrollTop -= 8;
          else if (value.y > box.bottom - 48) nav.scrollTop += 8;
          const shelfId = targetAt(value.x, value.y);
          if (shelfId !== value.shelfId) publish({ ...value, shelfId });
        }
      }
      frame = requestAnimationFrame(scroll);
    };
    frame = requestAnimationFrame(scroll);
    return () => cancelAnimationFrame(frame);
  }, [!!drag]);
  const closePicker = useCallback(() => { if (!pending.current) setPicker(null); }, []);
  useFocusTrap(dialogRef, { active: !!picker, onClose: closePicker });
  const context = {
    drag, busy: addToShelf.isPending, available: shelves.length > 0, register, pointerStart, cancel,
    pick: (book: Book, keyboard = false) => {
      if (ignoreClick.current && !keyboard) { ignoreClick.current = false; return; }
      ignoreClick.current = false;
      if (canStart()) { setMessage(''); setPicker(payload(book.id)); }
    },
    nativeStart: (book: Book, event: ReactDragEvent<HTMLElement>) => {
      if (!canStart()) { event.preventDefault(); return; }
      event.stopPropagation(); event.dataTransfer.effectAllowed = 'copy';
      event.dataTransfer.setData(MIME, 'internal');
      publish({ ...payload(book.id), x: event.clientX, y: event.clientY });
    },
    nativeOver: (shelfId: number, event: ReactDragEvent<HTMLElement>) => {
      if (!dragRef.current || !event.dataTransfer.types.includes(MIME) || !shelvesRef.current.some(s => s.id === shelfId)) return;
      event.preventDefault(); event.dataTransfer.dropEffect = 'copy';
      publish({ ...dragRef.current, x: event.clientX, y: event.clientY, shelfId });
    },
    nativeDrop: (shelfId: number, event: ReactDragEvent<HTMLElement>) => {
      const value = dragRef.current;
      if (!value || !event.dataTransfer.types.includes(MIME) || !shelvesRef.current.some(s => s.id === shelfId)) return;
      event.preventDefault(); event.stopPropagation(); void complete(value, shelfId);
    },
  };
  return <Context.Provider value={context}>
    {children}
    {drag && <div className={styles.ghost} style={{ left: Math.min(drag.x + 12, window.innerWidth - 150), top: Math.max(0, drag.y - 44) }}>
      {t('{n} selected', { n: drag.ids.length })}
    </div>}
    {picker && <div className={styles.scrim} onClick={closePicker}>
      <div ref={dialogRef} className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="shelf-drag-title" tabIndex={-1} onClick={e => e.stopPropagation()}>
        <div className={styles.heading}><h2 id="shelf-drag-title">{t('Add to shelf')}</h2>
          <button type="button" disabled={addToShelf.isPending} onClick={closePicker} aria-label={t('Close')}><X size={20} aria-hidden="true" focusable={false} /></button>
        </div>
        <p>{t('{n} selected', { n: picker.ids.length })}</p>
        {message && <p role="alert">{message}</p>}
        <ul>{shelves.map(s => <li key={s.id}><button type="button" disabled={addToShelf.isPending} onClick={() => void complete(picker, s.id)}>{s.name}</button></li>)}</ul>
      </div>
    </div>}
  </Context.Provider>;
}

export function ShelfDragHandle({ book, disabled = false }: { book: Book; disabled?: boolean }) {
  const drag = useShelfDrag();
  const t = useT();
  if (!drag?.available) return null;
  return <button type="button" className={styles.handle} disabled={disabled || drag.busy}
    aria-label={t('Add {title} to a shelf', { title: book.title })}
    title={t('Drag to a shelf, or choose a shelf')}
    onPointerDown={event => drag.pointerStart(book, event)} onClick={event => drag.pick(book, event.detail === 0)}>
    <GripVertical size={18} aria-hidden="true" focusable={false} />
  </button>;
}
