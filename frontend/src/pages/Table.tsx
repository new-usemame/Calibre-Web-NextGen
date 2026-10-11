import { useState, useEffect, useLayoutEffect, useRef } from 'react';
import { Link } from 'wouter';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import { ArrowUp, ArrowDown, Check, Columns3, ListChecks, Pencil, X } from 'lucide-react';
import { useBooks, useMe, useUpdateMetadata } from '../lib/queries';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT, useI18n } from '../lib/i18n';
import type { Book, ListCustomColumnDefinition } from '../lib/api';
import { formatAuthors } from '../lib/authors';
import { ApiError, apiGet, resourceUrl } from '../lib/api';
import { selectedCustomColumns, formatCustomColumnDate } from '../lib/customColumnDisplay';
import { BulkBar } from '../components/BulkBar';
import { useRangeSelection } from '../lib/useRangeSelection';
import { useLibraryRevision } from '../lib/libraryRevision';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './Table.module.css';

// Column key -> the API sort tokens for ascending / descending.
interface Col { key: string; label: string; sortAsc?: string; sortDesc?: string; custom?: ListCustomColumnDefinition; }
const COLUMNS: Col[] = [
  { key: 'title', label: 'Title', sortAsc: 'abc', sortDesc: 'zyx' },
  { key: 'authors', label: 'Authors', sortAsc: 'authaz', sortDesc: 'authza' },
  { key: 'series', label: 'Series' },
  // Tags has no server-side sort token, so it renders unsortable (#725).
  { key: 'tags', label: 'Tags' },
  { key: 'formats', label: 'Formats' },
  { key: 'date_added', label: 'Date added', sortAsc: 'old', sortDesc: 'new' },
  { key: 'last_modified', label: 'Last modified', sortAsc: 'modifiedold', sortDesc: 'modifiednew' },
  { key: 'read', label: 'Read' },
];

function formatCustomCell(book: Book, column: ListCustomColumnDefinition, locale: string): string {
  const value = book.custom_columns?.[String(column.id)]?.[0]?.value;
  if (value === null || value === undefined || value === '') return '—';
  if (column.datatype === 'datetime' && typeof value === 'string') return formatCustomColumnDate(value, locale) || '—';
  if ((column.datatype === 'int' || column.datatype === 'float') && typeof value === 'number') {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: column.datatype === 'float' ? 2 : 0 }).format(value);
  }
  return String(value);
}

function formatLibraryDate(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function EditableTitleCell({ book, canEdit, onSaved }: {
  book: Book; canEdit: boolean; onSaved: (title: string) => void;
}) {
  const t = useT();
  const update = useUpdateMetadata(book.id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(book.title);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);
  const cancel = () => { setValue(book.title); setEditing(false); };
  useLayoutEffect(() => {
    if (!canEdit) { setEditing(false); setValue(book.title); }
  }, [canEdit, book.title]);
  const save = () => {
    if (!canEdit || update.isPending) return;
    const title = value.trim();
    if (!title || title === book.title) { cancel(); return; }
    update.mutate({ title }, { onSuccess: () => { onSaved(title); setEditing(false); } });
  };

  if (!editing || !canEdit) return (
    <span className={styles.titleCell}>
      <Link href={`/book/${book.id}`} className={styles.titleLink}>{book.title}</Link>
      {canEdit && <button type="button" className={styles.inlineEdit}
        aria-label={t('Edit title for {title}', { title: book.title })} onClick={() => setEditing(true)}>
        <Pencil size={14} aria-hidden="true" focusable={false} />
      </button>}
    </span>
  );

  return <span className={styles.inlineForm}>
    <input ref={inputRef} value={value} aria-label={t('Title')}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => { if (event.key === 'Enter') save(); if (event.key === 'Escape') cancel(); }} />
    <button type="button" onClick={save} disabled={update.isPending || !value.trim()}>{t('Save')}</button>
    <button type="button" onClick={cancel} disabled={update.isPending} aria-label={t('Cancel title edit')}>
      <X size={14} aria-hidden="true" focusable={false} />
    </button>
    {update.isError && <span role="alert">{t('Could not save title.')}</span>}
  </span>;
}

