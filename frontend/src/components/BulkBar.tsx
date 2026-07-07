import { useState, useRef, useEffect } from 'react';
import { Check, X, BookCopy, Trash2, CheckCheck, Pencil, Combine } from 'lucide-react';
import { useBulkActions, useShelves, useMe, useMergeBooks } from '../lib/queries';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import { Spinner } from './Spinner';
import type { MetadataUpdate } from '../lib/api';
import styles from './BulkBar.module.css';

interface BulkBarProps {
  ids: number[];
  onClear: () => void;
  /** Called after a mutation that changes what the catalog should show
   *  (read state / membership / deletion), so the grid can refresh. */
  onChanged?: () => void;
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

  const canDelete = !!me?.role?.delete_books;
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
      onSuccess: () => { announce(t('{n} book(s) deleted.', { n: count })); onChanged?.(); onClear(); },
    });
  };

  const doMarkRead = (read: boolean) =>
    markRead.mutate({ ids, read }, {
      onSuccess: () => {
        announce(read ? t('{n} marked as read.', { n: count }) : t('{n} marked as unread.', { n: count }));
        onChanged?.();
      },
    });

  const doAddToShelf = (shelfId: number) => {
    addToShelf.mutate({ ids, shelfId }, {
      onSuccess: () => { announce(t('{n} book(s) added to the shelf.', { n: count })); onChanged?.(); },
    });
    setShelfOpen(false);
  };

  const canEdit = !!me?.role?.edit;
  const applyMeta = () => {
    // Only send the fields the admin actually filled (replace semantics).
    const fields: MetadataUpdate = {};
    if (meta.tags.trim()) fields.tags = meta.tags.trim();
    if (meta.series.trim()) fields.series = meta.series.trim();
    if (meta.publishers.trim()) fields.publishers = meta.publishers.trim();
    if (meta.languages.trim()) fields.languages = meta.languages.trim();
    if (meta.authors.trim()) fields.authors = meta.authors.trim();
    if (Object.keys(fields).length === 0) return;
    setMetadata.mutate({ ids, fields }, {
      onSuccess: () => {
        announce(t('Metadata applied to {n} book(s).', { n: count }));
        onChanged?.();
        setMetaOpen(false);
        setMeta({ tags: '', series: '', publishers: '', languages: '', authors: '' });
      },
    });
  };

  return (
    <>
    {metaOpen && (
      <div className={styles.metaPanel}>
        <p className={styles.metaHint}>{t('Apply to all selected (only filled fields change; replaces existing values):')}</p>
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
    <div className={styles.bar} role="toolbar" aria-label={t('Bulk actions')}>
      <span className={styles.count}>{t('{n} selected', { n: count })}</span>

      <div className={styles.actions}>
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
            onClick={() => setMetaOpen((o) => !o)}>
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
      </div>

      <button className={styles.clear} onClick={onClear} aria-label={t('Clear selection')}>
        <X size={18} aria-hidden="true" focusable={false} />
      </button>
    </div>
    </>
  );
}
