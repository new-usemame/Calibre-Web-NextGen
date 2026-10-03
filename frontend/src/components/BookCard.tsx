import { memo } from 'react';
import { BookOpen, BookCheck, BookPlus, Check, EyeOff, List, X, Star } from 'lucide-react';
import { Link } from 'wouter';
import type { Book } from '../lib/api';
import { useT } from '../lib/i18n';
import { BookCover } from './BookCover';
import { getPrimaryReadTarget } from '../lib/readerTarget';
import { formatAuthors } from '../lib/authors';
import styles from './BookCard.module.css';
import { Spinner } from './Spinner';
import { BookCardActions } from './BookCardActions';
import { ShelfDragHandle, useShelfDrag } from './ShelfDrag';

interface BookCardProps {
  book: Book;
  style?: React.CSSProperties;
  /** When provided, a remove (×) control is shown on the cover (e.g. on a shelf). */
  onRemove?: (book: Book) => void;
  removeLabel?: string;
  /** Selection mode: render as a toggle (not a link), with a checkbox overlay. */
  selectable?: boolean;
  selected?: boolean;
  /** Keep an in-flight bulk operation attached to its selection. */
  selectionDisabled?: boolean;
  /** `extend` is true for Shift+click: select the run from the previous click. */
  onToggleSelect?: (book: Book, extend: boolean) => void;
  /** When true, show the book's position within its series (#573) — used by the
   *  series view so the reading order is visible without duplicating it in titles. */
  showSeriesIndex?: boolean;
  /** Show a hover pencil that jumps straight to the edit page (fork #572). Opt-in
   *  so it only appears where it's wanted (catalog + search) and only for users
   *  who can edit. Suppressed in selection mode. */
  quickEdit?: boolean;
  /** Remove the cover action disclosure (read, edit, favorite and read status) for
   *  users who asked to declutter the grid (fork #1054: "many users are reading
   *  on their ereaders, so Read Now is redundant"). Persisted per browser and
   *  toggled from the catalog's View settings.
   *
   *  This removes the row rather than hiding it: an `opacity: 0` control (what
   *  the hover-reveal uses) is still focusable, so a user who has switched these
   *  off would keep tabbing through two invisible controls per card. Both
   *  actions remain on the book's own page, which the cover already links to. */
  hideActions?: boolean;
  /** Hide the compact Reading / Read status badges while leaving book state,
   * filters, progress and ordinary metadata tags untouched. */
  hideReadingTags?: boolean;
  /** Hide the shelf tags on the cover (#1254) — the per-user "Show shelf tags"
   *  view setting, shared with the classic grid's "Hide shelf badges". */
  hideShelfTags?: boolean;
  /** On a shelf's own page every card is on that shelf, so its tag is noise. */
  excludeShelfId?: number;
  /** Global-library surfaces only. The Add action stays visible even when the
   * user hid ordinary card actions, because adding is this surface's purpose. */
  membership?: 'owned' | 'unowned';
  onAddToLibrary?: (book: Book) => void;
  addPending?: boolean;
  /** Opt out only on surfaces that intentionally render a non-navigable card.
   * Global Library details are membership-independent global metadata. */
  detailsEnabled?: boolean;
  /** The authenticated account's viewer role. Kept explicit so a catalog card
   *  can never infer file access from the formats it happens to receive. */
  canRead?: boolean;
}

/** How many shelf names a cover shows before the rest fold into "+N". Two, not
 *  the classic grid's three: an SPA phone card is ~80px wide, and a third
 *  stacked tag would cover most of the art. The +N tag names the rest. */
const MAX_SHELF_TAGS = 2;

/** Format a Calibre series_index (a float, e.g. 1.0, 2.5) for display: whole
 *  numbers show as "1", fractional as "2.5". Returns null when there's nothing
 *  to show so the badge is omitted entirely. */
function formatSeriesIndex(idx: number | null | undefined): string | null {
  if (idx == null || Number.isNaN(idx)) return null;
  return Number.isInteger(idx) ? String(idx) : String(idx);
}

