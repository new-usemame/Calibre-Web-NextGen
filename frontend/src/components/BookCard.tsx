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
  /** Drop the whole bottom action row — "Read now" and the edit pencil — for
   *  users who asked to declutter the grid (fork #1054: "many users are reading
   *  on their ereaders, so Read Now is redundant"). Persisted per browser and
   *  toggled from the catalog's View settings.
   *
   *  This removes the row rather than hiding it: an `opacity: 0` control (what
   *  the hover-reveal uses) is still focusable, so a user who has switched these
   *  off would keep tabbing through two invisible controls per card. Both
   *  actions remain on the book's own page, which the cover already links to. */
  hideActions?: boolean;
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
  hideActions = false,
}: BookCardProps) {
  const t = useT();
  const authorStr = formatAuthors(book.authors);
  const seriesIndexLabel = showSeriesIndex ? formatSeriesIndex(book.series_index) : null;
  const readTarget = getPrimaryReadTarget(book.id, book.formats);

  // Series name + position under the cover (fork #657, #673, #855). Series-heavy
  // libraries navigate by series and want it visible without clicking into each
  // book — parity with the classic view, which showed it under the cover. Shown
  // wherever a book appears in a general list; suppressed in the series-detail
  // view (showSeriesIndex), where every card is the same series and the position
  // already shows as the #N badge, so a repeated name would be noise.
  const cardIndexLabel = formatSeriesIndex(book.series_index);
  const seriesLine =
    !showSeriesIndex && book.series
      ? cardIndexLabel
        ? t('{series} #{n}', { series: book.series, n: cardIndexLabel })
        : book.series
      : null;

  // Cover + overlay badges. All non-interactive (pointer-events: none via CSS) so
  // the single wrapping control (link or toggle button) is the only tab stop.
  const cover = (
    <div className={styles.coverWrap}>
      <BookCover coverUrl={book.cover_url} title={book.title} authors={book.authors} />
      {/* One bottom-left row rather than three independently-positioned badges.
          `hiddenBadge` and `seriesBadge` were BOTH pinned to bottom-left, so a
          hidden book in a series view stacked them on top of each other; and
          the read badge moving down here (#1117) would have made a third.
          A flex row makes overlap impossible by construction instead of by
          each badge hoping the others are absent. */}
      <div className={styles.badgeRow}>
        {/* role=img + aria-label on these badges is the established pattern from
            the WCAG pass: it announces the badge once, rather than letting the
            icon and the adjacent text be read as two separate things. Keep it
            even now that the label is visible. */}
        {book.read && (
          <span className={styles.readBadge} role="img" aria-label={t('Read')}
            data-testid="read-badge">
            <Check size={13} strokeWidth={3} aria-hidden="true" focusable={false} />
            {t('Read')}
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
      </div>
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
      {seriesLine && (
        <p className={styles.series} data-testid="book-card-series">{seriesLine}</p>
      )}
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

  // Browse mode: the card is a single link. Action controls are SIBLINGS of the
  // link (never nested inside <a> — that's invalid + a second tab stop).
  //
  // The read + edit controls share one flex row in NORMAL FLOW below the
  // metadata (#1166). They used to be absolutely positioned over the bottom of
  // the card, with room for the pencil reserved as fixed padding on the label
  // (#1112) — which holds only while the card is wider than the reservation. On
  // a 4-column phone grid the card is ~80px and the reservation 60px, so the
  // label wrapped to two lines, the row grew, and the 44px pencil rose into the
  // series line. Reported by rogovmtlz, @iroQuai and @HLRobius.
  //
  // A real row lets the browser allocate the space instead of the stylesheet
  // guessing at it, so a control can no longer land on top of card text at any
  // width, density or locale — the same "impossible by construction" move the
  // badge row above makes. `.removeBtn` stays absolute: it belongs to the cover,
  // not to this row.
  const hasActionRow = !hideActions && (Boolean(readTarget) || quickEdit);

  return (
    <div className={styles.wrap} style={style}>
      <Link href={`/book/${book.id}`} className={styles.card} aria-label={t('Open details for {title}', { title: book.title })}>
        {cover}
        {info}
      </Link>
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
      {hasActionRow && (
        <div className={quickEdit ? `${styles.actionRow} ${styles.actionRowEdit}` : styles.actionRow}>
          {readTarget && (
            <Link
              href={readTarget}
              className={styles.readNow}
              aria-label={t('Read {title}', { title: book.title })}
            >
              <BookOpen size={15} aria-hidden="true" focusable={false} />
              {/* The text is a span so a narrow card can drop the wording and
                  keep the icon, rather than ellipsising it to "R…". The
                  aria-label above still names the action either way. */}
              <span className={styles.readNowLabel}>{t('Read now')}</span>
            </Link>
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
              <Pencil size={13} strokeWidth={2.5} aria-hidden="true" focusable={false} />
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
