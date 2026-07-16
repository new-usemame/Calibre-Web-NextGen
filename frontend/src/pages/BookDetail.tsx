import { useState, useEffect, useRef, Fragment } from 'react';
import { Link, useParams, useLocation } from 'wouter';
import { Download, Pencil, Star, Archive, EyeOff, Eye, Send, Highlighter, Image as ImageIcon, Plus, X, BookOpen, Trash2, RefreshCw } from 'lucide-react';
import {
  useBook, useToggleRead, useToggleFavorite, useToggleArchived, useToggleHidden,
  useSendToEreader, useMe, useAccount, useUpdateMetadata, useDeleteBook, useReloadMetadata,
} from '../lib/queries';
import { Pill } from '../components/Pill';
import { AddToShelf } from '../components/AddToShelf';
import { StarRating } from '../components/StarRating';
import { MoreByAuthor } from '../components/MoreByAuthor';
import { AUTHOR_SEPARATOR } from '../lib/authors';
import { SpinnerCentered, Spinner } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { CustomColumn, CustomColumnValue, EntityRef } from '../lib/api';
import { ApiError, resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
import { getPrimaryReadTarget } from '../lib/readerTarget';
import styles from './BookDetail.module.css';

function formatBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return mb >= 0.1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
}

function formatDate(date: string, alwaysReturnFullDate = false): string {
  // Try to parse as ISO date — return just the year if it looks like a date
  const d = new Date(date);
  if (!isNaN(d.getTime())) {
    if (!alwaysReturnFullDate) {
      // If the time portion is midnight UTC it's likely just a year+date, show full date
      const year = d.getFullYear();
      const month = d.getMonth();
      const day = d.getDate();
      if (month === 0 && day === 1) return String(year);
    }
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
  }
  return date;
}

function formatCustomValue(column: CustomColumn, entry: CustomColumnValue, yes: string, no: string): string {
  const value = entry.value;
  if (value === null || value === undefined) return '';
  if (column.datatype === 'bool') return value ? yes : no;
  if (column.datatype === 'datetime' && typeof value === 'string') return formatDate(value, true);
  if (column.datatype === 'rating' && typeof value === 'number') return `${value / 2}/5`;
  if ((column.datatype === 'int' || column.datatype === 'float') && typeof value === 'number') {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: column.datatype === 'float' ? 2 : 0 }).format(value);
  }
  return String(value);
}

// Formats the in-browser reader can open. EPUB/KEPUB use the SPA's epub.js
// reader; the rest (PDF, comics, plain text, DjVu, audiobooks) open in the
// server's format-specific reader at read_url — so every readable format the
// library supports is reachable from the SPA, not just EPUB.
interface SendPanelProps {
  formats: string[];
  pending: boolean;
  banner: { ok: boolean; text: string } | null;
  /** User's saved e-reader address, used to prefill the recipient field (#715). */
  defaultEmail: string;
  onSend: (format: string, convert: boolean, emails: string) => void;
}

/** Compact send-to-e-reader form: pick a format, optionally convert, optionally
 *  override the recipient(s). The recipient field is prefilled with the user's
 *  saved e-reader address (#715 — previously the field was blank with only a
 *  "blank = your e-reader email" hint, so users thought the address was lost).
 *  Empty recipient still falls back to the saved address server-side. */
