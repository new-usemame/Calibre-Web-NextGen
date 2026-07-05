import { useState, useEffect, useRef } from 'react';
import { Link, useSearch } from 'wouter';
import { Search, ChevronLeft, SlidersHorizontal, ListChecks, Settings } from 'lucide-react';
import { BookCard } from '../components/BookCard';
import { BulkBar } from '../components/BulkBar';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { DiscoverSection } from '../components/DiscoverSection';
import { useBooks, useEntityList, ENTITY_PLURAL, useMe } from '../lib/queries';
import type { EntityKind, ReadFilter, DiscoveryView } from '../lib/queries';
import type { Book } from '../lib/api';
import { saveCatalog, loadCatalog } from '../lib/scrollCache';
import { usePersistentBool } from '../lib/usePersistentBool';
import { useT } from '../lib/i18n';
import styles from './Catalog.module.css';

const VIEW_LABEL: Record<DiscoveryView, string> = {
  hot: 'Hot — Most Downloaded',
  discover: 'Discover — Random Picks',
  rated: 'Top Rated',
  favorites: 'Favorites',
  archived: 'Archived',
};

const SORT_OPTIONS = [
  { label: 'Newest', value: 'new' },
  { label: 'Oldest', value: 'old' },
  { label: 'Title A–Z', value: 'abc' },
  { label: 'Title Z–A', value: 'zyx' },
  { label: 'Author A–Z', value: 'authaz' },
  { label: 'Author Z–A', value: 'authza' },
  { label: 'Newest published', value: 'pubnew' },
  { label: 'Oldest published', value: 'pubold' },
];

// Series-order sorts (by metadata series_index). Only offered when viewing a
// single series, where a numeric position is meaningful — a whole-library
// series_index sort is not. The ascending option is also the series view's
// default (see defaultSort below) so a series reads 1, 2, 3… out of the box (#573).
const SERIES_SORT_OPTIONS = [
  { label: 'Series order', value: 'seriesasc' },
  { label: 'Series order (reverse)', value: 'seriesdesc' },
];

const READ_FILTERS: { label: string; value: ReadFilter }[] = [
  { label: 'All', value: 'all' },
  { label: 'Unread', value: 'unread' },
  { label: 'Read', value: 'read' },
];

const KIND_LABEL: Record<EntityKind, string> = {
  author: 'Author',
  series: 'Series',
  tag: 'Tag',
  publisher: 'Publisher',
  language: 'Language',
  rating: 'Rating',
  format: 'Format',
};

interface CatalogProps {
  /** When set, the catalog is scoped to books linked to this entity. */
  entityKind?: EntityKind;
  entityId?: string | number;
  /** When set, render a fixed discovery view (hot/discover/rated/favorites/archived). */
  view?: DiscoveryView;
}

// Merge a freshly-fetched page into the accumulator: UPSERT existing books by id
// (a re-fetch — e.g. after restoring a scroll snapshot then react-query
// revalidates — brings updated fields, which must replace the stale copy, #578)
// and append genuinely-new ones. Add-only append would leave edited books showing
// their old title/cover after edit → Back.
function dedupAppend(prev: Book[], next: Book[]): Book[] {
  if (!next.length) return prev;
  const byId = new Map(next.map((b) => [b.id, b]));
  let changed = false;
  const merged = prev.map((b) => {
    const upd = byId.get(b.id);
    if (upd && upd !== b) { changed = true; return upd; }
    return b;
  });
  const seen = new Set(prev.map((b) => b.id));
  const fresh = next.filter((b) => !seen.has(b.id));
  if (!fresh.length && !changed) return prev;
  return [...merged, ...fresh];
}

