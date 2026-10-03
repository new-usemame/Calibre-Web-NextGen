import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'wouter';
import { BookOpen, Check, MoreHorizontal, Pencil, Star, X } from 'lucide-react';
import type { Book } from '../lib/api';
import { useMe, useToggleFavorite, useToggleRead } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './BookCardActions.module.css';

interface Props {
  book: Book;
  readTarget: string | null;
  quickEdit: boolean;
  onRemove?: (book: Book) => void;
  removeLabel: string;
  shelfNames: string[];
}

/** Mount mutations only for the open card, rather than every tile in a library. */
function ActionDialog({ book, readTarget, quickEdit, onRemove, removeLabel, shelfNames, anchor, onClose, personal, opener }: Props & {
  anchor: DOMRect; onClose: () => void; personal: boolean; opener: HTMLButtonElement | null;
}) {
  const t = useT();
  const announce = useAnnouncer();
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const [error, setError] = useState(false);
  const favorite = useToggleFavorite(book.id);
  const read = useToggleRead(book.id);
  const busy = favorite.isPending || read.isPending;
  useFocusTrap(panel, { onClose });
  useEffect(() => () => {
    // A successful filtered-list mutation can remove its own card. Restore to
    // the stable catalog heading when the original trigger no longer exists;
    // ordinary dismissal still uses the focus trap's trigger restoration.
    queueMicrotask(() => {
      // Native touch may restore the route's main landmark because tapping
      // the opener never focused it. Treat that neutral route focus like body.
      const active = document.activeElement;
      if (!opener?.isConnected && (active === document.body || active === document.querySelector('main'))) {
        document.querySelector<HTMLElement>('[data-testid="catalog-heading"], [data-testid="shelf-heading"]')?.focus();
      }
    });
  }, [opener]);
  useLayoutEffect(() => {
    const element = panel.current!;
    const place = () => {
      const box = element.getBoundingClientRect();
      const preferredTop = anchor.bottom + box.height + 8 <= window.innerHeight
        ? anchor.bottom + 8 : anchor.top - box.height - 8;
      const next = {
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - box.width - 8)),
        // Keyboard focus can scroll the opener after its anchor is captured.
        // Errors, translated text and loaded fonts can then grow the panel.
        top: Math.max(8, Math.min(preferredTop, window.innerHeight - box.height - 8)),
      };
      setPosition(previous => previous.left === next.left && previous.top === next.top ? previous : next);
    };
    place();
    const observer = new ResizeObserver(place);
    observer.observe(element);
    window.addEventListener('resize', place);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); };
  }, [anchor]);
  const failed = () => {
    setError(true);
    announce(t('Could not update this book. Try again.'), { assertive: true });
  };
  const update = async (mutation: Promise<unknown>) => {
    setError(false);
    try {
      await mutation;
      // Revision refresh may unmount this card before the mutation completes.
      // Keep the outcome announcement outside observer-scoped onSuccess hooks.
      announce(t('Saved.'));
      onClose();
    } catch { failed(); }
  };
  return createPortal(
    <div className={styles.backdrop} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={panel} className={styles.panel} style={position} role="dialog" aria-modal="true"
        aria-label={t('Actions for {title}', { title: book.title })} tabIndex={-1}>
        <div className={styles.heading}>
          <span dir="auto">{book.title}</span>
          <button type="button" className={styles.close} onClick={onClose} aria-label={t('Close')}>
            <X size={18} aria-hidden="true" focusable={false} />
          </button>
        </div>
        {readTarget && <Link href={readTarget} className={styles.action} onClick={onClose}>
          <BookOpen size={18} aria-hidden="true" focusable={false} /> {t('Read now')}
        </Link>}
        {quickEdit && <Link href={`/book/${book.id}/edit`} className={styles.action} onClick={onClose}>
          <Pencil size={18} aria-hidden="true" focusable={false} /> {t('Edit')}
        </Link>}
        {personal && <>
          {book.favorited != null && <button type="button" className={styles.action} disabled={busy} aria-pressed={book.favorited}
            onClick={() => void update(favorite.mutateAsync(undefined))}>
            <Star size={18} fill={book.favorited ? 'currentColor' : 'none'} aria-hidden="true" focusable={false} />
            {book.favorited ? t('Remove from favorites') : t('Add to favorites')}
          </button>}
          <button type="button" className={styles.action} disabled={busy}
            onClick={() => void update(read.mutateAsync(!book.read))}>
            <Check size={18} aria-hidden="true" focusable={false} />
            {book.read ? t('Mark as unread') : t('Mark as read')}
          </button>
          <p className={styles.status}>{book.read_status === 'did_not_finish' ? t('Did not finish') : book.read_status === 'on_hold' ? t('On hold') : book.in_progress ? t('Reading') : book.read ? t('Read') : t('Unread')}</p>
        </>}
        {shelfNames.length > 0 && <section className={styles.shelves} aria-label={t('Shelves')}>
          <h3>{t('Shelves')}</h3>
          <ul role="list">
            {shelfNames.map((name, index) => <li key={`${index}-${name}`} dir="auto">{name}</li>)}
          </ul>
        </section>}
        {onRemove && <button type="button" className={styles.action} disabled={busy}
          onClick={() => { onClose(); onRemove(book); }}>
          <X size={18} aria-hidden="true" focusable={false} /> {t(removeLabel)}
        </button>}
        {error && <p className={styles.error}>{t('Could not update this book. Try again.')}</p>}
      </div>
    </div>, document.body,
  );
}

export function BookCardActions(props: Props) {
  const t = useT();
  const me = useMe().data;
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setAnchor(null), []);
  const personal = !!me && !me.role?.anonymous && props.book.in_my_library !== false;
  if (!props.readTarget && !props.quickEdit && !personal && !props.onRemove) return null;
  return <>
    <button ref={trigger} type="button" className={styles.trigger} aria-haspopup="dialog" aria-expanded={!!anchor}
      aria-label={t('Actions for {title}', { title: props.book.title })}
      onClick={e => setAnchor(e.currentTarget.getBoundingClientRect())}>
      <MoreHorizontal size={20} aria-hidden="true" focusable={false} />
    </button>
    {anchor && <ActionDialog {...props} personal={personal} anchor={anchor} opener={trigger.current} onClose={close} />}
  </>;
}
