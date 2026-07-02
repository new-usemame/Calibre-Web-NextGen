import { useState, useEffect, useRef } from 'react';
import { Link, useLocation } from 'wouter';
import { ChevronLeft, Copy, Trash2, Pencil } from 'lucide-react';
import { useMagicShelfBooks, useDeleteMagicShelf, useDuplicateMagicShelf } from '../lib/queries';
import { BookCard } from '../components/BookCard';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import type { Book } from '../lib/api';
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

  if (isLoading && !data) return <SpinnerCentered size={40} />;
  if (error || !data) {
    return <main className={styles.container}>
      <Link href="/" className={styles.back}><ChevronLeft size={16} /> {t('Library')}</Link>
      <EmptyState message={error instanceof Error ? error.message : t('Smart shelf not found.')} />
    </main>;
  }

  const total = data.total;
  const hasMore = books.length < total;

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
            <div className={styles.loadMore}>
              <Button variant="ghost" onClick={() => setPage((p) => p + 1)} disabled={isFetching}>
                {isFetching ? (<><Spinner size={16} /> {t('Loading…')}</>) : t('Load more')}
              </Button>
            </div>
          )}
        </>
      )}
    </main>
  );
}