export function Catalog({ entityKind, entityId, view }: CatalogProps) {
  const t = useT();
  const filtered = !!entityKind;
  const isView = !!view;
  const isSeries = entityKind === 'series';
  // Series views expose two extra series-order options and default to ascending
  // series order so the list reads 1, 2, 3… instead of newest-first (#573).
  const sortOptions = isSeries ? [...SERIES_SORT_OPTIONS, ...SORT_OPTIONS] : SORT_OPTIONS;
  const defaultSort = isSeries ? 'seriesasc' : 'new';
  // Library-only controls (search box, advanced link, read-status filter) are
  // hidden for both entity-scoped and discovery views.
  const hideLibraryControls = filtered || isView;

  // Scroll/state restoration (#578): identity of THIS catalog instance (library
  // vs a specific entity vs a discovery view) — stable across a book → Back trip.
  const restoreKey = `catalog:${entityKind ?? ''}:${entityId ?? ''}:${view ?? ''}`;
  // Only restore a snapshot when it's consistent with the current URL query. A
  // fresh top-bar search navigates to /?q=… on the SAME library route; a stale
  // snapshot must not be rehydrated there or it would ignore the new search
  // (Greptile #593). Entity/discovery views carry no ?q, so any snapshot applies.
  const urlQAtMount = new URLSearchParams(
    typeof window !== 'undefined' ? window.location.search : '').get('q') || '';
  const rawSnap = loadCatalog(restoreKey);
  const snapRef = useRef(
    (filtered || isView || (rawSnap?.search ?? '') === urlQAtMount) ? rawSnap : undefined);
  const snap = snapRef.current;
  // True only for this first restored mount — used to stop the reset/urlQ effects
  // from clobbering the rehydrated page/filters before the user does anything.
  const restoringRef = useRef(!!snap);

  const [page, setPage] = useState(() => snap?.page ?? 1);
  const [allBooks, setAllBooks] = useState<Book[]>(() => snap?.books ?? []);
  const [searchInput, setSearchInput] = useState(() => snap?.searchInput ?? '');
  const [search, setSearch] = useState(() => snap?.search ?? '');
  const [sort, setSort] = useState(() => snap?.sort ?? defaultSort);
  const [readFilter, setReadFilter] = useState<ReadFilter>(() => (snap?.readFilter as ReadFilter) ?? 'all');

  // Multi-select / bulk mode
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Quick-edit pencil on cards (fork #572) — only for users who can edit, and
  // never while multi-selecting (the whole card toggles selection then).
  const canEdit = !!useMe().data?.role?.edit;

  // Discover section visibility (persisted; toggled by the gear menu or its ×).
  const [discoverHidden, setDiscoverHidden] = usePersistentBool('cwng_discover_hidden_v1', false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);

  const accKeyRef = useRef<string>(snap?.resetKey ?? '');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Resolve the entity's display name (for the heading) from its browse list —
  // cached when the user arrives from the browse page, a cheap fetch otherwise.
  const entityListQuery = useEntityList(filtered ? ENTITY_PLURAL[entityKind!] : '');
  const entityName = filtered
    ? entityListQuery.data?.items.find((e) => String(e.id) === String(entityId))?.name
    : undefined;

  // Seed the search box from a ?q= query param (the persistent top-bar search
  // navigates here as /?q=<term>). Library view only.
  const rawSearch = useSearch();
  const urlQ = new URLSearchParams(rawSearch).get('q') || '';
  useEffect(() => {
    if (filtered || isView) return;
    // On the first restored mount, keep the rehydrated search rather than letting
    // the (empty) URL query clobber it (#578).
    if (restoringRef.current) return;
    setSearchInput(urlQ);
    setSearch(urlQ);
  }, [urlQ, filtered, isView]);

  // Debounce the search box (library view only).
  useEffect(() => {
    if (filtered) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setSearch(searchInput), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchInput, filtered]);

  // Close the settings menu on outside-click / Escape.
  useEffect(() => {
    if (!settingsOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) setSettingsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSettingsOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [settingsOpen]);

  const resetKey = [search, sort, readFilter, entityKind ?? '', entityId ?? '', view ?? ''].join('|');

  // Any filter change resets paging to the first page — except on the first
  // restored mount, where the rehydrated page must survive (#578).
  useEffect(() => {
    if (restoringRef.current) return;
    setPage(1);
  }, [resetKey]);

  // Clear the restoring flag after the initial mount so later filter/URL changes
  // behave normally. Runs after the two guarded effects above (effect order).
  useEffect(() => {
    restoringRef.current = false;
  }, []);

  // Persist this catalog's state on unmount (e.g. navigating into a book) so a
  // later Back rehydrates the loaded pages, filters and scroll position (#578).
  const persistRef = useRef({ page, books: allBooks, resetKey: accKeyRef.current, search, searchInput, sort, readFilter });
  persistRef.current = { page, books: allBooks, resetKey: accKeyRef.current, search, searchInput, sort, readFilter };

  // Track the live scroll offset in a ref. Reading window.scrollY in the unmount
  // cleanup is too late: by then the catalog has been swapped for the (shorter)
  // book page and the browser has already clamped window.scrollY down to that
  // page's max scroll — so a first-page position (nothing tall enough to survive
  // the clamp) was saved as ~0 and Back landed back at the top (#578 first-page
  // regression, reported by @KucharczykL). We record every scroll here and save
  // the tracked value; the click that triggers navigation is a discrete event,
  // so React flushes this unmount cleanup before the clamp's async scroll event,
  // and the real offset is preserved.
  const lastScrollYRef = useRef(snap?.scrollY ?? 0);
  useEffect(() => {
    const onScroll = () => { lastScrollYRef.current = window.scrollY; };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    return () => {
      const s = persistRef.current;
      saveCatalog(restoreKey, { ...s, scrollY: lastScrollYRef.current });
    };
  }, [restoreKey]);

  // Restore the saved scroll position on the first mount, once the rehydrated
  // grid has painted (its height comes from the restored books, so the offset is
  // reachable). Retry briefly to cover late layout (fonts/cover boxes).
  useEffect(() => {
    const y = snap?.scrollY ?? 0;
    if (!y) return;
    let tries = 0;
    let raf = 0;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      window.scrollTo(0, y);
      if (++tries < 6 && Math.abs(window.scrollY - y) > 2) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    // Cancel on unmount: without this, a quick book-open right after Back keeps
    // the retry alive and scrolls the NEXT page to this offset / fights the user (#578).
    return () => { cancelled = true; cancelAnimationFrame(raf); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { data, isLoading, isFetching, isPlaceholderData, error } = useBooks({
    page,
    search,
    sort,
    readFilter,
    entityKind,
    entityId,
    view,
  });

  // Accumulate pages; replace the accumulator whenever the filter set changes.
  // Skip placeholder data: on a filter change react-query briefly returns the
  // PREVIOUS result (placeholderData) under the new resetKey — acting on it
  // would mark the key seen and push the real filtered data onto the append
  // path, leaving stale cards behind a corrected count.
  useEffect(() => {
    if (!data || isPlaceholderData) return;
    if (resetKey !== accKeyRef.current) {
      setAllBooks(data.items);
      accKeyRef.current = resetKey;
    } else {
      setAllBooks((prev) => dedupAppend(prev, data.items));
    }
  }, [data, isPlaceholderData, resetKey]);

  const total = data?.total ?? 0;
  const hasMore = allBooks.length < total;
  const isFirstLoad = isLoading && allBooks.length === 0;

  const heading = isView ? t(VIEW_LABEL[view!]) : filtered ? (entityName ?? '…') : t('Your Library');
  const countLabel =
    total > 0
      ? search && !filtered
        ? `${total} result${total !== 1 ? 's' : ''} for "${search}"`
        : `${total} book${total !== 1 ? 's' : ''}`
      : '';

  return (
    <main className={styles.container}>
      {filtered && (
        <Link href={`/${ENTITY_PLURAL[entityKind!]}`} className={styles.back}>
          <ChevronLeft size={16} />
          All {ENTITY_PLURAL[entityKind!]}
        </Link>
      )}

      <div className={styles.header}>
        {filtered && <span className={styles.kindLabel}>{t(KIND_LABEL[entityKind!])}</span>}
        <h1 className={styles.title}>{heading}</h1>
        {/* role=status so the result count is announced when filters/search
            change it and when load-more grows it (SC 4.1.3). */}
        {countLabel && <span className={styles.count} role="status">{countLabel}</span>}
      </div>

      {/* Toolbar */}
      <div className={styles.toolbar}>
        {!hideLibraryControls && (
          <div className={styles.searchWrap}>
            <Search size={15} className={styles.searchIcon} />
            <input
              type="search"
              className={styles.searchInput}
              placeholder={t('Search title, author…')}
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              aria-label={t('Search books')}
            />
          </div>
        )}

        {!hideLibraryControls && (
          <Link href="/search" className={styles.advancedLink} title={t('Advanced search')}>
            <SlidersHorizontal size={15} />
            <span className={styles.advancedLabel}>{t('Advanced')}</span>
          </Link>
        )}

        {/* Read-status segmented control (disabled while a text search is active,
            which the API resolves on a separate code path). Hidden in a fixed
            discovery view, which owns the server-side filter. */}
        {!isView && (
        <div className={styles.segmented} role="group" aria-label={t('Read status filter')}>
          {READ_FILTERS.map((rf) => (
            <button
              key={rf.value}
              type="button"
              className={readFilter === rf.value ? styles.segActive : styles.seg}
              aria-pressed={readFilter === rf.value}
              disabled={!!search && !filtered}
              onClick={() => setReadFilter(rf.value)}
            >
              {t(rf.label)}
            </button>
          ))}
        </div>
        )}

        <select
          className={styles.sortSelect}
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          aria-label={t('Sort order')}
        >
          {sortOptions.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t(opt.label)}
            </option>
          ))}
        </select>

        <button
          type="button"
          className={selecting ? styles.selectBtnActive : styles.selectBtn}
          onClick={() => {
            setSelecting((s) => !s);
            setSelected(new Set());
          }}
          aria-pressed={selecting}
          title={t('Select multiple')}
        >
          <ListChecks size={15} />
          <span className={styles.selectLabel}>{selecting ? t('Done') : t('Select')}</span>
        </button>

        {/* View settings (library landing only) — currently houses the Discover
            section toggle; a natural home for future per-view preferences. */}
        {!hideLibraryControls && (
          <div className={styles.settingsWrap} ref={settingsRef}>
            <button
              type="button"
              className={settingsOpen ? styles.gearBtnActive : styles.gearBtn}
              onClick={() => setSettingsOpen((o) => !o)}
              aria-haspopup="true"
              aria-expanded={settingsOpen}
              title={t('View settings')}
              aria-label={t('View settings')}
            >
              <Settings size={15} />
            </button>
            {settingsOpen && (
              <div className={styles.settingsMenu} role="menu">
                <p className={styles.settingsHead}>{t('View settings')}</p>
                <label className={styles.settingsItem}>
                  <input
                    type="checkbox"
                    className={styles.settingsCheck}
                    checked={!discoverHidden}
                    onChange={(e) => setDiscoverHidden(!e.target.checked)}
                  />
                  <span>{t('Show Discover section')}</span>
                </label>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Discover: random picks, library landing only (not while searching). */}
      {!hideLibraryControls && !search && !discoverHidden && (
        <DiscoverSection onClose={() => setDiscoverHidden(true)} />
      )}

      {isFirstLoad ? (
        <SpinnerCentered size={36} />
      ) : error ? (
        <EmptyState message={error instanceof Error ? error.message : t('Failed to load books.')} />
      ) : allBooks.length === 0 && !isFetching ? (
        <EmptyState
          message={
            search && !filtered
              ? t('No results for "{q}".', { q: search })
              : readFilter !== 'all'
                ? t('No {filter} books here.', { filter: readFilter })
                : t('No books here.')
          }
        />
      ) : (
        <>
          <div className={styles.grid}>
            {allBooks.map((book, i) => (
              <BookCard
                key={book.id}
                book={book}
                showSeriesIndex={isSeries}
                style={{ animationDelay: `${Math.min(i, 24) * 35}ms` }}
                quickEdit={canEdit && !selecting}
                selectable={selecting}
                selected={selected.has(book.id)}
                onToggleSelect={(b) =>
                  setSelected((prev) => {
                    const next = new Set(prev);
                    if (next.has(b.id)) next.delete(b.id);
                    else next.add(b.id);
                    return next;
                  })
                }
              />
            ))}
          </div>

          {hasMore && (
            <div className={styles.loadMore}>
              <Button variant="ghost" onClick={() => setPage((p) => p + 1)} disabled={isFetching}>
                {isFetching ? (
                  <>
                    <Spinner size={16} />
                    {t('Loading…')}
                  </>
                ) : (
                  t('Load more')
                )}
              </Button>
            </div>
          )}
        </>
      )}

      {selecting && selected.size > 0 && (
        <BulkBar
          ids={[...selected]}
          onClear={() => {
            setSelected(new Set());
            setSelecting(false);
          }}
          onChanged={() => {
            // A bulk action changed read state / membership / removed books.
            // Reset the accumulated grid so the refetched first page replaces it
            // (the load-more accumulator otherwise keeps stale/deleted cards).
            setAllBooks([]);
            setPage(1);
            accKeyRef.current = '';
          }}
        />
      )}
    </main>
  );
}
