import { useState, useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearch } from 'wouter';
import { ChevronLeft, SlidersHorizontal, ListChecks, Settings, RefreshCw, UploadCloud } from 'lucide-react';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import { BookCard } from '../components/BookCard';
import { BulkBar } from '../components/BulkBar';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { DiscoverSection } from '../components/DiscoverSection';
import { useBooks, useEntityList, ENTITY_PLURAL, useMe } from '../lib/queries';
import type { EntityKind, ReadFilter, DiscoveryView } from '../lib/queries';
import { apiPost, apiGet, type Book } from '../lib/api';
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

// Manual library scan (fork #780 / #665). The new UI had no equivalent of the
// classic header's "Refresh Library" button, so users who drop new files into
// the ingest folder had no way to trigger a re-scan from the SPA. POST
// /cwa-library-refresh starts a background ingest scan (csrf-exempt, session-
// authed — note these routes are NOT under /api/v1, so apiPost/apiGet only add
// the reverse-proxy mount prefix + credentials). We then poll
// /cwa-library-refresh/messages roughly once a second until the scan posts a
// result (or the ~2min cap elapses), then invalidate the catalog/discover/about
// queries so newly-ingested books + counts surface without a manual reload.
const LIBRARY_REFRESH_POLL_MS = 1000;
const LIBRARY_REFRESH_MAX_MS = 120000;

function useLibraryRefresh() {
  const qc = useQueryClient();
  const [isRefreshing, setRefreshing] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const deadlineRef = useRef(0);
  const inFlightRef = useRef(false);

  const stop = useCallback(() => {
    if (timerRef.current !== null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(false);
    setMessage('');
    try {
      const data = await apiPost<{ message: string }>('/cwa-library-refresh');
      setMessage(data.message ?? '');
      deadlineRef.current = Date.now() + LIBRARY_REFRESH_MAX_MS;
      // Guard against a double-click leaving two intervals running.
      stop();
      timerRef.current = setInterval(async () => {
        if (inFlightRef.current) return; // skip overlapping polls
        if (Date.now() >= deadlineRef.current) {
          stop();
          setRefreshing(false);
          return;
        }
        inFlightRef.current = true;
        try {
          const res = await apiGet<{ messages: string[] }>('/cwa-library-refresh/messages');
          if (res.messages && res.messages.length > 0) {
            stop();
            setMessage(res.messages.join('  '));
            setRefreshing(false);
            // Newly scanned books / metadata should now appear in the catalog.
            void qc.invalidateQueries({ queryKey: ['books'] });
            void qc.invalidateQueries({ queryKey: ['discover-strip'] });
            void qc.invalidateQueries({ queryKey: ['about'] });
          }
        } catch {
          // A transient poll failure is rare (the endpoint is an in-memory read);
          // keep polling until the deadline rather than aborting the scan.
        } finally {
          inFlightRef.current = false;
        }
      }, LIBRARY_REFRESH_POLL_MS);
    } catch (err) {
      stop();
      setRefreshing(false);
      setError(true);
      setMessage(err instanceof Error ? err.message : '');
    }
  }, [qc, stop]);

  // Clean up the poll interval if the catalog unmounts mid-scan.
  useEffect(() => () => stop(), [stop]);

  return { isRefreshing, message, error, refresh };
}

export function Catalog({ entityKind, entityId, view }: CatalogProps) {
  const t = useT();
  const libraryRefresh = useLibraryRefresh();
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
  const canUpload = !!useMe().data?.role?.upload;

  // Discover section visibility (persisted; toggled by the gear menu or its ×).
  const [discoverHidden, setDiscoverHidden] = usePersistentBool('cwng_discover_hidden_v1', false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);

  const accKeyRef = useRef<string>(snap?.resetKey ?? '');

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

  // The observer is a convenience, not the only way to reach another page:
  // this same guarded action also backs the keyboard/AT-visible Load more button.
  const loadMore = useCallback(() => {
    if (hasMore && !isFetching) setPage((p) => p + 1);
  }, [hasMore, isFetching]);

  const sentinelRef = useIntersectionObserver({
    onIntersect: loadMore,
    enabled: hasMore && !isFetching,
  });

  const heading = isView ? t(VIEW_LABEL[view!]) : filtered ? (entityName ?? '…') : t('Your Library');
  const countLabel = total > 0
    ? search && !filtered
      ? t('{count} results for "{query}"', { count: total, query: search })
      : t('{count} books', { count: total })
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
        {!hideLibraryControls && canUpload && (
          <Link href="/upload" className={styles.uploadLink}>
            <UploadCloud size={16} aria-hidden="true" focusable={false} />
            <span>{t('Upload books')}</span>
          </Link>
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

        {/* Manual library scan (fork #780 / #665) — the SPA equivalent of the
            classic header's "Refresh Library" button. Spins while the background
            ingest scan runs; the result is announced in the status line below. */}
        <button
          type="button"
          className={styles.refreshBtn}
          onClick={() => { void libraryRefresh.refresh(); }}
          disabled={libraryRefresh.isRefreshing}
          title={t('Refresh library')}
          aria-label={t('Refresh library')}
        >
          <RefreshCw size={15} className={libraryRefresh.isRefreshing ? styles.refreshIconSpin : undefined} />
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

      {/* Library-scan status (aria-live so the "please wait" → "complete"
          transition is announced, SC 4.1.3). Hidden when idle + empty. */}
      {(libraryRefresh.isRefreshing || libraryRefresh.message) && (
        <p
          className={libraryRefresh.error ? styles.refreshStatusError : styles.refreshStatus}
          role="status"
          aria-live="polite"
        >
          {libraryRefresh.message}
        </p>
      )}

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
            <div ref={sentinelRef} className={styles.loadMore}>
              <button
                type="button"
                className={styles.loadMoreButton}
                onClick={loadMore}
                disabled={isFetching}
              >
                {t('Load more')}
              </button>
              {isFetching && (
                <span className={styles.loadMoreStatus} role="status">
                  <Spinner size={16} />
                  {t('Loading…')}
                </span>
              )}
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
