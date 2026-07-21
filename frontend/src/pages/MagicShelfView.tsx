import { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'wouter';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import { ChevronLeft, Copy, Trash2, Pencil, Smartphone, Info } from 'lucide-react';
import {
  useMagicShelfBooks, useDeleteMagicShelf, useDuplicateMagicShelf,
  useToggleMagicShelfKoboSync, useMe, useUpdateProfile,
} from '../lib/queries';
import { BookCard } from '../components/BookCard';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import type { Book } from '../lib/api';
import { ApiError } from '../lib/api';
import styles from './Shelf.module.css';

function dedupAppend(prev: Book[], next: Book[]): Book[] {
  const seen = new Set(prev.map((b) => b.id));
  const fresh = next.filter((b) => !seen.has(b.id));
  return fresh.length ? [...prev, ...fresh] : prev;
}

/** Native view of a saved smart shelf's matching books, with duplicate/delete. */
export function MagicShelfView({ id }: { id: string }) {
  const t = useT();
  const [, navigate] = useLocation();
  const [page, setPage] = useState(1);
  const [books, setBooks] = useState<Book[]>([]);
  const accKey = useRef('');
  const { data, isLoading, isFetching, isPlaceholderData, error } = useMagicShelfBooks(id, page);
  const del = useDeleteMagicShelf();
  const dup = useDuplicateMagicShelf();
  const { data: me } = useMe();
  const toggleKobo = useToggleMagicShelfKoboSync(id);
  const updateProfile = useUpdateProfile();
  const [actionError, setActionError] = useState<string | null>(null);
  const [koboWarning, setKoboWarning] = useState<string | null>(null);

  // Route reuse: reset paging when the shelf id changes (#612).
  useEffect(() => {
    setPage(1);
  }, [id]);

  // Skip placeholder data — accumulating the previous shelf's briefly-served
  // rows under the new id would mix both shelves' books (#612, see Shelf.tsx).
  useEffect(() => {
    if (!data || isPlaceholderData) return;
    if (String(id) !== accKey.current) { setBooks(data.items); accKey.current = String(id); }
    else setBooks((p) => dedupAppend(p, data.items));
  }, [data, id, isPlaceholderData]);

  // Infinite-scroll sentinel. Called before the conditional early returns below
  // so the hook order stays stable across the loading→loaded transition; `data`
  // is undefined on the first render, so guard `enabled` null-safely (#784).
  const sentinelRef = useIntersectionObserver({
    onIntersect: () => setPage((p) => p + 1),
    enabled: !!data && books.length < data.total && !isFetching,
  });

  if (isLoading && !data) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return <main className={styles.container}>
      <Link href="/" className={styles.back}><ChevronLeft size={16} /> {t('Library')}</Link>
      <EmptyState message={error instanceof Error ? error.message : t('Smart shelf not found.')} />
    </main>;
  }

  const total = data.total;
  const hasMore = books.length < total;

  // #870 (@auspex, umbrella #867): ordinary shelves have had this button since
  // the SPA landed; smart shelves were the only type you had to open the rule
  // editor to mark. The admin-level "Sync Magic Shelves to Kobo" setting gates
  // whether the mark does anything, so hide the control when it is off rather
  // than let it store inert intent.
  const canKobo = Boolean(
    data.is_owner && me?.features?.kobo_sync && me?.features?.kobo_sync_magic_shelves,
  );

  const onToggleKobo = () => {
    setActionError(null);
    setKoboWarning(null);
    toggleKobo.mutate(!data.kobo_sync, {
      onSuccess: (res) => setKoboWarning(res?.warning ?? null),
      onError: (err) => setActionError(
        err instanceof ApiError ? err.message : t('Could not update shelf.'),
      ),
    });
  };

  // Same trap as #866 on ordinary shelves: the per-shelf mark does nothing
  // while the account still syncs the whole library to the device.
  const koboMarkInert = Boolean(
    canKobo && data.kobo_sync && me?.kobo_only_shelves_sync === false,
  );

  const enableShelfOnlySync = () => {
    setActionError(null);
    updateProfile.mutate({ kobo_only_shelves_sync: true }, {
      onError: (err) => setActionError(
        err instanceof ApiError ? err.message : t('Could not update your account setting.'),
      ),
    });
  };

  return (
    <main className={styles.container}>
      <Link href="/" className={styles.back}><ChevronLeft size={16} /> {t('Library')}</Link>
      <div className={styles.header}>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{data.icon} {data.name}</h1>
        </div>
        <div className={styles.subRow}>
          <span className={styles.count}>{total} {t('books')}</span>
          {data.is_owner && (
            <div className={styles.manage}>
              <Link href={`/magic/${id}/edit`} className={styles.manageBtn}>
                <Pencil size={14} /> {t('Edit')}
              </Link>
              <button className={styles.manageBtn} disabled={dup.isPending}
                onClick={() => dup.mutate(Number(id))}>
                <Copy size={14} /> {t('Duplicate')}
              </button>
              {canKobo && (
                <button className={data.kobo_sync ? styles.manageBtnActive : styles.manageBtn}
                  onClick={onToggleKobo} disabled={toggleKobo.isPending}>
                  <Smartphone size={14} /> {data.kobo_sync ? t('Kobo sync on') : t('Enable Kobo sync')}
                </button>
              )}
              <button className={styles.manageBtnDanger} disabled={del.isPending}
                onClick={() => {
                  if (window.confirm(t('Delete this smart shelf? Your books are not affected.')))
                    del.mutate(Number(id), { onSuccess: () => navigate('/') });
                }}>
                <Trash2 size={14} /> {t('Delete')}
              </button>
            </div>
          )}
        </div>
        {actionError && <p className={styles.actionError}>{actionError}</p>}
        {koboWarning && <p className={styles.actionError} role="status">{koboWarning}</p>}

        {koboMarkInert && (
          <div className={styles.koboNotice} role="status">
            <Info size={18} className={styles.koboNoticeIcon} aria-hidden="true" />
            <div className={styles.koboNoticeBody}>
              <p className={styles.koboNoticeText}>
                {t('Your Kobo is still set to sync your whole library, so marking this shelf does nothing on its own. Switch your account to shelf-only syncing to make it take effect.')}
              </p>
              <p className={styles.koboNoticeFine}>
                {t('Books that are not on a Kobo-sync shelf are then removed from the device on its next sync. They stay in your library here.')}
              </p>
              <div className={styles.koboNoticeActions}>
                <button
                  className={styles.koboNoticeBtn}
                  onClick={enableShelfOnlySync}
                  disabled={updateProfile.isPending}
                >
                  {updateProfile.isPending ? t('Saving…') : t('Sync only my selected shelves')}
                </button>
                <Link href="/account" className={styles.koboNoticeLink}>
                  {t('Account settings')}
                </Link>
              </div>
            </div>
          </div>
        )}
      </div>

      {books.length === 0 && !isFetching ? (
        <EmptyState message={t('No books match this smart shelf right now.')} />
      ) : (
        <>
          <div className={styles.grid}>
            {books.map((b, i) => (
              <BookCard key={b.id} book={b} style={{ animationDelay: `${Math.min(i, 24) * 35}ms` }} />
            ))}
          </div>
          {hasMore && (
            <div ref={sentinelRef} className={styles.loadMore}>
              {isFetching && (<><Spinner size={16} /> {t('Loading…')}</>)}
            </div>
          )}
        </>
      )}
    </main>
  );
}