function SendPanel({ formats, pending, banner, defaultEmail, onSend }: SendPanelProps) {
  const t = useT();
  const [format, setFormat] = useState(formats[0] ?? '');
  const [convert, setConvert] = useState(false);
  const [emails, setEmails] = useState(defaultEmail);
  const dirty = useRef(false);
  // The account query (which serves kindle_mail) may resolve after the panel
  // first mounts; keep seeding the saved address until the user edits the field.
  useEffect(() => {
    if (!dirty.current && defaultEmail) setEmails(defaultEmail);
  }, [defaultEmail]);
  return (
    <div className={styles.sendPanel}>
      <div className={styles.sendRow}>
        <label className={styles.sendField}>
          <span>{t('Format')}</span>
          <select value={format} onChange={(e) => setFormat(e.target.value)}>
            {formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
          </select>
        </label>
        <label className={styles.sendField}>
          <span>{t('Recipient(s)')}</span>
          <input
            type="text" value={emails}
            placeholder={defaultEmail || 'a@kindle.com, b@kindle.com'}
            onChange={(e) => { dirty.current = true; setEmails(e.target.value); }}
          />
          {defaultEmail && (
            <small className={styles.sendHint}>
              {t('Your saved e-reader address is shown — change it for this send only.')}
            </small>
          )}
        </label>
      </div>
      <div className={styles.sendActions}>
        <label className={styles.sendConvert}>
          <input type="checkbox" checked={convert} onChange={(e) => setConvert(e.target.checked)} />
          {t('Convert before sending')}
        </label>
        <button
          className={styles.actionPrimary}
          disabled={pending || !format}
          onClick={() => onSend(format, convert, emails.trim())}
        >
          {pending ? t('Sending…') : t('Send')}
        </button>
      </div>
      <p className={banner ? (banner.ok ? styles.sendOk : styles.sendErr) : undefined} role="status">{banner?.text}</p>
    </div>
  );
}

/** Inline tag add/remove on the book page (fork #572), so you can tweak a book's
 *  tags without opening the full editor and hand-editing a comma-separated string.
 *  The /metadata endpoint has replace semantics for `tags`, so each change rebuilds
 *  the whole comma-separated string from the book's current tags. Non-editors see
 *  the original read-only linked pills. */
function TagEditor({ bookId, tags, canEdit }:
  { bookId: number; tags: EntityRef[]; canEdit: boolean }) {
  const t = useT();
  const update = useUpdateMetadata(bookId);
  const [adding, setAdding] = useState(false);
  const [input, setInput] = useState('');
  const [expanded, setExpanded] = useState(false);
  const visibleTags = expanded ? tags : tags.slice(0, 8);
  const hasMore = tags.length > 8;

  const names = tags.map((tg) => tg.name);
  const apply = (next: string[]) => update.mutate({ tags: next.join(', ') });
  const removeTag = (name: string) => apply(names.filter((n) => n !== name));
  const addTag = () => {
    const v = input.trim();
    if (!v) { setAdding(false); return; }
    // Case-insensitive dedupe — don't re-add an existing tag.
    if (!names.some((n) => n.toLowerCase() === v.toLowerCase())) apply([...names, v]);
    setInput('');
    setAdding(false);
  };

  if (!canEdit) {
    if (tags.length === 0) return null;
    return (
      <div className={styles.tags} id="book-tags">
        {visibleTags.map((tag) => (
          <Link key={tag.id} href={`/tags/${tag.id}`} className={styles.tagLink}>
            <Pill>{tag.name}</Pill>
          </Link>
        ))}
        {hasMore && (
          <button type="button" className={styles.tagsDisclosure}
            aria-expanded={expanded} aria-controls="book-tags"
            onClick={() => setExpanded((value) => !value)}>
            {expanded ? t('Show fewer tags') : t('Show all {count} tags', { count: tags.length })}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={styles.tags} id="book-tags">
      {visibleTags.map((tag) => (
        <span key={tag.id} className={styles.tagChip}>
          <Link href={`/tags/${tag.id}`} className={styles.tagChipLink}>{tag.name}</Link>
          <button
            type="button"
            className={styles.tagRemove}
            aria-label={t('Remove tag {name}', { name: tag.name })}
            title={t('Remove tag')}
            disabled={update.isPending}
            onClick={() => removeTag(tag.name)}
          >
            <X size={12} strokeWidth={2.5} />
          </button>
        </span>
      ))}
      {hasMore && (
        <button type="button" className={styles.tagsDisclosure}
          aria-expanded={expanded} aria-controls="book-tags"
          onClick={() => setExpanded((value) => !value)}>
          {expanded ? t('Show fewer tags') : t('Show all {count} tags', { count: tags.length })}
        </button>
      )}
      {adding ? (
        <span className={styles.tagAddRow}>
          <input
            className={styles.tagAddInput}
            value={input}
            autoFocus
            placeholder={t('New tag')}
            aria-label={t('Add tag')}
            disabled={update.isPending}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); addTag(); }
              else if (e.key === 'Escape') { setInput(''); setAdding(false); }
            }}
            onBlur={addTag}
          />
          {update.isPending && <Spinner size={13} />}
        </span>
      ) : (
        <button
          type="button"
          className={styles.tagAddBtn}
          disabled={update.isPending}
          onClick={() => setAdding(true)}
        >
          <Plus size={13} strokeWidth={2.5} /> {t('Add tag')}
        </button>
      )}
    </div>
  );
}

