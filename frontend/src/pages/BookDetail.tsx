import { useState, useEffect, useRef, Fragment } from 'react';
import { Link, useParams, useLocation } from 'wouter';
import { Download, Pencil, Star, Archive, EyeOff, Eye, Send, Highlighter, Image as ImageIcon, Plus, X, BookOpen, BookCheck, BookPlus, Trash2, RefreshCw, TabletSmartphone, Settings, Upload as UploadIcon } from 'lucide-react';
import {
  useBook, useToggleRead, useToggleFavorite, useToggleArchived, useToggleHidden,
  useSendToEreader, useMe, useAccount, useUpdateMetadata, useDeleteBook, useReloadMetadata,
  useBookShelves, useShelves, useKoboTwoWayAnnotations, selectKoboTwoWayBook,
  useAddToMyLibrary, useMyLibraryRemovalImpact, useRemoveFromMyLibrary,
  useActiveDeliveryDevices, useQueueDeviceDelivery,
  useDeleteFormat, useConvertFormat, useAddFormat,
} from '../lib/queries';
import { authorityLabel, opaqueLabel } from '../lib/koboTwoWay';
import { MetadataTypeahead } from '../components/MetadataTypeahead';
import { Pill } from '../components/Pill';
import { AddToShelf } from '../components/AddToShelf';
import { Menu, type MenuSectionDef } from '../components/Menu';
import { StarRating } from '../components/StarRating';
import { MoreByAuthor } from '../components/MoreByAuthor';
import { AUTHOR_SEPARATOR } from '../lib/authors';
import { SpinnerCentered, Spinner } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { CustomColumn, CustomColumnValue, EntityRef, DeliveryDevice } from '../lib/api';
import { ApiError, resourceUrl, resourceSrcSet } from '../lib/api';
import { useT } from '../lib/i18n';
import { getPrimaryReadTarget } from '../lib/readerTarget';
import { canDeleteBooks, canDownloadBooks, canReadBooks, canUploadBooks } from '../lib/permissions';
import styles from './BookDetail.module.css';
import { useCardActionsHidden } from '../lib/useCardActionsHidden';
import { BookUserNotices } from '../components/UserNotices';
import { backTarget } from '../lib/backLink';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useMediaQuery } from '../lib/useMediaQuery';

/* `fetchpriority` is a plain DOM attribute. react-dom 18.3 has no knowledge of
   it, so the camelCase `fetchPriority` that @types/react declares would trip its
   "React does not recognize the prop" development warning — it still renders,
   because setAttribute lowercases the name, but the warning is noise. Spelling
   it the way the DOM does avoids that. It is declared with its own type rather
   than asserted to be ImgHTMLAttributes, because it demonstrably is NOT one of
   those attributes in these typings — that is the whole reason this exists.
   Spreading a typed variable (not an inline literal) is what keeps TS happy.
   Drop the indirection on the React 19 upgrade, which knows the attribute. */
type LowercaseFetchPriority = { fetchpriority: 'high' | 'low' | 'auto' };
const COVER_PRIORITY: LowercaseFetchPriority = { fetchpriority: 'high' };
const BOOK_DETAIL_NARROW_QUERY = '(max-width: 700px)';

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

interface DeviceSendPanelProps {
  devices: DeliveryDevice[];
  pending: boolean;
  banner: { ok: boolean; text: string } | null;
  onSend: (device: string) => void;
}

/** Pull delivery is intentionally separate from e-mail sending: the selected
 * reader collects this queue on its own next sync, even through a reverse proxy. */