function BookCardInner({
  book, style, onRemove, removeLabel = 'Remove',
  selectable = false, selected = false, selectionDisabled = false, onToggleSelect,
  showSeriesIndex = false,
  quickEdit = false,
  hideActions = false,
  hideReadingTags = false,
  hideShelfTags = false,
  excludeShelfId,
  membership,
  onAddToLibrary,
  addPending = false,
  detailsEnabled = true,
  canRead = false,
}: BookCardProps) {
  const t = useT();
  const shelfDrag = useShelfDrag();
  const authorStr = formatAuthors(book.authors);
  const seriesIndexLabel = showSeriesIndex ? formatSeriesIndex(book.series_index) : null;
  const readTarget = getPrimaryReadTarget(book.id, book.formats, canRead);
  const coverActions = !selectable && !hideActions && membership !== 'unowned';

  // Series name + position under the cover (fork #657, #673, #855). Series-heavy
  // libraries navigate by series and want it visible without clicking into each
  // book — parity with the classic view, which showed it under the cover. Shown
  // wherever a book appears in a general list; suppressed in the series-detail
  // view (showSeriesIndex), where every card is the same series and the position
  // already shows as the #N badge, so a repeated name would be noise.
  //
  // The name and the position are separate elements (#2051, reported by
  // @magdalar): as one clamped string, any name long enough to ellipsise took
  // the position with it. Only the name shrinks now; whatever the translation
  // puts around it ("#4", "Band 4", "nº 4") stays pinned beside it. The template
  // is rendered with the placeholder left in place and split there, so a locale
  // keeps its own wording and order.
  const cardIndexLabel = formatSeriesIndex(book.series_index);
  let seriesParts: { before: string; name: string; after: string } | null = null;
  if (!showSeriesIndex && book.series) {
    const parts = cardIndexLabel
      ? t('{series} #{n}', { series: '{series}', n: cardIndexLabel }).split('{series}')
      : null;
    seriesParts = parts && parts.length === 2
      ? { before: parts[0], name: book.series, after: parts[1] }
      : { before: '', name: book.series, after: cardIndexLabel ? ` #${cardIndexLabel}` : '' };
  }

  // Shelf tags (#1254, reported by @lguerard; #2261 asked the same through the
  // feedback form). The classic grid put the shelf name on the cover and people
  // switched back to it to see where a book was filed.
  const shelves = hideShelfTags
    ? []
    : (book.shelves ?? []).filter((s) => s.id !== excludeShelfId);
  const shownShelves = shelves.slice(0, MAX_SHELF_TAGS);
  const extraShelves = shelves.slice(MAX_SHELF_TAGS);

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
      {/* Top-right, stacked: top-left belongs to the selection checkbox and the
          shelf page's remove button, bottom-left to the status badge row. When
          either top-left control can appear, the row's left edge is inset past
          it so a long shelf name truncates instead of running under it;
          otherwise the name gets the full cover width. */}
      {shelves.length > 0 && (
        <div
          className={selectable || onRemove || coverActions ? `${styles.shelfRow} ${styles.shelfRowInset}` : styles.shelfRow}
          data-testid="shelf-tags"
        >
          <div className={styles.shelfLabels}>
          {shownShelves.map((s) => (
            <span key={s.id} className={styles.shelfBadge} role="img"
              aria-label={t('On shelf {name}', { name: s.name })} title={s.name}>
              <List size={11} strokeWidth={2.5} aria-hidden="true" focusable={false} />
              <span className={styles.shelfBadgeName} dir="auto">{s.name}</span>
            </span>
          ))}
          {extraShelves.length > 0 && (
            <span className={styles.shelfBadge} role="img"
              aria-label={t('Also on {names}', { names: extraShelves.map((s) => s.name).join(', ') })}
              title={extraShelves.map((s) => s.name).join(', ')}>
              +{extraShelves.length}
            </span>
          )}
          </div>
          <span className={styles.shelfSummary} role="img"
            aria-label={t('On shelf {name}', { name: shelves.map(s => s.name).join(', ') })}
            title={shelves.map(s => s.name).join(', ')}>
            {shelves.length}
          </span>
        </div>
      )}
      <div className={styles.badgeRow}>
        {/* role=img + aria-label on these badges is the established pattern from
            the WCAG pass: it announces the badge once, rather than letting the
            icon and the adjacent text be read as two separate things. Keep it
            even now that the label is visible. */}
        {!hideReadingTags && (book.read_status === 'did_not_finish' || book.read_status === 'on_hold') ? (
          <span className={styles.readingBadge}>
            {book.read_status === 'did_not_finish' ? t('Did not finish') : t('On hold')}
          </span>
        ) : !hideReadingTags && book.in_progress ? (
          <span className={styles.readingBadge} role="img" aria-label={t('Reading')}
            data-testid="reading-badge">
            <BookOpen size={13} strokeWidth={2.5} aria-hidden="true" focusable={false} />
            <span className={styles.badgeLabel}>{t('Reading')}</span>
          </span>
        ) : !hideReadingTags && book.read ? (
          <span className={styles.readBadge} role="img" aria-label={t('Read')}
            data-testid="read-badge">
            <Check size={13} strokeWidth={3} aria-hidden="true" focusable={false} />
            <span className={styles.badgeLabel}>{t('Read')}</span>
          </span>
        ) : null}
        {book.hidden && (
          <span className={styles.hiddenBadge} role="img" aria-label={t('Hidden')}
            data-testid="hidden-book-badge">
            <EyeOff size={12} aria-hidden="true" focusable={false} />
            <span className={styles.badgeLabel}>{t('Hidden')}</span>
          </span>
        )}
        {membership === 'owned' && (
          <span className={styles.libraryBadge} role="img" aria-label={t('In your library')}>
            <BookCheck size={12} aria-hidden="true" focusable={false} />
            <span className={styles.badgeLabel}>{t('In your library')}</span>
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
        {book.favorited && <span className={styles.favoriteBadge} role="img" aria-label={t('Favorite')}
          data-testid="favorite-badge"><Star size={14} fill="currentColor" aria-hidden="true" focusable={false} /></span>}
      </div>
      {selectable && (
        <span className={selected ? styles.checkboxOn : styles.checkboxOff} aria-hidden="true">
          {selected && <Check size={14} strokeWidth={3} />}
        </span>
      )}
    </div>
  );

  // dir="auto" per field, not once on the card (#1073, reported by @raphaelbahat).
  // The browser picks direction from the first strong directional character in
  // THAT string, so a Hebrew title above an English author renders each the right
  // way round. A single card-level or book-level flag has to be wrong about one
  // of them, and keying off the book's language metadata would miss the many
  // libraries that leave language unset — which is the case in the report.
  const info = (
    <div className={styles.info}>
      <p className={styles.title} dir="auto">{book.title}</p>
      <p className={styles.author} dir="auto">{authorStr}</p>
    </div>
  );

  const seriesContent = seriesParts && (
    <>
      {seriesParts.before && <span className={styles.seriesIndex}>{seriesParts.before}</span>}
      <span className={styles.seriesName}>{seriesParts.name}</span>
      {seriesParts.after && <span className={styles.seriesIndex}>{seriesParts.after}</span>}
    </>
  );
  const seriesTitle = seriesParts && seriesParts.before + seriesParts.name + seriesParts.after;
  const seriesLine = seriesParts && (
    <p className={styles.series} dir="auto" data-testid="book-card-series" title={seriesTitle!}>
      {seriesContent}
    </p>
  );

  // Selection mode: the whole card is a single toggle button. aria-pressed is
  // valid here (a real button) and announces the selection state.
  if (selectable) {
    return (
      <div className={styles.wrap} style={style} data-book-id={book.id}>
        <button
          type="button"
          draggable={!!shelfDrag?.available && !selectionDisabled && !shelfDrag.busy}
          onDragStart={event => shelfDrag?.nativeStart(book, event)}
          onDragEnd={() => shelfDrag?.cancel()}
          className={selected ? styles.cardSelected : styles.card}
          disabled={selectionDisabled || shelfDrag?.busy}
          aria-pressed={selected}
          aria-label={
            selected
              ? t('Deselect {title}', { title: book.title })
              : t('Select {title}', { title: book.title })
          }
          // Shift+mousedown would otherwise extend a text selection across the cards.
          onMouseDown={(e) => { if (e.shiftKey) e.preventDefault(); }}
          onClick={(e) => onToggleSelect?.(book, e.shiftKey)}
        >
          {cover}
          {info}
          {seriesLine}
        </button>
        <ShelfDragHandle book={book} disabled={selectionDisabled || shelfDrag?.busy} />
      </div>
    );
  }

  // The cover, title and author open the book; the series line keeps its own
  // sibling destination. A single sibling disclosure lives inside the cover's
  // aspect-ratio area, with its portaled panel outside a clipped book rail.
  const hasAddAction = membership === 'unowned' && !!onAddToLibrary;

  return (
    <div className={styles.wrap} style={style} data-book-id={book.id}>
      {detailsEnabled ? (
        <Link href={`/book/${book.id}`} draggable={!!shelfDrag?.available && !selectionDisabled && !shelfDrag.busy} onDragStart={event => shelfDrag?.nativeStart(book, event)} onDragEnd={() => shelfDrag?.cancel()} className={styles.card} aria-label={t('Open details for {title}', { title: book.title })}>
          {cover}{info}
        </Link>
      ) : (
        <div className={styles.card} aria-label={book.title}>{cover}{info}</div>
      )}
      {seriesLine && (detailsEnabled && book.series_id != null ? (
        <Link href={`/series/${book.series_id}`} className={`${styles.series} ${styles.seriesLink}`}
          dir="auto" data-testid="book-card-series" title={seriesTitle!}>
          {seriesContent}
        </Link>
      ) : (
        seriesLine
      ))}
      <ShelfDragHandle book={book} disabled={selectionDisabled} />
      {coverActions && <div className={styles.coverActions}>
        <BookCardActions book={book} readTarget={readTarget} quickEdit={quickEdit}
          onRemove={onRemove} removeLabel={removeLabel} shelfNames={shelves.map(shelf => shelf.name)} />
      </div>}
      {onRemove && !coverActions && (
        <button
          type="button"
          className={styles.removeBtn}
          aria-label={t(removeLabel)}
          onClick={() => onRemove(book)}
        >
          <X size={14} strokeWidth={3} aria-hidden="true" />
        </button>
      )}
      {hasAddAction && <div className={styles.actionRow}>
        <button type="button" className={`${styles.readNow} ${styles.addToLibrary}`}
          disabled={addPending} aria-label={t('Add {title} to my library', { title: book.title })}
          onClick={() => onAddToLibrary?.(book)}>
          {addPending ? <Spinner size={13} /> : <BookPlus size={15} aria-hidden="true" focusable={false} />}
          <span className={styles.readNowLabel}>{addPending ? t('Adding…') : t('Add')}</span>
        </button>
      </div>}
    </div>
  );
}

/** Grid items re-render only when their own props change: a selection tap or
 *  settings refetch in a 500-card catalog must not reconcile every card. */
export const BookCard = memo(BookCardInner);