export function BookDetail() {
  const t = useT();
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data: book, isLoading, error } = useBook(id);
  const toggleRead = useToggleRead(id);
  const toggleFavorite = useToggleFavorite(id);
  const toggleArchived = useToggleArchived(id);
  const toggleHidden = useToggleHidden(id);
  const sendToEreader = useSendToEreader(id);
  const deleteBook = useDeleteBook(id);
  const reloadMetadata = useReloadMetadata(id);
  const [, navigate] = useLocation();
  const me = useMe().data;
  // The send-to-e-reader button only renders when mail is configured + the user
  // can download, so defer the account fetch (which carries the saved e-reader
  // address used to prefill the recipient field, #715) until that's possible.
  const canSend = !!me?.features?.mail_configured && !!me?.role?.download;
  const savedEreader = useAccount({ enabled: canSend }).data?.kindle_mail ?? '';
  const [sendOpen, setSendOpen] = useState(false);
  const [sendBanner, setSendBanner] = useState<{ ok: boolean; text: string } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [reloadMessage, setReloadMessage] = useState('');

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !book) {
    return (
      <main className={styles.container}>
        <Link href="/" className={styles.back}>{t('← Library')}</Link>
        <EmptyState message={error instanceof Error ? error.message : t('Book not found.')} />
      </main>
    );
  }

  const primaryReadTarget = getPrimaryReadTarget(book.id, book.formats.map((f) => f.format));

  return (
    <main className={styles.container}>
      <Link href="/" className={styles.back}>{t('← Library')}</Link>

      <div className={styles.layout}>
        {/* LEFT: cover */}
        <div className={styles.coverCol}>
          <div className={styles.coverWrap}>
            {book.cover_url ? (
              <img
                src={resourceUrl(book.cover_url)}
                alt={book.title}
                className={styles.cover}
              />
            ) : (
              <div className={styles.coverFallback} aria-label={book.title}>
                <span className={styles.coverFallbackTitle}>{book.title}</span>
                {book.authors.length > 0 && (
                  <span className={styles.coverFallbackAuthor}>{book.authors[0].name}</span>
                )}
                <span className={styles.coverFallbackMark} aria-hidden="true">NextGen</span>
              </div>
            )}
            {me?.role?.edit && (
              <Link href={`/book/${book.id}/cover`} className={styles.changeCover}>
                <ImageIcon size={15} /> {t('Change cover')}
              </Link>
            )}
          </div>
        </div>

        {/* RIGHT: info */}
        <div className={styles.infoCol}>
          <div>
            <h1 className={styles.title}>{book.title}</h1>
            {book.authors.length > 0 && (
              <p className={styles.authors}>
                {book.authors.map((a, i) => (
                  <span key={a.id}>
                    {i > 0 && AUTHOR_SEPARATOR}
                    <Link href={`/authors/${a.id}`} className={styles.metaLink}>{a.name}</Link>
                  </span>
                ))}
              </p>
            )}
            {book.series && (
              <p className={styles.series}>
                <Link href={`/series/${book.series.id}`} className={styles.metaLink}>
                  {book.series.name}
                </Link>
                {' · '}
                {t('Book {number}', { number: book.series_index })}
              </p>
            )}
            {/* Rating — star parity with the classic detail page. Calibre stores
                0–10 (half-star granularity); null means unrated (no stars shown,
                not zero stars). */}
            {book.rating != null && book.rating > 0 && (
              <div className={styles.rating}>
                <StarRating rating={book.rating} size={16} />
              </div>
            )}
            {/* Passive "currently reading" marker (fork #634) — mirrors the classic
                detail page. Sync-driven display only; the read toggle below stays a
                2-state read/unread control. Shows the synced percent when known. */}
            {book.in_progress && (
              <div className={styles.readProgressWrap}>
                <p className={styles.currentlyReading}>
                  <BookOpen size={14} aria-hidden="true" focusable={false} />
                  {book.kosync_progress != null
                    ? `${t('Currently reading')} · ${Math.round(book.kosync_progress)}%`
                    : t('Currently reading')}
                </p>
                {book.kosync_progress != null && (
                  <div className={styles.readProgress} role="progressbar"
                    aria-label={t('Reading progress')} aria-valuemin={0} aria-valuemax={100}
                    aria-valuenow={Math.round(book.kosync_progress)}>
                    <span style={{ width: `${Math.max(0, Math.min(100, book.kosync_progress))}%` }} />
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Actions */}
          <div className={styles.actions}>
            {primaryReadTarget ? (
              <Link href={primaryReadTarget} className={styles.actionPrimary}>
                {t('Read now')}
              </Link>
            ) : null}

            <button
              className={book.read ? styles.readToggleActive : styles.readToggleGhost}
              onClick={() => toggleRead.mutate(!book.read)}
              disabled={toggleRead.isPending}
              aria-label={book.read ? t('Mark as unread') : t('Mark as read')}
            >
              {book.read ? `${t('Read')} ✓` : t('Mark as read')}
            </button>

            <AddToShelf bookId={book.id} />

            {/* Star / favorite */}
            <button
              className={book.favorited ? styles.readToggleActive : styles.readToggleGhost}
              onClick={() => toggleFavorite.mutate()}
              disabled={toggleFavorite.isPending}
              aria-label={book.favorited ? t('Remove from favorites') : t('Add to favorites')}
            >
              <Star size={14} fill={book.favorited ? 'currentColor' : 'none'} />
              {book.favorited ? t('Favorited') : t('Favorite')}
            </button>

            {/* Archive (sync-pause) */}
            <button
              data-testid="archive-book-toggle"
              className={book.archived ? styles.readToggleActive : styles.readToggleGhost}
              onClick={() => toggleArchived.mutate()}
              disabled={toggleArchived.isPending}
              aria-label={book.archived ? t('Unarchive') : t('Archive')}
            >
              <Archive size={14} />
              {book.archived ? t('Archived') : t('Archive')}
            </button>

            {book.formats.map((fmt) => (
              <a
                key={fmt.format}
                href={resourceUrl(fmt.download_url)}
                className={styles.downloadBtn}
                download
                // iOS Safari ignores the `download` hint and the server serves book
                // files with `Content-Disposition: inline` (needed for byte-range /
                // in-browser reading), so a same-tab tap navigates the SPA away to a
                // file the browser can't render — stranding the user on a dead page
                // until they force-restart (#716). Opening in a new tab preserves the
                // app tab; desktop browsers still honour `download` and don't spawn a
                // stray tab. `noopener` keeps the download context from reaching back.
                target="_blank"
                rel="noopener"
              >
                <Download size={15} />
                {fmt.format} · {formatBytes(fmt.size_bytes)}
              </a>
            ))}

            {/* Send to e-reader — gated on mail being configured + download role */}
            {me?.features?.mail_configured && me?.role?.download && book.formats.length > 0 && (
              <button
                className={styles.downloadBtn}
                onClick={() => { setSendOpen((v) => !v); setSendBanner(null); }}
                aria-label={t('Send to e-reader')}
              >
                <Send size={14} />
                {t('Send to e-reader')}
              </button>
            )}

            {me?.role?.edit && (
              <>
                <Link href={`/book/${book.id}/edit`} className={styles.downloadBtn}>
                  <Pencil size={14} aria-hidden="true" focusable={false} />
                  {t('Edit')}
                </Link>
                <button type="button" className={styles.downloadBtn}
                  disabled={reloadMetadata.isPending}
                  onClick={() => {
                    setReloadMessage('');
                    reloadMetadata.mutate(undefined, {
                      onSuccess: (result) => setReloadMessage(result.message),
                      onError: (err) => setReloadMessage(err instanceof ApiError ? err.message : t('Could not reload metadata')),
                    });
                  }}>
                  <RefreshCw size={14} aria-hidden="true" focusable={false} />
                  {reloadMetadata.isPending ? t('Reloading…') : t('Reload metadata from disk')}
                </button>
              </>
            )}

            {/* Highlights/annotations — view + export + import (Kobo). Opens the
                server annotations page; in-reader highlight creation is the
                flagship reader phase-2 (tracked separately). */}
            <Link href={`/book/${book.id}/annotations`} className={styles.downloadBtn}>
              <Highlighter size={14} aria-hidden="true" focusable={false} />
              {t('Highlights')}
            </Link>

            {/* Personal action, deliberately outside the delete-role gate. It
                sits immediately beside Delete when Delete is available and is
                still the final action for ordinary users. Guest sessions cannot
                own hidden state, so never offer them a control that returns 401. */}
            {!me?.role?.anonymous && (me?.features?.hide_books || book.hidden) && (
              <button
                type="button"
                data-testid="hide-book-toggle"
                className={book.hidden ? styles.readToggleActive : styles.readToggleGhost}
                onClick={() => toggleHidden.mutate(!book.hidden)}
                disabled={toggleHidden.isPending}
                aria-label={book.hidden ? t('Unhide') : t('Hide')}
              >
                {book.hidden
                  ? <Eye size={14} aria-hidden="true" focusable={false} />
                  : <EyeOff size={14} aria-hidden="true" focusable={false} />}
                {book.hidden ? t('Unhide') : t('Hide')}
              </button>
            )}

            {/* Delete the whole book — DB + files (fork #803). Hidden entirely for
                users without the delete role; the server re-checks and returns 403,
                so this is a UX gate, not the security boundary. */}
            {me?.role?.delete_books && (
              <button
                type="button"
                className={styles.actionDanger}
                disabled={deleteBook.isPending}
                aria-label={t('Delete book')}
                onClick={() => {
                  if (deleteBook.isPending) return;
                  if (!window.confirm(
                    t('Delete "{title}"? This permanently removes the book and all its files from your library. This cannot be undone.', { title: book.title })
                  )) return;
                  setDeleteError(null);
                  deleteBook.mutate(undefined, {
                    onSuccess: () => navigate('/'),
                    onError: (err) =>
                      setDeleteError(err instanceof ApiError ? err.message : t('Could not delete this book.')),
                  });
                }}
              >
                <Trash2 size={14} aria-hidden="true" focusable={false} />
                {deleteBook.isPending ? t('Deleting…') : t('Delete')}
              </button>
            )}
          </div>
          <p className={reloadMessage ? styles.actionStatus : undefined} role="status">{reloadMessage}</p>

          {deleteError && (
            <p className={styles.deleteErr} role="alert">{deleteError}</p>
          )}

          {/* Send-to-e-reader panel */}
          {sendOpen && (
            <SendPanel
              formats={book.formats.map((f) => f.format)}
              pending={sendToEreader.isPending}
              banner={sendBanner}
              defaultEmail={savedEreader}
              onSend={(format, convert, emails) => {
                setSendBanner(null);
                sendToEreader.mutate(
                  { format, convert, emails: emails || undefined },
                  {
                    onSuccess: (r) => { setSendBanner({ ok: true, text: r.message }); },
                    onError: (err) =>
                      setSendBanner({ ok: false, text: err instanceof ApiError ? err.message : t('Send failed.') }),
                  },
                );
              }}
            />
          )}

          {/* Tags — inline add/remove for editors (fork #572), read-only links
              otherwise. */}
          <TagEditor bookId={book.id} tags={book.tags} canEdit={!!me?.role?.edit} />

          {/* Metadata definition list */}
          <dl className={styles.meta}>
            {book.original_filename && (
              <>
                <dt className={styles.metaLabel}>{t('Imported as')}</dt>
                <dd className={styles.metaValue}>{book.original_filename}</dd>
              </>
            )}
            {book.kosync_progress != null && (
              <>
                <dt className={styles.metaLabel}>{t('KOReader Progress')}</dt>
                <dd className={styles.metaValue}>{book.kosync_progress.toFixed(1)}%</dd>
              </>
            )}
            {book.kosync_progress_created_at !== null && (
              <>
                <dt className={styles.metaLabel} title={t('When reading progress was first synced')}>
                  {t('Started reading')}
                </dt>
                <dd className={styles.metaValue}>{formatDate(book.kosync_progress_created_at, true)}</dd>
              </>
            )}
            {book.kosync_progress_timestamp !== null && (
              <>
                <dt className={styles.metaLabel}>{t('Last synced')}</dt>
                <dd className={styles.metaValue}>{formatDate(book.kosync_progress_timestamp, true)}</dd>
              </>
            )}
            {book.pubdate && (
              <>
                <dt className={styles.metaLabel}>{t('Published')}</dt>
                <dd className={styles.metaValue}>{formatDate(book.pubdate)}</dd>
              </>
            )}
            {book.date_added && (
              <>
                <dt className={styles.metaLabel}>{t('Date added')}</dt>
                <dd className={styles.metaValue}><time dateTime={book.date_added}>{formatDate(book.date_added, true)}</time></dd>
              </>
            )}
            {book.last_modified && (
              <>
                <dt className={styles.metaLabel}>{t('Last modified')}</dt>
                <dd className={styles.metaValue}><time dateTime={book.last_modified}>{formatDate(book.last_modified, true)}</time></dd>
              </>
            )}
            {book.languages.length > 0 && (
              <>
                <dt className={styles.metaLabel}>{book.languages.length === 1 ? t('Language') : t('Languages')}</dt>
                <dd className={styles.metaValue}>
                  {book.languages.map((l, i) => (
                    <span key={l.id}>
                      {i > 0 && ', '}
                      <Link href={`/languages/${l.id}`} className={styles.metaLink}>{l.name}</Link>
                    </span>
                  ))}
                </dd>
              </>
            )}
            {book.publishers.length > 0 && (
              <>
                <dt className={styles.metaLabel}>{book.publishers.length === 1 ? t('Publisher') : t('Publishers')}</dt>
                <dd className={styles.metaValue}>
                  {book.publishers.map((p, i) => (
                    <span key={p.id}>
                      {i > 0 && ', '}
                      <Link href={`/publishers/${p.id}`} className={styles.metaLink}>{p.name}</Link>
                    </span>
                  ))}
                </dd>
              </>
            )}
            {book.identifiers.map((id, i) => (
              <Fragment key={`id-${i}`}>
                <dt className={styles.metaLabel}>{id.label || id.type.toUpperCase()}</dt>
                <dd className={styles.metaValue}>
                  {id.url
                    ? <a href={id.url} target="_blank" rel="noopener noreferrer" className={styles.metaLink}>{id.val}</a>
                    : id.val}
                </dd>
              </Fragment>
            ))}
            {(book.custom_columns ?? []).map((column) => (
              <Fragment key={`custom-${column.id}`}>
                <dt className={styles.metaLabel}>{column.name}</dt>
                <dd className={styles.metaValue}>
                  {column.datatype === 'comments' && column.values[0]?.value_html ? (
                    <span
                      // value_html is sanitized by the API serializer.
                      // eslint-disable-next-line react/no-danger
                      dangerouslySetInnerHTML={{ __html: column.values[0].value_html }}
                    />
                  ) : column.values
                    .map((entry) => formatCustomValue(column, entry, t('Yes'), t('No')))
                    .filter(Boolean)
                    .join(', ')}
                </dd>
              </Fragment>
            ))}
          </dl>

          {/* Description */}
          {book.description_html && (
            <div
              className={styles.description}
              // description_html is sanitized server-side in serialize_book_detail
              // (cps/clean_html.clean_string — bleach/nh3 allowlist, same as the
              // legacy templates), so it is safe to render here.
              // eslint-disable-next-line react/no-danger
              dangerouslySetInnerHTML={{ __html: book.description_html }}
            />
          )}
        </div>
      </div>

      {/* More by this author — a full-width browse strip below the two-column
          layout. Fills the page for sparse/description-less books and turns the
          detail page into a browse surface. Keyed on the book so switching books
          refetches; renders nothing when the author has no other titles. */}
      {book.authors.length > 0 && (
        <MoreByAuthor
          key={book.id}
          authorId={book.authors[0].id}
          authorName={book.authors[0].name}
          excludeBookId={book.id}
        />
      )}
    </main>
  );
}
