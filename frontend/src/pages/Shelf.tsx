import { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'wouter';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import {
  ChevronLeft, Globe, Lock, Pencil, Trash2, Check, X, ArrowUpDown, ArrowUp, ArrowDown, Smartphone,
  Info,
} from 'lucide-react';
import {
  useShelf, useUpdateShelf, useDeleteShelf, useShelfMembership, useReorderShelfBooks, useMe,
  useUpdateProfile,
} from '../lib/queries';
import { BookCard } from '../components/BookCard';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import type { Book } from '../lib/api';
import { ApiError } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './Shelf.module.css';

function dedupAppend(prev: Book[], next: Book[]): Book[] {
  const seen = new Set(prev.map((b) => b.id));
  const fresh = next.filter((b) => !seen.has(b.id));
  return fresh.length ? [...prev, ...fresh] : prev;
}

export function Shelf({ id }: { id: string }) {
  const t = useT();
  const [, navigate] = useLocation();
  const [page, setPage] = useState(1);
  const [books, setBooks] = useState<Book[]>([]);
  const accKeyRef = useRef<string>('');

  const { data, isLoading, isFetching, isPlaceholderData, error } = useShelf(id, page);
  const updateShelf = useUpdateShelf(id);
  const deleteShelf = useDeleteShelf();
  const reorder = useReorderShelfBooks(id);
  const { remove } = useShelfMembership();
  const me = useMe().data;
  const updateProfile = useUpdateProfile();

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const [reordering, setReordering] = useState(false);

  // Route reuse (/shelf/A -> /shelf/B keeps this component mounted): reset
  // paging and per-shelf UI modes when the shelf changes (#612).
  useEffect(() => {
    setPage(1);
    setEditing(false);
    setReordering(false);
    setActionError(null);
  }, [id]);

  // Accumulate pages; replace when the shelf changes. Skip placeholder data:
  // on an id change react-query would briefly serve the PREVIOUS shelf's rows
  // (placeholderData) — accumulating those stamps them as the new shelf's and
  // appends the real rows behind them, mixing both shelves' books (#612).
  useEffect(() => {
    if (!data || isPlaceholderData) return;
    const key = String(id);
    if (key !== accKeyRef.current) {
      setBooks(data.items);
      accKeyRef.current = key;
    } else {
      setBooks((prev) => dedupAppend(prev, data.items));
    }
  }, [data, id, isPlaceholderData]);

  // Infinite-scroll sentinel. This hook MUST be called before any conditional
  // early return below — Rules of Hooks require a stable hook order across
  // renders. `data` is undefined on the first (loading) render and populated on
  // the next, so `enabled` is guarded null-safely rather than deriving it from
  // the post-guard `total`/`hasMore` (#784: calling it after the guards made the
  // hook count jump loading→loaded and crashed the shelf to a blank screen).
  const sentinelRef = useIntersectionObserver({
    onIntersect: () => setPage((p) => p + 1),
    enabled: !!data && books.length < data.total && !isFetching,
  });

  if (isLoading && !data) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return (
      <main className={styles.container}>
        <Link href="/shelves" className={styles.back}>
          <ChevronLeft size={16} /> {t('All shelves')}
        </Link>
        <EmptyState message={error instanceof Error ? error.message : t('Shelf not found.')} />
      </main>
    );
  }

  const total = data.total;
  const hasMore = books.length < total;
  const canEdit = data.can_edit;

  const startRename = () => {
    setDraftName(data.name);
    setEditing(true);
    setActionError(null);
  };

  const saveRename = () => {
    const trimmed = draftName.trim();
    if (!trimmed || trimmed === data.name) {
      setEditing(false);
      return;
    }
    updateShelf.mutate(
      { name: trimmed },
      {
        onSuccess: () => setEditing(false),
        onError: (err) =>
          setActionError(err instanceof ApiError ? err.message : t('Could not rename shelf.')),
      },
    );
  };

  const onDelete = () => {
    if (!window.confirm(t('Delete shelf "{name}"? This cannot be undone.', { name: data.name }))) return;
    deleteShelf.mutate(Number(id), {
      onSuccess: () => navigate('/shelves'),
      onError: (err) =>
        setActionError(err instanceof ApiError ? err.message : t('Could not delete shelf.')),
    });
  };

  const onRemoveBook = (book: Book) => {
    // Optimistically drop from the local list; the query invalidation reconciles.
    setBooks((prev) => prev.filter((b) => b.id !== book.id));
    remove.mutate({ shelfId: Number(id), bookId: book.id });
  };

  const toggleVisibility = () => {
    setActionError(null);
    updateShelf.mutate({ is_public: !data.is_public }, {
      onError: (err) => setActionError(err instanceof ApiError ? err.message : t('Could not update shelf.')),
    });
  };

  const toggleKoboSync = () => {
    setActionError(null);
    updateShelf.mutate({ kobo_sync: !data.kobo_sync }, {
      onError: (err) => setActionError(err instanceof ApiError ? err.message : t('Could not update shelf.')),
    });
  };

  // #866 (@auspex): "Kobo sync on" on a shelf does nothing while the account
  // still syncs the whole library — the device pages through every book and
  // never settles on the shelf. Nothing said so, so the mark looked broken.
  // Only warn when the flag is actually known (an older server omits it) and
  // the shelf is one of ours — a public shelf someone else marked is not synced
  // to our device either way.
  const koboMarkInert = Boolean(
    me?.features?.kobo_sync && data.kobo_sync && data.is_owner
    && me?.kobo_only_shelves_sync === false,
  );

  const enableShelfOnlySync = () => {
    setActionError(null);
    updateProfile.mutate({ kobo_only_shelves_sync: true }, {
      onError: (err) => setActionError(
        err instanceof ApiError ? err.message : t('Could not update your account setting.'),
      ),
    });
  };

  // Move a book up/down in the loaded order, then persist the full order.
  const moveBook = (index: number, dir: -1 | 1) => {
    const next = [...books];
    const target = index + dir;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setBooks(next);
    reorder.mutate(next.map((b) => b.id), {
      onError: (err) => setActionError(err instanceof ApiError ? err.message : t('Could not save order.')),
    });
  };

  return (
    <main className={styles.container}>
      <Link href="/shelves" className={styles.back}>
        <ChevronLeft size={16} /> {t('All shelves')}
      </Link>

      <div className={styles.header}>
        <div className={styles.titleRow}>
          {editing ? (
            <div className={styles.renameRow}>
              <input
                className={styles.renameInput}
                value={draftName}
                onChange={(e) => setDraftName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') saveRename();
                  if (e.key === 'Escape') setEditing(false);
                }}
                autoFocus
                aria-label={t('Shelf name')}
                maxLength={120}
              />
              <button className={styles.iconBtn} onClick={saveRename} aria-label={t('Save name')} title={t('Save')}>
                <Check size={18} />
              </button>
              <button
                className={styles.iconBtn}
                onClick={() => setEditing(false)}
                aria-label={t('Cancel rename')}
                title={t('Cancel')}
              >
                <X size={18} />
              </button>
            </div>
          ) : (
            <>
              <h1 className={styles.title}>{data.name}</h1>
              <span
                className={styles.visibility}
                title={data.is_public ? t('Public shelf') : t('Private shelf')}
              >
                {data.is_public ? <Globe size={16} /> : <Lock size={16} />}
              </span>
            </>
          )}
        </div>

        <div className={styles.subRow}>
          <span className={styles.count}>
            {total === 1
              ? t('{count} book', { count: total })
              : t('{count} books', { count: total })}
          </span>
          {canEdit && !editing && (
            <div className={styles.manage}>
              <button className={styles.manageBtn} onClick={startRename}>
                <Pencil size={14} /> {t('Rename')}
              </button>
              <button className={styles.manageBtn} onClick={toggleVisibility} disabled={updateShelf.isPending}>
                {data.is_public ? <Lock size={14} /> : <Globe size={14} />}
                {data.is_public ? t('Make private') : t('Make public')}
              </button>
              {me?.features?.kobo_sync && (
                <button className={data.kobo_sync ? styles.manageBtnActive : styles.manageBtn}
                  onClick={toggleKoboSync} disabled={updateShelf.isPending}>
                  <Smartphone size={14} /> {data.kobo_sync ? t('Kobo sync on') : t('Enable Kobo sync')}
                </button>
              )}
              {books.length > 1 && !hasMore && (
                <button className={reordering ? styles.manageBtnActive : styles.manageBtn}
                  onClick={() => setReordering((v) => !v)}>
                  <ArrowUpDown size={14} /> {reordering ? t('Done reordering') : t('Reorder')}
                </button>
              )}
              <button className={styles.manageBtnDanger} onClick={onDelete} disabled={deleteShelf.isPending}>
                <Trash2 size={14} /> {t('Delete')}
              </button>
            </div>
          )}
        </div>
        {actionError && <p className={styles.actionError}>{actionError}</p>}

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
                  {updateProfile.isPending
                    ? t('Saving…')
                    : t('Sync only my selected shelves')}
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
        <EmptyState message={t("This shelf is empty. Add books from any book's page.")} />
      ) : reordering ? (
        <ol className={styles.reorderList}>
          {books.map((book, i) => (
            <li key={book.id} className={styles.reorderItem}>
              <span className={styles.reorderPos}>{i + 1}</span>
              <span className={styles.reorderTitle}>{book.title}</span>
              <span className={styles.reorderControls}>
                <button className={styles.iconBtn} onClick={() => moveBook(i, -1)}
                  disabled={i === 0 || reorder.isPending} aria-label={t('Move up')}>
                  <ArrowUp size={16} />
                </button>
                <button className={styles.iconBtn} onClick={() => moveBook(i, 1)}
                  disabled={i === books.length - 1 || reorder.isPending} aria-label={t('Move down')}>
                  <ArrowDown size={16} />
                </button>
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <>
          <div className={styles.grid}>
            {books.map((book, i) => (
              <BookCard
                key={book.id}
                book={book}
                style={{ animationDelay: `${Math.min(i, 24) * 35}ms` }}
                onRemove={canEdit ? onRemoveBook : undefined}
                removeLabel={t('Remove from shelf')}
              />
            ))}
          </div>

          {hasMore && (
            <div ref={sentinelRef} className={styles.loadMore}>
              {isFetching && (
                <>
                  <Spinner size={16} /> {t('Loading…')}
                </>
              )}
            </div>
          )}
        </>
      )}
    </main>
  );
}