function DeviceSendPanel({ devices, pending, banner, onSend }: DeviceSendPanelProps) {
  const t = useT();
  const [device, setDevice] = useState(devices[0]?.public_id ?? '');
  const hintId = 'device-delivery-hint';
  useEffect(() => {
    if (!devices.some((item) => item.public_id === device)) {
      setDevice(devices[0]?.public_id ?? '');
    }
  }, [devices, device]);
  return (
    <div id="device-send-panel" className={styles.sendPanel} data-testid="device-send-panel">
      <label className={styles.sendField}>
        <span>{t('Device')}</span>
        <select value={device} aria-describedby={hintId}
          onChange={(event) => setDevice(event.target.value)}>
          <option value="" disabled>{t('Choose a device')}</option>
          {devices.map((item) => (
            <option key={item.public_id} value={item.public_id}>{item.label}</option>
          ))}
        </select>
      </label>
      <p id={hintId} className={styles.sendHint}>{t("Collects on the device's next sync.")}</p>
      <div className={styles.sendActions}>
        <button type="button" className={`${styles.actionPrimary} ${styles.deviceSendButton}`}
          disabled={pending || !device} onClick={() => onSend(device)}>
          {pending ? t('Queueing…') : t('Send to device')}
        </button>
      </div>
      <p className={banner ? (banner.ok ? styles.sendOk : styles.sendErr) : undefined}
        role="status" aria-live="polite">{banner?.text}</p>
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
  const narrowLayout = useMediaQuery(BOOK_DETAIL_NARROW_QUERY);
  const collapsedTagLimit = narrowLayout ? 8 : 20;
  const hasMore = tags.length - collapsedTagLimit >= 3;
  const visibleTags = expanded || !hasMore ? tags : tags.slice(0, collapsedTagLimit);

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
          {/* Suggests tags the library already has, so quick-add stops minting
              near-duplicates from typos (#741, #572). The full editor got this
              in #741; this field was left on a bare input, which is what both
              reporters came back about. Enter/Escape stay ours — the combobox
              hands on any key it did not consume, so arrow-key navigation
              through the suggestions still works. */}
          <MetadataTypeahead
            field="tags"
            multi={false}
            value={input}
            onChange={setInput}
            excludeValues={names}
            inputClassName={styles.tagAddInput}
            placeholder={t('New tag')}
            aria-label={t('Add tag')}
            autoFocus
            disabled={update.isPending}
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
  const [cardActionsHidden] = useCardActionsHidden();
  const t = useT();
  const announce = useAnnouncer();
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data: book, isLoading, error } = useBook(id);
  const me = useMe().data;
  const selectionMode = me?.library_mode === 'personal_library';
  const inLibrary = !!book && (!selectionMode || book.in_my_library !== false);
  const toggleRead = useToggleRead(id);
  const toggleFavorite = useToggleFavorite(id);
  const toggleArchived = useToggleArchived(id);
  const toggleHidden = useToggleHidden(id);
  const sendToEreader = useSendToEreader(id);
  const queueDeviceDelivery = useQueueDeviceDelivery(id);
  const deleteBook = useDeleteBook(id);
  const reloadMetadata = useReloadMetadata(id);
  const addToLibrary = useAddToMyLibrary();
  const removalImpact = useMyLibraryRemovalImpact();
  const removeFromLibrary = useRemoveFromMyLibrary();
  const [location, navigate] = useLocation();
  const deliveryDevices = useActiveDeliveryDevices(
    inLibrary && !!me && !me.role?.anonymous && !!me.role?.download,
  );
  /* Stage 0 two-way sync state chip (read-only; manage it on Account). */
  const twoWay = useKoboTwoWayAnnotations({
    enabled: inLibrary && !!me && !me.role?.anonymous && !!me.features?.kobo_two_way_annotations,
  });
  const bookBackTarget = backTarget(location);
  // The send-to-e-reader button only renders when mail is configured + the user
  // can download, so defer the account fetch (which carries the saved e-reader
  // address used to prefill the recipient field, #715) until that's possible.
  const canSend = inLibrary && !!me?.features?.mail_configured && !!me?.role?.download;
  const savedEreader = useAccount({ enabled: canSend }).data?.kindle_mail ?? '';
  const [sendOpen, setSendOpen] = useState(false);
  const [sendBanner, setSendBanner] = useState<{ ok: boolean; text: string } | null>(null);
  const [deviceSendOpen, setDeviceSendOpen] = useState(false);
  const [deviceSendBanner, setDeviceSendBanner] = useState<{ ok: boolean; text: string } | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [reloadMessage, setReloadMessage] = useState('');
  // Shelf membership for the metadata list (#1254). Both queries are already
  // in flight for the always-rendered AddToShelf popover below and share its
  // cache keys, so reading them here costs no extra request.
  const shelfMembership = useBookShelves(id, { enabled: inLibrary }).data;
  const visibleShelves = useShelves({ enabled: inLibrary }).data;

  if (isLoading) return <SpinnerCentered size={40} />;
  if (error || !book) {
    return (
      <main className={styles.container}>
        <Link href={bookBackTarget.href} className={styles.back}>
          {bookBackTarget.isOrigin ? t('← Back') : t('← Library')}
        </Link>
        <EmptyState message={error instanceof Error ? error.message : t('Book not found.')} />
      </main>
    );
  }

  const primaryReadTarget = getPrimaryReadTarget(
    book.id,
    book.formats.map((f) => f.format),
    canReadBooks(me),
  );
  const canDelete = canDeleteBooks(me);

  // The membership endpoint returns ids only, and both it and the shelf list
  // apply the same server-side visibility filter (own shelves + public ones),
  // so every id here resolves to a name the caller is allowed to see.
  const onShelfIds = new Set(shelfMembership?.shelf_ids ?? []);
  const bookShelves = (visibleShelves?.items ?? []).filter((s) => onShelfIds.has(s.id));

  const requestDeleteBook = () => {
    if (deleteBook.isPending) return;
    if (!window.confirm(
      t('Delete "{title}" from the global library? The book and all its files are permanently erased for every member. This cannot be undone.', { title: book.title })
    )) return;
    setDeleteError(null);
    deleteBook.mutate(undefined, {
      onSuccess: (result) => {
        if (result?.warning) window.alert(result.warning.message);
        navigate('/');
      },
      onError: (err) =>
        setDeleteError(err instanceof ApiError ? err.message : t('Could not delete this book.')),
    });
  };

  const removeMembership = () => {
    removalImpact.mutate(book.id, {
      onSuccess: (impact) => {
        const lines = [t('Remove "{title}" from your library?', { title: book.title }), '',
          t('It leaves your library and your OPDS feed.'),
          t("If you use Kobo's built-in sync, it also leaves your Kobo at its next sync. Other e-readers keep downloaded copies, and KOReader progress sync keeps working.")];
        if (impact.affected_shelves.length) lines.push(t('It also leaves these shelves: {shelves}.', { shelves: impact.affected_shelves.join(', ') }));
        lines.push(t('Nothing is deleted: the book stays in the global library, and your highlights, notes and reading progress are kept.'));
        lines.push(me?.role?.browse_global ? t('You can add it back any time from the global library.') : t('Only an administrator can add it back.'));
        if (!window.confirm(lines.join('\n'))) return;
        removeFromLibrary.mutate(book.id, {
          onSuccess: () => announce(t('Removed from your library')),
          onError: () => announce(t('Could not remove the book. Please try again.'), { assertive: true }),
        });
      },
      onError: () => announce(t('Could not remove the book. Please try again.'), { assertive: true }),
    });
  };

  const requestReloadMetadata = () => {
    if (reloadMetadata.isPending) return;
    // Destructive: reload overwrites whatever the user curated here with what
    // the file on disk says, and there is no undo. Every other destructive
    // action in the SPA confirms first (#1496, @JamesHACS).
    if (!window.confirm(
      t('Reload metadata for "{title}" from the file on disk? Any title, author or series you edited here is replaced by what the file contains. This cannot be undone.', { title: book.title })
    )) return;
    setReloadMessage('');
    reloadMetadata.mutate(undefined, {
      onSuccess: (result) => setReloadMessage(result.message),
      onError: (err) => setReloadMessage(err instanceof ApiError ? err.message : t('Could not reload metadata')),
    });
  };

  /* The "More actions" gear menu — every book action that is not one of the
     four visible controls (Read now, Favorite, Add to shelf, the gear itself).
     Labels name the ACTION performed (state-aware), per the cleanup brief:
     today's "In your library" state chip becomes "Remove from library". */
  const menuItems: MenuSectionDef['items'] = [];
  if (inLibrary) {
    menuItems.push({
      id: 'read-toggle',
      label: book.read ? t('Mark as unread') : t('Mark as read'),
      icon: book.read ? <BookOpen size={15} /> : <BookCheck size={15} />,
      disabled: toggleRead.isPending,
      onSelect: () => toggleRead.mutate(!book.read),
      testId: 'menu-read-toggle',
    });
    menuItems.push({
      id: 'archive-toggle',
      label: book.archived ? t('Unarchive') : t('Archive'),
      icon: <Archive size={15} />,
      disabled: toggleArchived.isPending,
      onSelect: () => toggleArchived.mutate(),
      testId: 'archive-book-toggle',
    });
    /* Personal action, deliberately outside the delete-role gate. Guest sessions
       cannot own hidden state, so never offer them a control that returns 401. */
    if (!me?.role?.anonymous && (me?.features?.hide_books || book.hidden)) {
      menuItems.push({
        id: 'hide-toggle',
        label: book.hidden ? t('Unhide') : t('Hide'),
        icon: book.hidden ? <Eye size={15} /> : <EyeOff size={15} />,
        disabled: toggleHidden.isPending,
        onSelect: () => toggleHidden.mutate(!book.hidden),
        testId: 'hide-book-toggle',
      });
    }
  }
  if (canSend && book.formats.length > 0) {
    menuItems.push({
      id: 'send-ereader',
      label: t('Send to e-reader'),
      icon: <Send size={15} />,
      onSelect: () => { setSendOpen(true); setSendBanner(null); },
    });
  }
  if (inLibrary && me?.role?.download && book.formats.length > 0 && (deliveryDevices.data?.devices.length ?? 0) > 0) {
    menuItems.push({
      id: 'send-device',
      label: t('Send to device'),
      icon: <TabletSmartphone size={15} />,
      onSelect: () => { setDeviceSendOpen(true); setDeviceSendBanner(null); },
    });
  }
  if (me?.role?.edit) {
    menuItems.push({
      id: 'reload-metadata',
      label: reloadMetadata.isPending ? t('Reloading…') : t('Reload metadata from disk'),
      icon: <RefreshCw size={15} />,
      disabled: reloadMetadata.isPending,
      onSelect: requestReloadMetadata,
    });
  }
  if (selectionMode) {
    if (inLibrary) {
      menuItems.push({
        id: 'remove-from-library',
        label: t('Remove from library'),
        icon: <BookCheck size={15} />,
        disabled: removalImpact.isPending || removeFromLibrary.isPending,
        onSelect: removeMembership,
        testId: 'menu-remove-from-library',
      });
    } else if (me?.role?.browse_global) {
      menuItems.push({
        id: 'add-to-library',
        label: t('Add to library'),
        icon: <BookPlus size={15} />,
        disabled: addToLibrary.isPending,
        onSelect: () => addToLibrary.mutate(book.id, {
          onSuccess: () => announce(t('Added to your library')),
          onError: () => announce(t('Could not add the book. Please try again.'), { assertive: true }),
        }),
        testId: 'menu-add-to-library',
      });
    } else {
      // Not toggleable for this user: show the current state, disabled.
      menuItems.push({ id: 'not-in-library', label: t('Not in your library'), icon: <BookPlus size={15} />, disabled: true });
    }
  }
  if (inLibrary) {
    const annotationCount = book.annotation_count ?? 0;
    menuItems.push({
      id: 'highlights',
      label: t('View highlights'),
      icon: <Highlighter size={15} />,
      to: `/book/${book.id}/annotations`,
      ariaLabel: annotationCount > 0
        ? t('View highlights, {count} saved annotations', { count: annotationCount })
        : undefined,
      trailing: annotationCount > 0 ? (
        <span className={styles.highlightCount} data-testid="highlight-count" aria-hidden="true">
          {annotationCount}
        </span>
      ) : undefined,
    });
  }
  if (me?.role?.edit) {
    menuItems.push({
      id: 'edit-metadata',
      label: t('Edit metadata'),
      icon: <Pencil size={15} />,
      to: `/book/${book.id}/edit`,
    });
  }
  if (!me?.role?.anonymous) {
    menuItems.push({
      id: 'edit-cover',
      label: t('Edit cover…'),
      icon: <ImageIcon size={15} />,
      to: `/book/${book.id}/cover`,
    });
  }
  const menuSections: MenuSectionDef[] = [{ id: 'actions', items: menuItems }];
  if (canDelete) {
    menuSections.push({
      id: 'destructive',
      label: t('Admin only'),
      danger: true,
      items: [{
        id: 'delete-book',
        label: deleteBook.isPending ? t('Deleting…') : t('Delete from the global library'),
        icon: <Trash2 size={15} />,
        danger: true,
        disabled: deleteBook.isPending,
        onSelect: requestDeleteBook,
        testId: 'menu-delete-book',
      }],
    });
  }

  return (
    <main className={styles.container}>
      <Link href={bookBackTarget.href} className={styles.back}>
        {bookBackTarget.isOrigin ? t('← Back') : t('← Library')}
      </Link>

      <BookUserNotices bookId={book.id} />

      <div className={styles.layout}>
        {/* LEFT: cover */}
        <div className={styles.coverCol}>
          <div className={styles.coverWrap}>
            {/* This cover is the page's largest paint, and the SPA builds it in
                JS — the preload scanner never sees it, so it starts at the
                browser's default (low) image priority. COVER_PRIORITY lifts it.
                `decoding="async"` is a HINT, not a guarantee: it allows the
                browser to present the rest of the page before this image's
                decode completes — it does not promise a particular thread, and
                the browser may also choose to present the image later. The srcset offers sm at 1x and md at 2x —
                the resolution constants ARE density multipliers (1/2/4) and the
                column is a fixed 280px, so md suits 2x while lg would usually be
                the unresized original again (its 4x target height exceeds most
                covers, and the thumbnailer only downscales). */}
            {book.cover_url ? (
              <img
                src={resourceUrl(book.cover_url)}
                srcSet={book.cover_srcset ? resourceSrcSet(book.cover_srcset) : undefined}
                alt={book.title}
                className={styles.cover}
                decoding="async"
                {...COVER_PRIORITY}
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
            {/* "Edit cover" pill overlaid on the artwork — opens the cover
                editor, where both the library cover and the reader's own
                (private) cover are managed. Always visible on touch/coarse
                pointers; hover/focus-revealed on fine pointers (see the CSS).
                Guests get no control: the editor's sources answer 403/401 for
                them, so the affordance would be a dead end. */}
            {!me?.role?.anonymous && (
              <Link href={`/book/${book.id}/cover`} className={styles.changeCover}
                data-testid="edit-cover-pill">
                <ImageIcon size={14} aria-hidden="true" focusable={false} />
                {t('Edit cover')}
              </Link>
            )}
          </div>
        </div>

        {/* RIGHT: info */}
        <div className={styles.infoCol}>
          <div>
            {/* dir="auto" per field (#1073): direction follows each string's own
                first strong character, so a Hebrew title and a Latin series name
                on the same page each render correctly. */}
            <h1 className={styles.title} dir="auto">{book.title}</h1>
            {book.authors.length > 0 && (
              <p className={styles.authors} dir="auto">
                {book.authors.map((a, i) => (
                  <span key={a.id}>
                    {i > 0 && AUTHOR_SEPARATOR}
                    <Link href={`/authors/${a.id}`} className={styles.metaLink}>{a.name}</Link>
                  </span>
                ))}
              </p>
            )}
            {book.series && (
              <p className={styles.series} dir="auto">
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
            {inLibrary && book.in_progress && (
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

          {/* Description — in the DOM directly under the title/author header,
              because on a phone that is where it belongs (#1828): the thing the
              page is about comes before the controls and the attribute list.
              Desktop keeps its long-standing visual order (actions first,
              description last) via `order` in the stylesheet, so this reorder
              changes nothing there. */}
          {book.description_html && (
            <div
              className={styles.description}
              dir="auto"
              // description_html is sanitized server-side in serialize_book_detail
              // (cps/clean_html.clean_string — bleach/nh3 allowlist, same as the
              // legacy templates), so it is safe to render here.
              // eslint-disable-next-line react/no-danger
              dangerouslySetInnerHTML={{ __html: book.description_html }}
            />
          )}

          {/* Actions — the four visible controls. Everything else lives in the
              gear menu (built above) or in the Files section at the page foot. */}
          <div className={styles.actions} data-testid="book-actions">
            {inLibrary && primaryReadTarget ? (
              <Link href={primaryReadTarget} className={styles.actionPrimary}>
                {t('Read now')}
              </Link>
            ) : null}

            {/* Star / favorite */}
            {inLibrary && <button
              className={book.favorited ? styles.readToggleActive : styles.readToggleGhost}
              onClick={() => toggleFavorite.mutate()}
              disabled={toggleFavorite.isPending}
              aria-label={book.favorited ? t('Remove from favorites') : t('Add to favorites')}
            >
              <Star size={14} fill={book.favorited ? 'currentColor' : 'none'} />
              {book.favorited ? t('Favorited') : t('Favorite')}
            </button>}

            {inLibrary && (
              <AddToShelf bookId={book.id} inLibrary={inLibrary} />
            )}

            {menuItems.length > 0 && (
              <Menu
                label={t('More actions')}
                title={t('More actions')}
                icon={<Settings size={17} aria-hidden="true" focusable={false} />}
                sections={menuSections}
                triggerTestId="book-actions-menu"
                menuTestId="book-actions-menu-list"
              />
            )}
          </div>
          <p className={reloadMessage ? styles.actionStatus : undefined} role="status">{reloadMessage}</p>

          {/* Rendered outside the region so the error still surfaces on mobile,
              where the region itself is hidden (#1828). */}
          {deleteError && <p className={styles.deleteErr} role="alert">{deleteError}</p>}

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


          {deviceSendOpen && (
            <DeviceSendPanel
              devices={deliveryDevices.data?.devices ?? []}
              pending={queueDeviceDelivery.isPending}
              banner={deviceSendBanner}
              onSend={(device) => {
                setDeviceSendBanner(null);
                queueDeviceDelivery.mutate(device, {
                  onSuccess: (result) => setDeviceSendBanner({ ok: true, text: result.message }),
                  onError: (err) => setDeviceSendBanner({
                    ok: false,
                    text: err instanceof ApiError
                      ? err.message : t('Could not queue this book for the device.'),
                  }),
                });
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
            {inLibrary && book.kosync_progress != null && (
              <>
                <dt className={styles.metaLabel}>{t('KOReader Progress')}</dt>
                <dd className={styles.metaValue}>{book.kosync_progress.toFixed(1)}%</dd>
              </>
            )}
            {inLibrary && book.kosync_progress_created_at !== null && (
              <>
                <dt className={styles.metaLabel} title={t('When reading progress was first synced')}>
                  {t('Started reading')}
                </dt>
                <dd className={styles.metaValue}>{formatDate(book.kosync_progress_created_at, true)}</dd>
              </>
            )}
            {inLibrary && book.kosync_progress_timestamp !== null && (
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
            {inLibrary && bookShelves.length > 0 && (
              <>
                {/* Always the plural msgid: "Shelf" is translated in no locale
                    today, so a count-switched label would render English for a
                    single shelf everywhere. */}
                <dt className={styles.metaLabel}>{t('Shelves')}</dt>
                <dd className={styles.metaValue} data-testid="book-shelves">
                  {bookShelves.map((s, i) => (
                    <span key={s.id}>
                      {i > 0 && ', '}
                      <Link href={`/shelf/${s.id}`} className={styles.metaLink}>{s.name}</Link>
                    </span>
                  ))}
                </dd>
              </>
            )}
            {/* Stage 0 per-book two-way state, when the user opted in and the
                book has pipeline state. Read-only status line (was an action-row
                chip); manage it on Account. */}
            {(() => {
              const twoWayBook = selectKoboTwoWayBook(twoWay.data, book.id);
              if (!inLibrary || !twoWay.data?.enabled || !twoWayBook) return null;
              return (
                <>
                  <dt className={styles.metaLabel}>{t('Kobo two-way sync')}</dt>
                  <dd className={styles.metaValue}>
                    <Link href="/account" className={styles.metaLink}>
                      {authorityLabel(t, twoWayBook, twoWay.data.scope)}
                    </Link>
                    {opaqueLabel(t, twoWayBook) && (
                      <span className={styles.twoWayBlocked}>{opaqueLabel(t, twoWayBook)}</span>
                    )}
                  </dd>
                </>
              );
            })()}
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
                <dd className={styles.metaValue} dir="auto">
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
        </div>
      </div>

      {/* More by this author — a full-width browse strip below the two-column
          layout. Fills the page for sparse/description-less books and turns the
          detail page into a browse surface. Keyed on the book so switching books
          refetches; renders nothing when the author has no other titles. */}
      {book.authors.length > 0 && (
        <MoreByAuthor
          hideActions={cardActionsHidden}
          canRead={canReadBooks(me)}
          key={book.id}
          authorId={book.authors[0].id}
          authorName={book.authors[0].name}
          excludeBookId={book.id}
        />
      )}

      {/* Files — the per-format downloads (out of the action row) plus the
          delete/convert/add-format controls that used to sit at the foot of
          the edit-metadata page. Last on the page by design. */}
      <FilesSection id={id} />
    </main>
  );
}

/** The book's files: one row per format with Download (download role) and
 *  Delete (delete+edit roles), the Convert from/to control (edit role — the
 *  endpoint is _require_edit), and "Add a format" (upload role + the
 *  instance's upload switch). Moved here from Edit metadata. */
function FilesSection({ id }: { id: string }) {
  const t = useT();
  const { data: book } = useBook(id);
  const me = useMe().data;
  const deleteFormat = useDeleteFormat(id);
  const convertFormat = useConvertFormat(id);
  const addFormat = useAddFormat(id);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const formats = book?.formats.map((f) => f.format) ?? [];
  const convertOptions = book?.convert_options;
  // Sources/targets come from the server as lowercase; display them uppercased
  // to match the format list.
  const sources = (convertOptions?.sources.length ? convertOptions.sources : formats.map((f) => f.toLowerCase()));
  const targets = convertOptions?.targets ?? [];
  if (!book || book.formats.length === 0) return null;
  const canDownload = canDownloadBooks(me);
  const canDelete = canDeleteBooks(me);
  // #1288: "Add a format" POSTs to /api/v1/books/<id>/formats, which requires
  // role_upload and honours the admin's "Enable Uploads" switch.
  const canUpload = canUploadBooks(me);
  const canEdit = !!me?.role?.edit;
  if (!canDownload && !canDelete && !canUpload && !canEdit) return null;

  // Keep the selected source/target normalized to lowercase option values.
  const selectedFrom = (from || sources[0] || '').toLowerCase();
  const availableTargets = targets.filter((target) => target.toLowerCase() !== selectedFrom);
  const selectedTo = (to || '').toLowerCase();

  const onAddFormat = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMsg(null);
    addFormat.mutate(file, {
      onSuccess: () => setMsg({ ok: true, text: t('Format queued — it will appear once processed.') }),
      onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Upload failed.') }),
    });
    e.target.value = '';
  };

  const onConvert = (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    const src = selectedFrom;
    const dst = selectedTo;
    if (!src || !dst || src === dst) return;
    convertFormat.mutate(
      { from: src.toUpperCase(), to: dst.toUpperCase() },
      {
        onSuccess: (r) => { setMsg({ ok: true, text: r.message }); setTo(''); },
        onError: (err) => setMsg({ ok: false, text: err instanceof ApiError ? err.message : t('Convert failed.') }),
      },
    );
  };

  return (
    <section className={styles.filesSection} data-testid="book-files" aria-labelledby="book-files-heading">
      <h2 className={styles.filesTitle} id="book-files-heading">{t('Files')}</h2>
      <ul className={styles.fileList}>
        {book.formats.map((f) => (
          <li key={f.format} className={styles.fileItem}>
            <span className={styles.fileName}>{f.format.toUpperCase()}</span>
            <span className={styles.fileSize}>{formatBytes(f.size_bytes)}</span>
            {canDownload && (
              // download_url hits cps/helper.py get_download_link, which sets
              // `attachment`. `target` is inert with a present `download`
              // attribute (the UA downloads without consulting it); it stays
              // only because removing it needs a real standalone-iOS run to
              // verify (#716).
              <a className={styles.fileAction} href={resourceUrl(f.download_url)}
                download target="_blank" rel="noopener">
                <Download size={14} aria-hidden="true" focusable={false} />
                {t('Download')}
              </a>
            )}
            {canDelete && (
              <button type="button" className={`${styles.fileAction} ${styles.fileDelete}`}
                onClick={() => {
                  if (window.confirm(t('Delete the {fmt} file? The book record, metadata, shelves, and reading state stay available.', { fmt: f.format }))) {
                    setMsg(null);
                    deleteFormat.mutate(f.format, {
                      onSuccess: (result) => setMsg(result?.warning
                        ? { ok: false, text: result.warning.message }
                        : { ok: true, text: t('Format deleted.') }),
                      onError: (err) => setMsg({
                        ok: false,
                        text: err instanceof ApiError ? err.message : t('Could not delete this format.'),
                      }),
                    });
                  }
                }}
                disabled={deleteFormat.isPending}
                aria-label={t('Delete {fmt}', { fmt: f.format })}>
                <Trash2 size={14} aria-hidden="true" focusable={false} />
                {t('Delete')}
              </button>
            )}
          </li>
        ))}
      </ul>
      {canDelete && (
        <p className={styles.fileNote}>
          {t('The book record, metadata, shelves, and reading state stay available. If this is the last format, you can add a replacement later.')}
        </p>
      )}

      {canEdit && sources.length > 0 && availableTargets.length > 0 && (
        <form className={styles.convertForm} onSubmit={onConvert}>
          <label className={styles.fileField}>
            <span className={styles.fileFieldLabel}>{t('Convert from')}</span>
            <select className={styles.fileSelect} value={selectedFrom} onChange={(e) => setFrom(e.target.value)}>
              {sources.map((f) => <option key={f} value={f.toLowerCase()}>{f.toUpperCase()}</option>)}
            </select>
          </label>
          <span className={styles.convertToLabel} aria-hidden="true">{t('to')}</span>
          <label className={styles.fileField}>
            <span className={styles.fileFieldLabel}>{t('Convert to')}</span>
            <select className={styles.fileSelect} value={selectedTo} onChange={(e) => setTo(e.target.value)}
              aria-label={t('Convert to format')}>
              <option value="" disabled>{t('Select format')}</option>
              {availableTargets.map((f) => <option key={f} value={f.toLowerCase()}>{f.toUpperCase()}</option>)}
            </select>
          </label>
          <button type="submit" className={styles.fileAction}
            disabled={convertFormat.isPending || !selectedTo}>
            <RefreshCw size={14} aria-hidden="true" focusable={false} /> {t('Convert')}
          </button>
        </form>
      )}

      {canUpload && (
        <label className={styles.fileAddBtn}>
          <UploadIcon size={15} aria-hidden="true" focusable={false} />
          {addFormat.isPending ? t('Uploading…') : t('Add a format')}
          {/* C3: sr-only (NOT hidden) keeps the input focusable + in tab order;
              the label shows a focus ring via :focus-within. */}
          <input type="file" className={styles.fileInput} onChange={onAddFormat} disabled={addFormat.isPending} />
        </label>
      )}
      <span className={msg ? (msg.ok ? styles.fileMsgOk : styles.fileMsgErr) : undefined} role="status">{msg?.text}</span>
    </section>
  );
}
