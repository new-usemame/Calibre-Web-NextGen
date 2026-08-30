import { useState, useRef, useEffect } from 'react';
import { Check, X, BookCopy, BookMinus, Trash2, CheckCheck, Pencil, Combine } from 'lucide-react';
import { useBulkActions, useShelves, useMe, useMergeBooks } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { Spinner } from './Spinner';
import { ApiError, type MetadataListMode, type MetadataUpdate } from '../lib/api';
import styles from './BulkBar.module.css';

interface BulkBarProps {
  ids: number[];
  personalLibrary: boolean;
  onClear: () => void;
  /** Called after a mutation that changes what the catalog should show
   *  (read state / membership / deletion), so the grid can refresh. */
  onChanged?: () => void;
}

export function BulkSelectionBar({ count, onClear, children, sticky = false }: {
  count: number; onClear: () => void; children: React.ReactNode; sticky?: boolean;
}) {
  const t = useT();
  const barRef = useRef<HTMLDivElement>(null);

  /* #1756 — the floating variant is a fixed overlay, so the page's last row
     can never scroll out from under it unless the document gains bottom room.
     The bar publishes its own rendered height as --bulk-bar-h on <html>; the
     catalog's scroll padding keys off that variable, so the clearance tracks
     the bar's ACTUAL wrapped height (two rows of icon buttons on a narrow
     phone, one on desktop) instead of a guessed constant. Only the floating
     variant measures — the sticky one is in flow and overlays nothing. The
     metadata panel is deliberately NOT measured: it is a transient overlay
     with its own scroll, and padding the page out behind it would be absurd. */
  useEffect(() => {
    if (sticky) return;
    const el = barRef.current;
    if (!el) return;
    const root = document.documentElement;
    const publish = () => {
      root.style.setProperty('--bulk-bar-h', `${Math.ceil(el.getBoundingClientRect().height)}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(el);
    return () => {
      observer.disconnect();
      root.style.removeProperty('--bulk-bar-h');
    };
  }, [sticky]);

  return (
    <div ref={barRef} className={`${styles.bar} ${sticky ? styles.sticky : ''}`} role="region"
      aria-label={t('{n} selected', { n: count })}>
      <span className={styles.count}>{t('{n} selected', { n: count })}</span>
      <div className={styles.actions}>{children}</div>
      <button className={styles.clear} onClick={onClear} aria-label={t('Clear selection')}>
        <X size={18} aria-hidden="true" focusable={false} />
      </button>
    </div>
  );
}

/** Floating action bar for the catalog's multi-select mode. Uses per-book
 *  accounting whether the server receives individual requests or bounded
 *  membership batches. */
export function BulkBar({ ids, personalLibrary, onClear, onChanged }: BulkBarProps) {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const { markRead, addToShelf, deleteBooks, removeFromMyLibrary, setMetadata } = useBulkActions();
  const mergeBooks = useMergeBooks();
  const { data: shelvesData } = useShelves();
  const [shelfOpen, setShelfOpen] = useState(false);
  const shelfRef = useRef<HTMLDivElement>(null);
  const [metaOpen, setMetaOpen] = useState(false);
  const [listMode, setListMode] = useState<MetadataListMode>('add');
  const [meta, setMeta] = useState({ tags: '', series: '', publishers: '', languages: '', authors: '' });

  useEffect(() => {
    if (!shelfOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (shelfRef.current && !shelfRef.current.contains(e.target as Node)) setShelfOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShelfOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [shelfOpen]);

  const canDelete = !!me?.role?.delete_books && !!me?.role?.edit;
  const canEditPublic = !!me?.role?.edit_shelfs;
  const editableShelves = (shelvesData?.items ?? []).filter(
    (s) => s.is_owner || (s.is_public && canEditPublic),
  );
  const busy = markRead.isPending || addToShelf.isPending || deleteBooks.isPending
    || removeFromMyLibrary.isPending
    || setMetadata.isPending || mergeBooks.isPending;
  const count = ids.length;

  const onMerge = () => {
    if (count < 2) return;
    if (!window.confirm(t('Merge {n} books into the first selected? The others are removed after their formats are copied over.', { n: count }))) return;
    mergeBooks.mutate(ids, { onSuccess: () => { onChanged?.(); onClear(); } });
  };

  const onDelete = () => {
    if (!window.confirm(t('Permanently delete {n} selected book(s) from the global library for every user? The books and all their files will be erased from the server. This cannot be undone.', { n: count }))) return;
    deleteBooks.mutate(ids, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        announce(failed
          ? t('{succeeded} book(s) permanently deleted from the global library for every user; {failed} failed.', { succeeded, failed })
          : t('{n} book(s) permanently deleted from the global library for every user.', { n: succeeded }), { assertive: failed > 0 });
        if (succeeded) onChanged?.();
        if (!failed) onClear();
      },
    });
  };

  const onRemoveFromMyLibrary = () => {
    if (!window.confirm(t("Remove {n} selected book(s) from your library? They leave your library and your OPDS feed. They are also removed from any regular shelves you added them to. Nothing is deleted from the global library. Highlights, notes, bookmarks, and reading progress are kept. If you use Kobo's built-in sync, the books also leave your Kobo at its next sync; other e-readers keep downloaded copies.", { n: count }))) return;
    removeFromMyLibrary.mutate(ids, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        let message = failed
          ? t('{succeeded} book(s) removed from your library; {failed} failed.', { succeeded, failed })
          : t('{n} book(s) removed from your library.', { n: succeeded });
        const tooLarge = result.errors.find((error) =>
          error instanceof ApiError && error.detail?.code === 'batch_too_large');
        if (tooLarge instanceof ApiError) {
          const maxItems = tooLarge.detail?.max_items;
          message += ' ' + (typeof maxItems === 'number'
            ? t('The server rejected a batch as too large (maximum {max} books).', { max: maxItems })
            : t('The server rejected a batch as too large.'));
        }
        announce(message, { assertive: failed > 0 });
        if (succeeded) onChanged?.();
        if (!failed) onClear();
      },
    });
  };

  const doMarkRead = (read: boolean) =>
    markRead.mutate({ ids, read }, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        announce(failed
          ? t('{succeeded} updated; {failed} failed.', { succeeded, failed })
          : (read ? t('{n} marked as read.', { n: succeeded }) : t('{n} marked as unread.', { n: succeeded })),
        { assertive: failed > 0 });
        if (succeeded) onChanged?.();
      },
    });

  const doAddToShelf = (shelfId: number) => {
    addToShelf.mutate({ ids, shelfId }, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        announce(failed
          ? t('{succeeded} added to the shelf; {failed} failed.', { succeeded, failed })
          : t('{n} book(s) added to the shelf.', { n: succeeded }), { assertive: failed > 0 });
        if (succeeded) onChanged?.();
      },
    });
    setShelfOpen(false);
  };

  const canEdit = !!me?.role?.edit;
  const applyMeta = () => {
    // Only send the fields the editor actually filled. list_mode is attached
    // after this check so selecting a mode alone cannot issue empty writes.
    const fields: MetadataUpdate = {};
    if (meta.tags.trim()) fields.tags = meta.tags.trim();
    if (meta.series.trim()) fields.series = meta.series.trim();
    if (meta.publishers.trim()) fields.publishers = meta.publishers.trim();
    if (meta.languages.trim()) fields.languages = meta.languages.trim();
    if (meta.authors.trim()) fields.authors = meta.authors.trim();
    if (Object.keys(fields).length === 0) return;
    if (listMode === 'replace' && !window.confirm(t(
      'Replace metadata for {n} selected book(s)? Those books will lose their existing values in every filled field.',
      { n: count },
    ))) return;
    fields.list_mode = listMode;
    setMetadata.mutate({ ids, fields }, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        announce(failed
          ? t('Metadata applied to {succeeded}; {failed} failed.', { succeeded, failed })
          : t('Metadata applied to {n} book(s).', { n: succeeded }), { assertive: failed > 0 });
        if (succeeded) onChanged?.();
        if (!failed) {
          setMetaOpen(false);
          setListMode('add');
          setMeta({ tags: '', series: '', publishers: '', languages: '', authors: '' });
        }
      },
    });
  };

  return (
    <div className={styles.bulkStack}>
    {metaOpen && (
      <div className={styles.metaPanel} role="region" aria-label={t('Apply metadata')}>
        <fieldset className={styles.modeGroup}>
          <legend>{t('How should multi-value fields be applied?')}</legend>
          <div className={styles.modeChoices}>
            <label className={listMode === 'add' ? styles.modeActive : styles.modeChoice}>
              <input type="radio" name="bulk-list-mode" value="add"
                checked={listMode === 'add'} onChange={() => setListMode('add')} />
              {t('Add to existing')}
            </label>
            <label className={listMode === 'replace' ? styles.modeActive : styles.modeChoice}>
              <input type="radio" name="bulk-list-mode" value="replace"
                checked={listMode === 'replace'} onChange={() => setListMode('replace')} />
              {t('Replace existing')}
            </label>
          </div>
        </fieldset>
        <p className={styles.metaHint} aria-live="polite">
          {listMode === 'add'
            ? t("New authors, tags, publishers, and languages will be added after each book's existing values. Filled single-value fields will be replaced.")
            : t("Every filled field will replace each book's existing values.")}
        </p>
        <div className={styles.metaGrid}>
          <input placeholder={t('Authors (separate with &)')} aria-label={t('Authors (separate with &)')} value={meta.authors}
            onChange={(e) => setMeta({ ...meta, authors: e.target.value })} />
          <input placeholder={t('Series')} aria-label={t('Series')} value={meta.series}
            onChange={(e) => setMeta({ ...meta, series: e.target.value })} />
          <input placeholder={t('Tags (comma separated)')} aria-label={t('Tags (comma separated)')} value={meta.tags}
            onChange={(e) => setMeta({ ...meta, tags: e.target.value })} />
          <input placeholder={t('Publishers (comma separated)')} aria-label={t('Publishers (comma separated)')} value={meta.publishers}
            onChange={(e) => setMeta({ ...meta, publishers: e.target.value })} />
          <input placeholder={t('Languages (comma separated)')} aria-label={t('Languages (comma separated)')} value={meta.languages}
            onChange={(e) => setMeta({ ...meta, languages: e.target.value })} />
        </div>
        <button className={styles.metaApply} onClick={applyMeta} disabled={setMetadata.isPending}>
          {setMetadata.isPending ? t('Applying…') : t('Apply to {n} books', { n: count })}
        </button>
      </div>
    )}
    <BulkSelectionBar count={count} onClear={onClear}>
        {personalLibrary && (
          <button type="button" className={styles.actionPrimary} disabled={busy}
            onClick={onRemoveFromMyLibrary}>
            <BookMinus size={15} aria-hidden="true" focusable={false} /> {t('Remove from my library')}
          </button>
        )}
        <button className={styles.action} disabled={busy}
          onClick={() => doMarkRead(true)}>
          <CheckCheck size={15} aria-hidden="true" focusable={false} /> {t('Mark read')}
        </button>
        <button className={styles.action} disabled={busy}
          onClick={() => doMarkRead(false)}>
          <Check size={15} aria-hidden="true" focusable={false} /> {t('Mark unread')}
        </button>

        <div className={styles.shelfWrap} ref={shelfRef}>
          <button className={styles.action} disabled={busy || editableShelves.length === 0}
            aria-haspopup="true" aria-expanded={shelfOpen}
            onClick={() => setShelfOpen((o) => !o)}>
            <BookCopy size={15} aria-hidden="true" focusable={false} /> {t('Add to shelf')}
          </button>
          {shelfOpen && (
            // Disclosure (not an ARIA menu): plain buttons, Tab-through + Escape.
            <div className={styles.shelfMenu}>
              {editableShelves.map((s) => (
                <button key={s.id} className={styles.shelfItem}
                  onClick={() => doAddToShelf(s.id)}>
                  {s.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {canEdit && (
          <button className={styles.action} disabled={busy} aria-expanded={metaOpen}
            onClick={() => {
              if (metaOpen) setListMode('add');
              setMetaOpen((open) => !open);
            }}>
            <Pencil size={15} aria-hidden="true" focusable={false} /> {t('Edit metadata')}
          </button>
        )}

        {canEdit && count >= 2 && (
          <button className={styles.action} disabled={busy} onClick={onMerge}>
            <Combine size={15} aria-hidden="true" focusable={false} /> {t('Merge')}
          </button>
        )}

        {canDelete && (
          <button type="button" className={styles.actionDanger} disabled={busy} onClick={onDelete}>
            <Trash2 size={15} aria-hidden="true" focusable={false} /> {t('Delete from the global library')}
          </button>
        )}

        {busy && <Spinner size={16} />}
    </BulkSelectionBar>
    </div>
  );
}
