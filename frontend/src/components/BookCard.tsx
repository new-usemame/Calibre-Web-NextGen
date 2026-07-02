import { Check, X, Pencil } from 'lucide-react';
import { Link, useLocation } from 'wouter';
import type { Book } from '../lib/api';
import { BookCover } from './BookCover';
import styles from './BookCard.module.css';

interface BookCardProps {
  book: Book;
  style?: React.CSSProperties;
  /** When provided, a remove (×) control is shown on the cover (e.g. on a shelf). */
  onRemove?: (book: Book) => void;
  removeLabel?: string;
  /** Selection mode: render as a toggle (not a link), with a checkbox overlay. */
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (book: Book) => void;
  /** When true, show the book's position within its series (#573) — used by the
   *  series view so the reading order is visible without duplicating it in titles. */
  showSeriesIndex?: boolean;
  /** Show a hover pencil that jumps straight to the edit page (fork #572). Opt-in
   *  so it only appears where it's wanted (catalog + search) and only for users
   *  who can edit. Suppressed in selection mode. */
  quickEdit?: boolean;
}

/** Format a Calibre series_index (a float, e.g. 1.0, 2.5) for display: whole
 *  numbers show as "1", fractional as "2.5". Returns null when there's nothing
 *  to show so the badge is omitted entirely. */
function formatSeriesIndex(idx: number | null | undefined): string | null {
  if (idx == null || Number.isNaN(idx)) return null;
  return Number.isInteger(idx) ? String(idx) : String(idx);
}

export function BookCard({
  book, style, onRemove, removeLabel = 'Remove',
  selectable = false, selected = false, onToggleSelect,
  showSeriesIndex = false,
  quickEdit = false,
}: BookCardProps) {
  const authorStr = book.authors.join(', ');
  const seriesIndexLabel = showSeriesIndex ? formatSeriesIndex(book.series_index) : null;
  const [, navigate] = useLocation();

  const inner = (
    <article
      className={selected ? styles.cardSelected : styles.card}
      style={style}
      tabIndex={0}
      aria-pressed={selectable ? selected : undefined}
    >
      <div className={styles.coverWrap}>
        <BookCover coverUrl={book.cover_url} title={book.title} />
        {book.read && (
          <span className={styles.readBadge} aria-label="Read" title="Read">
            <Check size={14} strokeWidth={3} />
          </span>
        )}
        {seriesIndexLabel && (
          <span
            className={styles.seriesBadge}
            aria-label={`Series position ${seriesIndexLabel}`}
            title={`Series position ${seriesIndexLabel}`}
          >
            #{seriesIndexLabel}
          </span>
        )}
        {selectable && (
          <span className={selected ? styles.checkboxOn : styles.checkboxOff} aria-hidden="true">
            {selected && <Check size={14} strokeWidth={3} />}
          </span>
        )}
        {onRemove && !selectable && (
          <button
            type="button"
            className={styles.removeBtn}
            aria-label={removeLabel}
            title={removeLabel}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onRemove(book);
            }}
          >
            <X size={14} strokeWidth={3} />
          </button>
        )}
        {quickEdit && !selectable && (
          // Drop straight into the edit page without opening the book first
          // (fork #572). Sits inside the card's <Link>, so stop the click from
          // also navigating to the detail view.
          <button
            type="button"
            className={styles.quickEditBtn}
            aria-label={`Edit ${book.title}`}
            title="Edit metadata"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              navigate(`/book/${book.id}/edit`);
            }}
          >
            <Pencil size={13} strokeWidth={2.5} />
          </button>
        )}
      </div>
      <div className={styles.info}>
        <p className={styles.title}>{book.title}</p>
        <p className={styles.author}>{authorStr}</p>
      </div>
    </article>
  );

  // In selection mode the whole card toggles selection instead of navigating.
  if (selectable) {
    return (
      <button
        type="button"
        className={styles.cardLink}
        onClick={() => onToggleSelect?.(book)}
        aria-label={`${selected ? 'Deselect' : 'Select'} ${book.title}`}
      >
        {inner}
      </button>
    );
  }

  return (
    <Link href={`/book/${book.id}`} className={styles.cardLink}>
      {inner}
    </Link>
  );
}
