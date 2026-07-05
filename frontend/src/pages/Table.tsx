import { useState, useEffect, useRef } from 'react';
import { Link } from 'wouter';
import { ArrowUp, ArrowDown, Check, Columns3 } from 'lucide-react';
import { useBooks } from '../lib/queries';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import type { Book } from '../lib/api';
import { resourceUrl } from '../lib/api';
import styles from './Table.module.css';

// Column key -> the API sort tokens for ascending / descending.
type ColKey = 'title' | 'authors' | 'series' | 'formats' | 'read';
interface Col { key: ColKey; label: string; sortAsc?: string; sortDesc?: string; }
const COLUMNS: Col[] = [
  { key: 'title', label: 'Title', sortAsc: 'abc', sortDesc: 'zyx' },
  { key: 'authors', label: 'Authors', sortAsc: 'authaz', sortDesc: 'authza' },
  { key: 'series', label: 'Series' },
  { key: 'formats', label: 'Formats' },
  { key: 'read', label: 'Read' },
];

function dedupAppend(prev: Book[], next: Book[]): Book[] {
  const seen = new Set(prev.map((b) => b.id));
  const fresh = next.filter((b) => !seen.has(b.id));
  return fresh.length ? [...prev, ...fresh] : prev;
}

/** Native spreadsheet/table view of the library — sortable columns, column
 *  visibility, infinite "load more". Replaces the legacy /table page. */
export function Table() {
  const t = useT();
  const [sort, setSort] = useState('new');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<Book[]>([]);
  const [hidden, setHidden] = useState<Set<ColKey>>(new Set());
  const [colMenu, setColMenu] = useState(false);
  const accSort = useRef('');

  const { data, isLoading, isFetching, isPlaceholderData, error } = useBooks({ page, sort });

  useEffect(() => { setPage(1); }, [sort]);
  useEffect(() => {
    if (!data || isPlaceholderData) return;
    if (sort !== accSort.current) { setRows(data.items); accSort.current = sort; }
    else setRows((p) => dedupAppend(p, data.items));
  }, [data, isPlaceholderData, sort]);

  const total = data?.total ?? 0;
  const hasMore = rows.length < total;

  const onSort = (col: Col) => {
    if (!col.sortAsc) return;
    setSort((s) => (s === col.sortAsc ? col.sortDesc! : col.sortAsc!));
  };
  const sortIcon = (col: Col) => {
    if (col.sortAsc === sort) return <ArrowUp size={13} />;
    if (col.sortDesc === sort) return <ArrowDown size={13} />;
    return null;
  };
  const visible = COLUMNS.filter((c) => !hidden.has(c.key));

  if (isLoading && rows.length === 0) return <SpinnerCentered size={40} />;
  if (error) {
    return <main className={styles.container}>
      <EmptyState message={error instanceof Error ? error.message : t('Failed to load.')} />
    </main>;
  }

  return (
    <main className={styles.container}>
      <div className={styles.header}>
        <h1 className={styles.title}>{t('Table view')}</h1>
        <span className={styles.count}>{total ? `${total}` : ''}</span>
        <div className={styles.colWrap}>
          <button className={styles.colBtn} onClick={() => setColMenu((v) => !v)} aria-expanded={colMenu}>
            <Columns3 size={15} /> {t('Columns')}
          </button>
          {colMenu && (
            <div className={styles.colMenu}>
              {COLUMNS.map((c) => (
                <label key={c.key} className={styles.colItem}>
                  <input type="checkbox" checked={!hidden.has(c.key)}
                    onChange={() => setHidden((h) => {
                      const n = new Set(h);
                      if (n.has(c.key)) n.delete(c.key); else n.add(c.key);
                      return n;
                    })} />
                  {t(c.label)}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState message={t('No books here.')} />
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.coverCol} aria-label={t('Cover')} />
                  {visible.map((c) => {
                    if (!c.sortAsc) {
                      return <th key={c.key}><span className={styles.thInner}>{t(c.label)}</span></th>;
                    }
                    // SC 2.1.1 + 4.1.2: the sort control is a real <button> and the
                    // <th> carries aria-sort so SR users hear the current order.
                    const ariaSort = c.sortAsc === sort ? 'ascending'
                      : c.sortDesc === sort ? 'descending' : 'none';
                    return (
                      <th key={c.key} className={styles.sortable} aria-sort={ariaSort}>
                        <button type="button" className={styles.thButton} onClick={() => onSort(c)}>
                          <span className={styles.thInner}>
                            {t(c.label)} <span aria-hidden="true">{sortIcon(c)}</span>
                          </span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {rows.map((b) => (
                  <tr key={b.id}>
                    <td className={styles.coverCol}>
                      {b.cover_url
                        ? <img src={resourceUrl(b.cover_url)} alt="" className={styles.coverThumb} loading="lazy" />
                        : <div className={styles.coverThumbEmpty} />}
                    </td>
                    {visible.map((c) => (
                      <td key={c.key}>
                        {c.key === 'title' && (
                          <Link href={`/book/${b.id}`} className={styles.titleLink}>{b.title}</Link>
                        )}
                        {c.key === 'authors' && (b.authors || []).join(', ')}
                        {c.key === 'series' && (b.series ? `${b.series}${b.series_index ? ` #${b.series_index}` : ''}` : '—')}
                        {c.key === 'formats' && (b.formats || []).join(', ')}
                        {c.key === 'read' && (b.read
                          ? <Check size={15} className={styles.readYes} role="img" aria-label={t('Read')} />
                          : <span aria-label={t('Unread')} role="img">—</span>)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
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
