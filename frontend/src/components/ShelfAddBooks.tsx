import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, Search, X } from 'lucide-react';
import { useBulkActions, useShelfAvailableBooks } from '../lib/queries';
import { useFocusTrap } from '../lib/a11y/useFocusTrap';
import { useT } from '../lib/i18n';
import { resourceUrl } from '../lib/api';
import { mergePickerPages, retainFailed, togglePick } from '../lib/shelfPicker';
import { Spinner } from './Spinner';
import styles from './ShelfAddBooks.module.css';

/** Bulk "Add books" picker for a manual shelf: search, tick books across as many
 *  searches as needed, then add everything in one go. Mirrors the classic UI's
 *  shelf-page modal. Writes go through the same per-book endpoint as every other
 *  shelf add, so permissions and de-duplication stay server-side. */
export function ShelfAddBooks({ shelfId, onClose, onAdded }: {
  shelfId: number;
  onClose: () => void;
  onAdded: () => void;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [error, setError] = useState('');
  const { addToShelf } = useBulkActions();
  const busy = addToShelf.isPending;

  // useFocusTrap re-runs its effect whenever onClose changes identity, which
  // restores focus to the trigger and then focuses the first control, stealing
  // focus from the search box on every keystroke. Keep the callback stable.
  const busyRef = useRef(busy);
  busyRef.current = busy;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const requestClose = useCallback(() => { if (!busyRef.current) closeRef.current(); }, []);

  useFocusTrap(dialogRef, { onClose: requestClose });
  // Declared after the trap so it wins on mount: land in the search box.
  useEffect(() => { searchRef.current?.focus(); }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => setQuery(text.trim()), 250);
    return () => window.clearTimeout(handle);
  }, [text]);

  const list = useShelfAvailableBooks(shelfId, query);
  const books = useMemo(
    () => mergePickerPages((list.data?.pages ?? []).map((page) => page.items)),
    [list.data],
  );

  const submit = async () => {
    if (!selected.size || busy) return;
    setError('');
    try {
      const result = await addToShelf.mutateAsync({ ids: [...selected], shelfId });
      if (result.failedIds.length === 0) {
        onAdded();
        onClose();
        return;
      }
      setSelected(retainFailed(selected, result.failedIds));
      setError(t('{failed} of {total} books could not be added. They remain selected so you can retry.',
        { failed: result.failedIds.length, total: result.failedIds.length + result.succeededIds.length }));
      if (result.succeededIds.length) onAdded();
      void list.refetch();
    } catch {
      setError(t('Could not add the selected books. Please try again.'));
    }
  };

  return <div className={styles.scrim} onClick={requestClose}>
    <div ref={dialogRef} className={styles.dialog} role="dialog" aria-modal="true"
      aria-labelledby="shelf-add-books-title" tabIndex={-1} onClick={(e) => e.stopPropagation()}>
      <div className={styles.heading}>
        <h2 id="shelf-add-books-title">{t('Add books to shelf')}</h2>
        <button type="button" className={styles.iconBtn} disabled={busy} onClick={onClose} aria-label={t('Close')}>
          <X size={20} aria-hidden="true" focusable={false} />
        </button>
      </div>
      <label className={styles.search}>
        <Search size={16} aria-hidden="true" focusable={false} />
        <input ref={searchRef} type="search" value={text} onChange={(e) => setText(e.target.value)}
          placeholder={t('Search title, author, series…')} aria-label={t('Search library')} />
      </label>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <div className={styles.results} aria-busy={list.isFetching}>
        {list.isLoading && <div className={styles.center}><Spinner /></div>}
        {list.isError && <p role="alert" className={styles.error}>{t('Could not load books.')}</p>}
        {!list.isLoading && !list.isError && books.length === 0 && <p className={styles.empty}>{t('No books found.')}</p>}
        <ul className={styles.list}>
          {books.map((book) => {
            const checked = selected.has(book.id);
            return <li key={book.id}>
              <label className={`${styles.row} ${checked ? styles.rowChecked : ''} ${book.in_shelf ? styles.rowDisabled : ''}`}>
                <input type="checkbox" checked={checked || book.in_shelf} disabled={book.in_shelf || busy}
                  onChange={() => setSelected((prev) => togglePick(prev, book))} />
                {book.cover_url
                  ? <img src={resourceUrl(book.cover_url)} alt="" loading="lazy" className={styles.cover} />
                  : <span className={styles.cover} aria-hidden="true" />}
                <span className={styles.meta}>
                  <span className={styles.title}>{book.title}</span>
                  {book.authors.length > 0 && <span className={styles.authors}>{book.authors.join(' & ')}</span>}
                </span>
                {book.in_shelf && <span className={styles.onShelf}><Check size={14} aria-hidden="true" focusable={false} /> {t('On shelf')}</span>}
              </label>
            </li>;
          })}
        </ul>
        {list.hasNextPage && <button type="button" className={styles.more} disabled={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}>
          {list.isFetchingNextPage ? t('Loading…') : t('Load more')}
        </button>}
      </div>
      <div className={styles.footer}>
        <span aria-live="polite">{t('{n} selected', { n: selected.size })}</span>
        <button type="button" className={styles.primary} disabled={!selected.size || busy} onClick={() => void submit()}>
          {busy ? t('Adding…') : t('Add selected')}
        </button>
      </div>
    </div>
  </div>;
}
