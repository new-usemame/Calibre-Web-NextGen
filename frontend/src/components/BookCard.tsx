import { BookOpen, Check, EyeOff, X, Pencil } from 'lucide-react';
import { Link } from 'wouter';
import type { Book } from '../lib/api';
import { useT } from '../lib/i18n';
import { BookCover } from './BookCover';
import { getPrimaryReadTarget } from '../lib/readerTarget';
import { formatAuthors } from '../lib/authors';
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
  const t = useT();
  const authorStr = formatAuthors(book.authors);
  const seriesIndexLabel = showSeriesIndex ? formatSeriesIndex(book.series_index) : null;
  const readTarget = getPrimaryReadTarget(book.id, book.formats);

  // Cover + overlay badges. All non-interactive (pointer-events: none via CSS) so
  // the single wrapping control (link or toggle button) is the only tab stop.
  const cover = (
    <div className={styles.coverWrap}>
      <BookCover coverUrl={book.cover_url} title={book.title} authors={book.authors} />
      {book.read && (
        <span className={styles.readBadge} role="img" aria-label={t('Read')}>
          <Check size={14} strokeWidth={3} aria-hidden="true" />
        </span>
      )}
      {book.hidden && (
        <span className={styles.hiddenBadge} role="img" aria-label={t('Hidden')}
          data-testid="hidden-book-badge">
          <EyeOff size={12} aria-hidden="true" focusable={false} />
          {t('Hidden')}
        </span>
      )}
      {seriesIndexLabel && (
        <span
          className={styles.seriesBadge}
          role="img"
          aria-label={t('Series position {n}', { n: seriesIndexLabel })}
        >
          #{seriesIndexLabel}
        </span>
      )}
      {selectable && (
        <span className={selected ? styles.checkboxOn : styles.checkboxOff} aria-hidden="true">
          {selected && <Check size={14} strokeWidth={3} />}
        </span>
      )}
    </div>
  );

  const info = (
    <div className={styles.info}>
      <p className={styles.title}>{book.title}</p>
      <p className={styles.author}>{authorStr}</p>
    </div>
  );

  // Selection mode: the whole card is a single toggle button. aria-pressed is
  // valid here (a real button) and announces the selection state.
  if (selectable) {
    return (
      <div className={styles.wrap} style={style}>
        <button
          type="button"
          className={selected ? styles.cardSelected : styles.card}
          aria-pressed={selected}
          aria-label={
            selected
              ? t('Deselect {title}', { title: book.title })
              : t('Select {title}', { title: book.title })
          }
          onClick={() => onToggleSelect?.(book)}
        >
          {cover}
          {info}
        </button>
      </div>
    );
  }

  // Browse mode: the card is a single link. Action buttons are SIBLINGS of the
  // link (never nested inside <a> — that's invalid + a second tab stop),
  // absolutely positioned over the cover by .wrap.
  return (
    <div className={styles.wrap} style={style}>
      <Link href={`/book/${book.id}`} className={styles.card} aria-label={t('Open details for {title}', { title: book.title })}>
        {cover}
        {info}
      </Link>
      {readTarget && (
        <Link href={readTarget} className={styles.readNow} aria-label={t('Read {title}', { title: book.title })}>
          <BookOpen size={15} aria-hidden="true" focusable={false} /> {t('Read now')}
        </Link>
      )}
      {onRemove && (
        <button
          type="button"
          className={styles.removeBtn}
          aria-label={t(removeLabel)}
          onClick={() => onRemove(book)}
        >
          <X size={14} strokeWidth={3} aria-hidden="true" />
        </button>
      )}
      {quickEdit && (
        <Link
          href={`/book/${book.id}/edit`}
          className={styles.quickEditBtn}
          aria-label={t('Edit {title}', { title: book.title })}
          // The pencil is a SIBLING of the card link (never nested in an <a>),
          // so a click can't bubble to the card's own navigation — stopPropagation
          // keeps that invariant explicit if the layout is ever re-nested.
          // wouter's <Link> runs SPA navigation only on a plain left-click; on
          // ⌘/ctrl/shift/alt-click it returns early without preventDefault, so the
          // browser opens the edit page in a new tab natively (#798).
          onClick={(e) => e.stopPropagation()}
        >
          <Pencil size={13} strokeWidth={2.5} aria-hidden="true" />
        </Link>
      )}
    </div>
  );
}
