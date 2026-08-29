import { useState, useRef, useEffect } from 'react';
import { Check, X, BookCopy, Trash2, CheckCheck, Pencil, Combine } from 'lucide-react';
import { useBulkActions, useShelves, useMe, useMergeBooks } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { Spinner } from './Spinner';
import type { MetadataListMode, MetadataUpdate } from '../lib/api';
import styles from './BulkBar.module.css';

interface BulkBarProps {
  ids: number[];
  onClear: () => void;
  /** Called after a mutation that changes what the catalog should show
   *  (read state / membership / deletion), so the grid can refresh. */
  onChanged?: () => void;
}

export function BulkSelectionBar({ count, onClear, children, sticky = false }: {
  count: number; onClear: () => void; children: React.ReactNode; sticky?: boolean;
}) {
  const t = useT();
  return (
    <div className={`${styles.bar} ${sticky ? styles.sticky : ''}`} role="region"
      aria-label={t('{n} selected', { n: count })}>
      <span className={styles.count}>{t('{n} selected', { n: count })}</span>
      <div className={styles.actions}>{children}</div>
      <button className={styles.clear} onClick={onClear} aria-label={t('Clear selection')}>
        <X size={18} aria-hidden="true" focusable={false} />
      </button>
    </div>
  );
}

/** Floating action bar for the catalog's multi-select mode. Fans each action
 *  out over the selected book ids via the existing per-book endpoints. */
export function BulkBar({ ids, onClear, onChanged }: BulkBarProps) {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const { markRead, addToShelf, remove, setMetadata } = useBulkActions();
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
  const busy = markRead.isPending || addToShelf.isPending || remove.isPending
    || setMetadata.isPending || mergeBooks.isPending;
  const count = ids.length;

  const onMerge = () => {
    if (count < 2) return;
    if (!window.confirm(t('Merge {n} books into the first selected? The others are removed after their formats are copied over.', { n: count }))) return;
    mergeBooks.mutate(ids, { onSuccess: () => { onChanged?.(); onClear(); } });
  };

  const onDelete = () => {
    if (!window.confirm(t('Delete {n} book(s)? This cannot be undone.', { n: count }))) return;
    remove.mutate(ids, {
      onSuccess: (result) => {
        const succeeded = result.succeededIds.length;
        const failed = result.failedIds.length;
        announce(failed
          ? t('{succeeded} book(s) deleted; {failed} failed.', { succeeded, failed })
          : t('{n} book(s) deleted.', { n: succeeded }), { assertive: failed > 0 });
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
          <button className={styles.actionDanger} disabled={busy} onClick={onDelete}>
            <Trash2 size={15} aria-hidden="true" focusable={false} /> {t('Delete')}
          </button>
        )}

        {busy && <Spinner size={16} />}
    </BulkSelectionBar>
    </div>
  );
}
