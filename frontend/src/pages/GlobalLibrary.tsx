import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { Link, useLocation, useSearch } from 'wouter';
import { BookCard } from '../components/BookCard';
import { EmptyState } from '../components/EmptyState';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { ApiError, type Book } from '../lib/api';
import { useAnnouncer } from '../lib/a11y/announcer';
import { useAddToMyLibrary, useGlobalLibrary, useMe } from '../lib/queries';
import { usePersistentBool } from '../lib/usePersistentBool';
import { usePersistentChoice } from '../lib/usePersistentChoice';
import { useCardActionsHidden } from '../lib/useCardActionsHidden';
import { useT } from '../lib/i18n';
import catalogStyles from './Catalog.module.css';
import styles from './GlobalLibrary.module.css';

function appendUnique(previous: Book[], incoming: Book[]) {
  const byId = new Map(previous.map((book) => [book.id, book]));
  incoming.forEach((book) => byId.set(book.id, book));
  return [...byId.values()];
}

export function GlobalLibrary() {
  const t = useT();
  const announce = useAnnouncer();
  const me = useMe().data;
  const [, navigate] = useLocation();
  const rawSearch = useSearch();
  const initial = new URLSearchParams(rawSearch);
  const [onlyMissing, setOnlyMissing] = usePersistentBool(
    'cwng:global-unowned-v1', initial.get('scope') === 'unowned');
  const [query, setQuery] = useState(initial.get('q') ?? '');
  const [search, setSearch] = useState(initial.get('q') ?? '');
  const [sort, setSort] = useState('new');
  const [density] = usePersistentChoice(
    'cwng:catalog-density-v1', ['comfortable', 'compact', 'dense'] as const, 'compact');
  const [cardActionsHidden] = useCardActionsHidden();
  const [page, setPage] = useState(1);
  const [books, setBooks] = useState<Book[]>([]);
  const filter = onlyMissing ? 'not_in_my_library' : 'all';
  const listing = useGlobalLibrary({ page, search, sort, filter });
  const add = useAddToMyLibrary();

  useEffect(() => {
    if (me?.library_mode === 'monolibrary') navigate('/', { replace: true });
  }, [me?.library_mode, navigate]);

  useEffect(() => {
    setPage(1);
    setBooks([]);
    const params = new URLSearchParams();
    if (onlyMissing) params.set('scope', 'unowned');
    if (search) params.set('q', search);
    const suffix = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${suffix ? `?${suffix}` : ''}`);
  }, [onlyMissing, search, sort]);

  useEffect(() => {
    if (!listing.data || listing.isPlaceholderData) return;
    setBooks((current) => page === 1 ? listing.data!.items : appendUnique(current, listing.data!.items));
  }, [listing.data, listing.isPlaceholderData, page]);

  useEffect(() => {
    if (!listing.data || listing.isPlaceholderData || !listing.data.sort || listing.data.sort === sort) return;
    setSort(listing.data.sort);
  }, [listing.data, listing.isPlaceholderData, sort]);

  if (me?.library_mode === 'monolibrary') return <SpinnerCentered size={40} />;

  const denied = listing.error instanceof ApiError && listing.error.status === 403;
  const total = listing.data?.total ?? 0;
  const hasMore = books.length < total;
  const sortOptions = [
    { value: 'new', label: t('Recently added') },
    { value: 'old', label: t('Oldest') },
    { value: 'abc', label: t('Title A–Z') },
    { value: 'authaz', label: t('Author A–Z') },
    ...(listing.data?.custom_sort_options ?? []),
  ];

  const addBook = (book: Book) => add.mutate(book.id, {
    onSuccess: () => announce(t('Added to your library')),
    onError: () => announce(t('Could not add the book. Please try again.'), { assertive: true }),
  });

  return (
    <main className={catalogStyles.container} data-testid="global-library-page">
      <div className={catalogStyles.header}>
        <h1 className={catalogStyles.title}>{t('Global Library')}</h1>
        {total > 0 && <span className={catalogStyles.count} role="status">{t('{count} books', { count: total })}</span>}
      </div>
      <p className={styles.intro}>{t('The whole archive. Add books to your library from here.')}</p>

      <div className={catalogStyles.toolbar}>
        <div className={styles.scope} role="group" aria-label={t('Library scope')}>
          <button type="button" aria-pressed={!onlyMissing} onClick={() => setOnlyMissing(false)}>{t('All')}</button>
          <button type="button" aria-pressed={onlyMissing} onClick={() => setOnlyMissing(true)}>{t('Not in your library')}</button>
        </div>
        <form className={catalogStyles.searchWrap} onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); }}>
          <Search size={15} className={catalogStyles.searchIcon} aria-hidden="true" focusable={false} />
          <label className="sr-only" htmlFor="global-library-search">{t('Search')}</label>
          <input id="global-library-search" type="search" className={catalogStyles.searchInput}
            value={query} onChange={(event) => setQuery(event.target.value)}
            placeholder={t('Search title, author…')} />
        </form>
        <select className={catalogStyles.sortSelect} value={sort} onChange={(event) => setSort(event.target.value)}
          aria-label={t('Sort order')}>
          {sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </div>

      {listing.isLoading && books.length === 0 ? (
        <div className={`${catalogStyles.grid} ${catalogStyles[`density_${density}`]}`}><div className={catalogStyles.gridLoading}><SpinnerCentered size={36} /></div></div>
      ) : denied ? (
        <EmptyState message={t("You don't have access to the global library. Ask an administrator to grant it.")} />
      ) : listing.error ? (
        <EmptyState message={listing.error instanceof Error ? listing.error.message : t('Failed to load books.')} />
      ) : books.length === 0 ? (
        <EmptyState message={onlyMissing
          ? t('Every book here is already in your library.')
          : t('No books here.')} />
      ) : (
        <>
          <div className={`${catalogStyles.grid} ${catalogStyles[`density_${density}`]}`}>
            {books.map((book) => {
              const owned = book.in_my_library !== false;
              return <BookCard key={book.id} book={book} membership={owned ? 'owned' : 'unowned'}
                detailsEnabled
                canRead={owned && !!me?.role?.viewer}
                hideActions={cardActionsHidden}
                onAddToLibrary={owned ? undefined : addBook}
                addPending={add.isPending && add.variables === book.id} />;
            })}
          </div>
          {hasMore && <div className={catalogStyles.loadMore}>
            <button type="button" className={catalogStyles.loadMoreButton} disabled={listing.isFetching}
              onClick={() => setPage((current) => current + 1)}>{t('Load more')}</button>
            {listing.isFetching && <span className={catalogStyles.loadMoreStatus} role="status"><Spinner size={16} />{t('Loading…')}</span>}
          </div>}
        </>
      )}
      <Link href="/" className={styles.back}>{t('My Library')}</Link>
    </main>
  );
}