function dedupAppend(prev: Book[], next: Book[]): Book[] {
  const seen = new Set(prev.map((b) => b.id));
  const updates = new Map(next.map((b) => [b.id, b]));
  const fresh = next.filter((b) => !seen.has(b.id));
  return [...prev.map((book) => updates.get(book.id) ?? book), ...fresh];
}

/** Native spreadsheet/table view of the library — sortable columns, column
 *  visibility, infinite "load more". Replaces the legacy /table page. */
export function Table() {
  const me = useMe().data;
  const permissions = Object.keys(me?.role ?? {}).sort().map((key) => [key, me?.role[key]]);
  // Account, permissions and library-mode changes replace the observer too.
  // Same-reader membership refreshes must keep it alive to report retry IDs.
  return <TableContents key={`${me?.id ?? 'guest'}:${me?.library_mode ?? 'monolibrary'}:${JSON.stringify(permissions)}`} />;
}

function TableContents() {
  const t = useT();
  const announce = useAnnouncer();
  const { locale } = useI18n();
  const me = useMe().data;
  const canEdit = !!me?.role?.edit;
  const libraryRevision = useLibraryRevision();
  const [sort, setSort] = useState('new');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<Book[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [colMenu, setColMenu] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [selectAllBusy, setSelectAllBusy] = useState(false);
  const [selectAllError, setSelectAllError] = useState('');
  const [refreshRevision, setRefreshRevision] = useState(0);
  const selectAllRequest = useRef(0);
  const selectButton = useRef<HTMLButtonElement>(null);
  const restoreSelectionFocus = useRef(false);
  const previousLibraryRevision = useRef(libraryRevision);
  const changedLibrary = previousLibraryRevision.current !== libraryRevision;
  const toggleSelect = useRangeSelection(setSelected, rows.map((book) => book.id), selecting);
  const accSort = useRef('');
  const previousRefreshRevision = useRef(refreshRevision);
  const changedRefresh = previousRefreshRevision.current !== refreshRevision;
  const previousSort = useRef(sort);
  const changedSort = previousSort.current !== sort;

  const { data, dataUpdatedAt, isLoading, isFetching, isPlaceholderData, error } = useBooks({ page: changedSort || changedLibrary || changedRefresh ? 1 : page, sort });

  const cancelSelection = (restoreFocus = false) => {
    selectAllRequest.current += 1;
    setSelectAllBusy(false);
    setSelectAllError('');
    setSelected(new Set());
    setSelecting(false);
    if (restoreFocus) restoreSelectionFocus.current = true;
  };
  useLayoutEffect(() => {
    if (restoreSelectionFocus.current && !selecting && !bulkBusy) {
      restoreSelectionFocus.current = false;
      selectButton.current?.focus();
    }
  }, [selecting, bulkBusy]);
  useLayoutEffect(() => {
    if (!changedSort) return;
    previousSort.current = sort;
    cancelSelection();
    setPage(1);
    setRows([]);
    accSort.current = '';
  }, [changedSort, sort]);
  useLayoutEffect(() => {
    if (!changedLibrary) return;
    previousLibraryRevision.current = libraryRevision;
    // Membership refreshes rebuild visible rows while the bulk operation's
    // observer and selected IDs survive until its result accounting runs.
    selectAllRequest.current += 1;
    setSelectAllBusy(false);
    setSelectAllError('');
    setPage(1);
    setRows([]);
    accSort.current = '';
  }, [changedLibrary, libraryRevision]);
  useLayoutEffect(() => {
    if (!changedRefresh) return;
    previousRefreshRevision.current = refreshRevision;
    setPage(1);
    setRows([]);
    accSort.current = '';
  }, [changedRefresh, refreshRevision]);
  useEffect(() => () => { selectAllRequest.current += 1; }, []);
  useEffect(() => {
    if (!data || isPlaceholderData || data.page !== page) return;
    // A completed bulk write rebuilds from page 1. An earlier page-2
    // response must not seed the reset accumulator before that render lands.
    if (accSort.current === '' && data.page !== 1) return;
    if (sort !== accSort.current) { setRows(data.items); accSort.current = sort; }
    else setRows((p) => dedupAppend(p, data.items));
  }, [data, dataUpdatedAt, isPlaceholderData, sort, page, refreshRevision]);

  const total = data?.total ?? 0;
  const selectAllBooks = async () => {
    const request = ++selectAllRequest.current;
    setSelectAllBusy(true);
    setSelectAllError('');
    announce(t('Selecting all books in this view…'));
    try {
      const result = await apiGet<{ ids: number[] }>(`/api/v1/books?${new URLSearchParams({ select_all: '1', sort })}`);
      if (request !== selectAllRequest.current) return;
      setSelected(new Set(result.ids));
      announce(t('Selected all {count} books in this view.', { count: result.ids.length }));
    } catch (error) {
      if (request !== selectAllRequest.current) return;
      const limit = error instanceof ApiError && error.detail?.code === 'selection_too_large'
        ? t('Select all is limited to {max} books. Narrow the current view and try again.', {
          max: typeof error.detail.max_items === 'number' ? error.detail.max_items : 100000,
        }) : t('Could not select all books. Try again.');
      setSelectAllError(limit);
      announce(limit, { assertive: true });
    } finally {
      if (request === selectAllRequest.current) setSelectAllBusy(false);
    }
  };
  const hasMore = rows.length < total;
  const sentinelRef = useIntersectionObserver({
    onIntersect: () => setPage((p) => p + 1),
    enabled: hasMore && !isFetching,
  });

  const onSort = (col: Col) => {
    if (!col.sortAsc) return;
    setSort((s) => (s === col.sortAsc ? col.sortDesc! : col.sortAsc!));
  };
  const sortIcon = (col: Col) => {
    if (col.sortAsc === sort) return <ArrowUp size={13} />;
    if (col.sortDesc === sort) return <ArrowDown size={13} />;
    return null;
  };
  const customColumns: Col[] = selectedCustomColumns(data?.custom_column_definitions, me)
    .map((column) => ({
    key: `custom-${column.id}`,
    label: column.name,
    sortAsc: `cc-${column.id}-asc`,
    sortDesc: `cc-${column.id}-desc`,
    custom: column,
  }));
  const allColumns = [...COLUMNS, ...customColumns];
  const visible = allColumns.filter((c) => !hidden.has(c.key));

  return (
    <main className={`${styles.container} ${selecting && selected.size ? styles.bulkActive : ''}`}>
      <div className={styles.header}>
        <h1 className={styles.title}>{t('Table view')}</h1>
        <span className={styles.count}>{total ? `${total}` : ''}</span>
        {me && !me.role.anonymous && <button ref={selectButton} type="button" className={styles.colBtn}
          aria-pressed={selecting} title={t('Select multiple')} disabled={bulkBusy}
          onClick={() => {
            const next = !selecting;
            cancelSelection(!next);
            setSelecting(next);
          }}>
          <ListChecks size={15} aria-hidden="true" focusable={false} /> {selecting ? t('Done') : t('Select')}
        </button>}
        {selecting && <button type="button" className={styles.colBtn}
          disabled={selectAllBusy || bulkBusy || !total} aria-busy={selectAllBusy}
          onClick={() => { void selectAllBooks(); }}>
          {selectAllBusy ? t('Selecting…') : t('Select all {count} books', { count: total })}
        </button>}
        <span className="sr-only" role="status">{selecting ? t('{n} selected', { n: selected.size }) : ''}</span>
        <div className={styles.colWrap}>
          <button className={styles.colBtn} onClick={() => setColMenu((v) => !v)} aria-expanded={colMenu}>
            <Columns3 size={15} /> {t('Columns')}
          </button>
          {colMenu && (
            <div className={styles.colMenu}>
              {allColumns.map((c) => (
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
      {selectAllError && <p className={styles.selectionError}>{selectAllError}</p>}

      {error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Failed to load.')} />
      ) : isLoading && rows.length === 0 ? (
        <SpinnerCentered size={40} />
      ) : rows.length === 0 ? (
        <EmptyState message={t('No books here.')} />
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {selecting && <th className={styles.selectionCol} scope="col">{t('Select')}</th>}
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
                        <button type="button" className={styles.thButton} disabled={bulkBusy} onClick={() => onSort(c)}>
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
                  <tr key={b.id} className={selected.has(b.id) ? styles.selectedRow : undefined}>
                    {selecting && <td className={styles.selectionCol}>
                      <input type="checkbox" checked={selected.has(b.id)} disabled={selectAllBusy || bulkBusy}
                        aria-label={t('Select {title}', { title: b.title })}
                        onChange={() => {}}
                        onClick={(event) => toggleSelect(b, event.shiftKey)} />
                    </td>}
                    <td className={styles.coverCol}>
                      {b.cover_url
                        ? <img src={resourceUrl(b.cover_url)} alt="" className={styles.coverThumb} loading="lazy" />
                        : <div className={styles.coverThumbEmpty} />}
                    </td>
                    {visible.map((c) => (
                      <td key={c.key}>
                        {c.key === 'title' && <EditableTitleCell book={b} canEdit={canEdit && !selecting}
                          onSaved={(title) => setRows((current) => current.map((row) =>
                            row.id === b.id ? { ...row, title } : row))} />}
                        {c.key === 'authors' && formatAuthors(b.authors)}
                        {c.key === 'series' && (b.series ? `${b.series}${b.series_index ? ` #${b.series_index}` : ''}` : '—')}
                        {c.key === 'tags' && ((b.tags || []).join(', ') || '—')}
                        {c.key === 'formats' && (b.formats || []).join(', ')}
                        {c.key === 'date_added' && <time dateTime={b.date_added ?? undefined}>{formatLibraryDate(b.date_added)}</time>}
                        {c.key === 'last_modified' && <time dateTime={b.last_modified ?? undefined}>{formatLibraryDate(b.last_modified)}</time>}
                        {c.key === 'read' && (b.read_status === 'did_not_finish'
                          ? <span>{t('Did not finish')}</span>
                          : b.read_status === 'on_hold'
                            ? <span>{t('On hold')}</span>
                            : b.in_progress
                              ? <span>{t('Currently reading')}</span>
                              : b.read
                          ? <Check size={15} className={styles.readYes} role="img" aria-label={t('Read')} />
                          : <span aria-label={t('Unread')} role="img">—</span>)}
                        {c.custom && formatCustomCell(b, c.custom, locale)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div ref={sentinelRef} className={styles.loadMore}>
              <button type="button" className={styles.colBtn} disabled={isFetching}
                onClick={() => setPage((current) => current + 1)}>{t('Load more')}</button>
              {isFetching && (<><Spinner size={16} /> {t('Loading…')}</>)}
            </div>
          )}
        </>
      )}
      {selecting && selected.size > 0 && <BulkBar ids={[...selected]}
        personalLibrary={me?.library_mode === 'personal_library'} onClear={() => cancelSelection(true)}
        onRetryable={(failed) => setSelected(new Set(failed))}
        actionsDisabled={selectAllBusy} onBusyChange={setBulkBusy}
        onChanged={() => {
          setPage(1);
          setRefreshRevision((current) => current + 1);
        }} />}
    </main>
  );
}
