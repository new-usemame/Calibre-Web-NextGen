import { useState, Fragment } from 'react';
import { Link, useParams } from 'wouter';
import { Download, Pencil, Star, Archive, EyeOff, Eye, Send, Highlighter, Image as ImageIcon, Plus, X, BookOpen } from 'lucide-react';
import {
  useBook, useToggleRead, useToggleFavorite, useToggleArchived, useToggleHidden,
  useSendToEreader, useMe, useUpdateMetadata,
} from '../lib/queries';
import { Pill } from '../components/Pill';
import { AddToShelf } from '../components/AddToShelf';
import { StarRating } from '../components/StarRating';
import { MoreByAuthor } from '../components/MoreByAuthor';
import { SpinnerCentered, Spinner } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { BookFormat, EntityRef } from '../lib/api';
import { ApiError, resourceUrl } from '../lib/api';
import { useT } from '../lib/i18n';
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

// Formats the in-browser reader can open. EPUB/KEPUB use the SPA's epub.js
// reader; the rest (PDF, comics, plain text, DjVu, audiobooks) open in the
// server's format-specific reader at read_url — so every readable format the
// library supports is reachable from the SPA, not just EPUB.
const SPA_READABLE = new Set(['epub', 'kepub']);
const LEGACY_READABLE = new Set([
  'pdf', 'txt', 'djvu', 'cbz', 'cbr', 'cbt', 'cb7',
  'mp3', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav',
]);

function isReadable(fmt: BookFormat): boolean {
  const f = fmt.format.toLowerCase();
  return SPA_READABLE.has(f) || LEGACY_READABLE.has(f);
}

interface SendPanelProps {
  formats: string[];
  pending: boolean;
  banner: { ok: boolean; text: string } | null;
  onSend: (format: string, convert: boolean, emails: string) => void;
}

/** Compact send-to-e-reader form: pick a format, optionally convert, optionally
 *  override the recipient(s). Empty recipient → user's configured e-reader email. */
function SendPanel({ formats, pending, banner, onSend }: SendPanelProps) {
  const t = useT();
  const [format, setFormat] = useState(formats[0] ?? '');
  const [convert, setConvert] = useState(false);
  const [emails, setEmails] = useState('');
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
          <span>{t('Recipient(s) — blank = your e-reader email')}</span>
          <input
            type="text" value={emails} placeholder="a@kindle.com, b@kindle.com"
            onChange={(e) => setEmails(e.target.value)}
          />
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
      <div className={styles.tags}>
        {tags.map((tag) => (
          <Link key={tag.id} href={`/tags/${tag.id}`} className={styles.tagLink}>
            <Pill>{tag.name}</Pill>
          </Link>
        ))}
      </div>
    );
  }

  return (
    <div className={styles.tags}>
      {tags.map((tag) => (
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
  const me = useMe().data;
  const [sendOpen, setSendOpen] = useState(false);
  const [sendBanner, setSendBanner] = useState<{ ok: boolean; text: string } | null>(null);

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !book) {
    return (
      <main className={styles.container}>
        <Link href="/" className={styles.back}>{t('← Library')}</Link>
        <EmptyState message={error instanceof Error ? error.message : t('Book not found.')} />
      </main>
    );
  }

  const readableFormats = book.formats.filter(isReadable);
  // Prefer EPUB (SPA reader); else the first server-readable format.
  const hasEpub = book.formats.some((f) => SPA_READABLE.has(f.format.toLowerCase()));
  const primaryReadable = readableFormats.find((f) => LEGACY_READABLE.has(f.format.toLowerCase())) ?? null;

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
                    {i > 0 && ', '}
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
                {' · Book '}
                {book.series_index}
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
              <p className={styles.currentlyReading}>
                <BookOpen size={14} aria-hidden="true" />
                {book.kosync_progress != null
                  ? `${t('Currently reading')} · ${Math.round(book.kosync_progress)}%`
                  : t('Currently reading')}
              </p>
            )}
          </div>

          {/* Actions */}
          <div className={styles.actions}>
            {hasEpub ? (
              // EPUB opens in the in-browser SPA reader (resumes saved progress).
              <Link href={`/read/${book.id}`} className={styles.actionPrimary}>
                {t('Read now')}
              </Link>
            ) : primaryReadable ? (
              // PDF/audio/text open in the native multi-format reader; comics/
              // DjVu fall through there to the server reader for image extraction.
              <Link href={`/view/${book.id}/${primaryReadable.format.toLowerCase()}`} className={styles.actionPrimary}>
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
              className={book.archived ? styles.readToggleActive : styles.readToggleGhost}
              onClick={() => toggleArchived.mutate()}
              disabled={toggleArchived.isPending}
              aria-label={book.archived ? t('Unarchive') : t('Archive')}
            >
              <Archive size={14} />
              {book.archived ? t('Archived') : t('Archive')}
            </button>

            {/* Hide / unhide — only shown when hiding is enabled, or to unhide an
                already-hidden book (so an admin disabling the flag can't strand it). */}
            {(me?.features?.hide_books || book.hidden) && (
              <button
                className={book.hidden ? styles.readToggleActive : styles.readToggleGhost}
                onClick={() => toggleHidden.mutate()}
                disabled={toggleHidden.isPending}
                aria-label={book.hidden ? t('Unhide') : t('Hide')}
              >
                {book.hidden ? <Eye size={14} /> : <EyeOff size={14} />}
                {book.hidden ? t('Unhide') : t('Hide')}
              </button>
            )}

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
              <Link href={`/book/${book.id}/edit`} className={styles.downloadBtn}>
                <Pencil size={14} />
                {t('Edit')}
              </Link>
            )}

            {/* Highlights/annotations — view + export + import (Kobo). Opens the
                server annotations page; in-reader highlight creation is the
                flagship reader phase-2 (tracked separately). */}
            <Link href={`/book/${book.id}/annotations`} className={styles.downloadBtn}>
              <Highlighter size={14} />
              {t('Highlights')}
            </Link>
          </div>

          {/* Send-to-e-reader panel */}
          {sendOpen && (
            <SendPanel
              formats={book.formats.map((f) => f.format)}
              pending={sendToEreader.isPending}
              banner={sendBanner}
              onSend={(format, convert, emails) => {
                setSendBanner(null);
                sendToEreader.mutate(
                  { format, convert, emails: emails || undefined },
                  {
                    onSuccess: (r) => { setSendBanner({ ok: true, text: r.message }); },
                    onError: (err) =>
                      setSendBanner({ ok: false, text: err instanceof ApiError ? err.message : 'Send failed.' }),
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
