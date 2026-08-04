import { useState, useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearch, useLocation } from 'wouter';
import { ChevronLeft, SlidersHorizontal, ListChecks, Settings, RefreshCw, UploadCloud, LayoutGrid, List, Pencil, Check, X, Trash2, Merge } from 'lucide-react';
import { useIntersectionObserver } from '../lib/useIntersectionObserver';
import { BookCard } from '../components/BookCard';
import { BookCover } from '../components/BookCover';
import { BulkBar } from '../components/BulkBar';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { DiscoverSection } from '../components/DiscoverSection';
import { useBooks, useAdvancedSearch, useEntityList, ENTITY_PLURAL, useMe, useRenameTag, useDeleteTag, tagConflictOf } from '../lib/queries';
import type { TagConflict } from '../lib/queries';
import type { EntityKind, ReadFilter, DiscoveryView } from '../lib/queries';
import { apiPost, apiGet, ApiError, type Book, type AdvancedSearchParams } from '../lib/api';
import { formatAuthors } from '../lib/authors';
import { saveCatalog, loadCatalog } from '../lib/scrollCache';
import { usePersistentBool } from '../lib/usePersistentBool';
import { usePersistentChoice } from '../lib/usePersistentChoice';
import { useT } from '../lib/i18n';
import { useAnnouncer } from '../lib/a11y/announcer';
import styles from './Catalog.module.css';
import { canUploadBooks } from '../lib/permissions';

const VIEW_OPTIONS: Record<DiscoveryView, { label: string }> = {
  hot: { label: 'Hot — Most Downloaded' },
  discover: { label: 'Discover — Random Picks' },
  rated: { label: 'Top Rated' },
  favorites: { label: 'Favorites' },
  archived: { label: 'Archived' },
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

// Fork #640 — the plain Library view remembers its sort order and read filter
// across full page reloads (localStorage, per browser). The in-memory scrollCache
// only survives client-side back/forward, so an F5 previously reset sort → "Newest"
// and the read filter → "All". Entity/series/discovery views keep their contextual
// defaults (#573 series-order, #498 saved view) and are intentionally excluded — a
// remembered whole-library sort must not leak into a series or author listing.
const LIBRARY_SORT_KEY = 'cwng:library-sort-v1';
const LIBRARY_READ_FILTER_KEY = 'cwng:library-readfilter-v1';
const LIBRARY_SORT_VALUES = SORT_OPTIONS.map((o) => o.value);
const LIBRARY_READ_FILTER_VALUES: ReadFilter[] = READ_FILTERS.map((f) => f.value);

// A stored choice is honoured only if it is still a valid option; anything else
// (a stale value, a foreign key, disabled storage) safely falls back to undefined
// so the caller uses the contextual default.
function readStoredChoice<T extends string>(key: string, allowed: readonly T[]): T | undefined {
  try {
    const stored = localStorage.getItem(key) as T | null;
    return stored && allowed.includes(stored) ? stored : undefined;
  } catch { return undefined; }
}

const KIND_OPTIONS: Record<EntityKind, { label: string }> = {
  author: { label: 'Author' },
  series: { label: 'Series' },
  tag: { label: 'Tag' },
  publisher: { label: 'Publisher' },
  language: { label: 'Language' },
  rating: { label: 'Rating' },
  format: { label: 'Format' },
};

// Human-facing plural labels are deliberately separate from ENTITY_PLURAL:
// route segments such as "authors" are identifiers, not gettext msgids.
const KIND_PLURAL_OPTIONS: Record<EntityKind, { label: string }> = {
  author: { label: 'Authors' },
  series: { label: 'Series' },
  tag: { label: 'Tags' },
  publisher: { label: 'Publishers' },
  language: { label: 'Languages' },
  rating: { label: 'Ratings' },
  format: { label: 'Formats' },
};

const DENSITY_OPTIONS = [
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'compact', label: 'Compact' },
  { value: 'dense', label: 'Dense' },
] as const;

interface CatalogProps {
  /** When set, the catalog is scoped to books linked to this entity. */
  entityKind?: EntityKind;
  entityId?: string | number;
  /** When set, render a fixed discovery view (hot/discover/rated/favorites/archived). */
  view?: DiscoveryView;
  /** The user's saved default library view (#498): an advanced-search filter that
   *  becomes the standing contents of Your Library. It scopes the LIBRARY LISTING
   *  only — this is still the library page, with its heading, actions and Discover
   *  strip (#928). Ignored for entity/discovery views and while a search is active,
   *  which are explicit navigations away from the default view. */
  defaultFilter?: AdvancedSearchParams;
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

export function Catalog({ entityKind, entityId, view, defaultFilter }: CatalogProps) {
  const t = useT();
  const announce = useAnnouncer();
  const libraryRefresh = useLibraryRefresh();
  const filtered = !!entityKind;
  const isView = !!view;
  // "Show all books" — a per-visit escape from the saved default view. A standing
  // filter that hides books with no way out is a trap; clearing it for good stays
  // on the Advanced-search page that set it.
  const [showingAll, setShowingAll] = useState(false);
  const isSeries = entityKind === 'series';
  const renameTag = useRenameTag(entityId ?? '');
  const deleteTag = useDeleteTag(entityId ?? '');
  const [, navigate] = useLocation();
  const [renamingTag, setRenamingTag] = useState(false);
  const [tagNameDraft, setTagNameDraft] = useState('');
  const [tagRenameError, setTagRenameError] = useState('');
  // The tag a rename collided with. Set means "offer to merge", not "failed".
  const [tagMergeTarget, setTagMergeTarget] = useState<TagConflict | null>(null);
  const [deletingTag, setDeletingTag] = useState(false);
  const [tagDeleteError, setTagDeleteError] = useState('');
  // Series views expose two extra series-order options and default to ascending
  // series order so the list reads 1, 2, 3… instead of newest-first (#573).
  const sortOptions = isSeries ? [...SERIES_SORT_OPTIONS, ...SORT_OPTIONS] : SORT_OPTIONS;
  const defaultSort = isSeries ? 'seriesasc' : 'new';
  // Library-only controls (search box, advanced link, read-status filter) are
  // hidden for both entity-scoped and discovery views.
  const hideLibraryControls = filtered || isView;
  // The plain Library tab — the only view whose sort/read-filter is persisted (#640).
  const isPlainLibrary = !filtered && !isView;

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
  const [sort, setSort] = useState(() =>
    snap?.sort
    ?? (isPlainLibrary ? readStoredChoice(LIBRARY_SORT_KEY, LIBRARY_SORT_VALUES) : undefined)
    ?? defaultSort);
  const [readFilter, setReadFilter] = useState<ReadFilter>(() =>
    (snap?.readFilter as ReadFilter)
    ?? (isPlainLibrary ? readStoredChoice(LIBRARY_READ_FILTER_KEY, LIBRARY_READ_FILTER_VALUES) : undefined)
    ?? 'all');

  // Persist the Library view's sort + read filter so a full reload restores them (#640).
  // Scoped to the plain library — entity/discovery views must not overwrite the key.
  useEffect(() => {
    if (!isPlainLibrary) return;
    try { localStorage.setItem(LIBRARY_SORT_KEY, sort); } catch { /* storage can be disabled */ }
  }, [isPlainLibrary, sort]);
  useEffect(() => {
    if (!isPlainLibrary) return;
    try { localStorage.setItem(LIBRARY_READ_FILTER_KEY, readFilter); } catch { /* storage can be disabled */ }
  }, [isPlainLibrary, readFilter]);

  // Is the saved default view (#498) driving this listing? It scopes the plain
  // library only — an entity or discovery view, or a search, is an explicit
  // navigation out of it, and the saved filter carries no free-text field to
  // intersect a search with anyway. Declared here because resetKey below is part
  // of the filter identity.
  const filterActive = !!defaultFilter && !filtered && !isView && !search && !showingAll;

  // Multi-select / bulk mode
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Quick-edit pencil on cards (fork #572) — only for users who can edit, and
  // never while multi-selecting (the whole card toggles selection then).
  const me = useMe().data;
  const canEdit = !!me?.role?.edit;
  const canRenameTag = entityKind === 'tag' && canEdit;
  // #1288: the role is only half the gate — classic also requires the admin's
  // "Enable Uploads" switch. See lib/permissions.ts.
  const canUpload = canUploadBooks(me);

  // Discover section visibility (persisted; toggled by the gear menu or its ×).
  const [discoverHidden, setDiscoverHidden] = usePersistentBool('cwng_discover_hidden_v1', false);
  const [showHidden, setShowHidden] = usePersistentBool('cwng_show_hidden_books_v1', false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [density, setDensity] = usePersistentChoice(
    'cwng:catalog-density-v1', ['comfortable', 'compact', 'dense'] as const, 'compact');
  const [rowsChoice, setRowsChoice] = usePersistentChoice(
    'cwng:catalog-rows-v1', ['1', '2', '3', '4', '5', '6'] as const, '2');
  const rowsPerLoad = Number(rowsChoice);
  const [gridNode, setGridNode] = useState<HTMLDivElement | null>(null);
  const fallbackPerPage = me?.display?.books_per_page && me.display.books_per_page > 0
    ? me.display.books_per_page : 24;
  // columnCount starts as a GUESS derived from books_per_page; the real value is
  // measured off the rendered grid below. The guess and the measurement rarely
  // agree, so page 1 used to be fetched twice — once at the guessed size, then
  // again once the measurement landed, with the first response thrown away
  // (#1144). gridMeasured gates the query so only the measured size is ever
  // requested; the guess survives only as the fail-open fallback.
  const [columnCount, setColumnCount] = useState(() => Math.max(1, Math.ceil(fallbackPerPage / rowsPerLoad)));
  const [gridMeasured, setGridMeasured] = useState(false);
  const perPage = rowsPerLoad * columnCount;
  const [seriesPresentation, setSeriesPresentation] = usePersistentChoice(
    'cwng:series-presentation-v1', ['grid', 'list'] as const, 'grid');
  const settingsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!gridNode) return;
    const measure = () => {
      const tracks = getComputedStyle(gridNode).gridTemplateColumns.trim();
      // An empty or 'none' track list means the grid has not been laid out yet
      // (a hidden ancestor, a panel mid-transition), which is the absence of a
      // measurement rather than a measurement of one column. Releasing the gate
      // on it would query at rowsPerLoad x 1 and then correct once the real
      // layout arrived — reinstating the double fetch this gate exists to stop.
      // Leave gridMeasured false and let the next observer callback, or the
      // fail-open timer, resolve it.
      if (!tracks || tracks === 'none') return;
      setColumnCount(Math.max(1, tracks.split(/\s+/).length));
      setGridMeasured(true);
    };
    // Measure first, observe second. Without a ResizeObserver the grid stops
    // reacting to later resizes, but the one measurement that the initial query
    // waits on still happens — the absence of the observer used to skip it
    // entirely, which would now mean waiting out the fail-open timer on every
    // load and then querying at the guessed size anyway.
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(gridNode);
    return () => observer.disconnect();
  }, [gridNode, density]);

  // Fail-open. The measurement needs the grid element to exist, and a first
  // attempt at this gate deadlocked: no data -> no grid -> no observer -> no
  // measurement -> query stays disabled -> no data. Rendering the loading state
  // inside the grid container (below) is what breaks that cycle, but the gate
  // must not be the only thing standing between a user and their library, so
  // any path that fails to measure within a frame falls back to the guess and
  // queries anyway. Worst case is the old redundant fetch; never an empty page.
  useEffect(() => {
    if (gridMeasured) return;
    const timer = setTimeout(() => setGridMeasured(true), 150);
    return () => clearTimeout(timer);
  }, [gridMeasured]);

  const accKeyRef = useRef<string>(snap?.resetKey ?? '');

  // Resolve the entity's display name (for the heading) from its browse list —
  // cached when the user arrives from the browse page, a cheap fetch otherwise.
  const entityListQuery = useEntityList(filtered ? ENTITY_PLURAL[entityKind!] : '');
  const entityName = filtered
    ? entityListQuery.data?.items.find((e) => String(e.id) === String(entityId))?.name
    : undefined;
  const entityFailed = filtered && !!entityListQuery.error;
  const entityMissing = filtered && !entityListQuery.isPending && !!entityListQuery.data && !entityName;

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

  // The saved default view is part of the filter identity: turning it on/off (or
  // saving a different one) changes which books belong here, so the accumulator
  // must reset rather than append the new set onto the old (#928).
  const resetKey = [search, sort, readFilter, entityKind ?? '', entityId ?? '', view ?? '', perPage, showHidden,
    filterActive ? JSON.stringify(defaultFilter) : ''].join('|');

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
  // Whether this listing's contents are the server's call — a search, an entity
  // page, a discovery view or the saved default filter. Recorded on the snapshot
  // so an edit elsewhere in the app knows whether it may patch a book in place
  // or has to let the view rebuild (#1169). Computed here because this is where
  // the knowledge lives.
  const membershipFiltered = !!search || !!entityKind || !!view || filterActive;
  const persistRef = useRef({ page, books: allBooks, resetKey: accKeyRef.current, search, searchInput, sort, readFilter, membershipFiltered });
  persistRef.current = { page, books: allBooks, resetKey: accKeyRef.current, search, searchInput, sort, readFilter, membershipFiltered };

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

  // A series shown as a list renders no grid, so there is nothing to measure and
  // nothing to wait for — it queries at the guessed size as before. Mirrors the
  // presentation condition on the list branch below; multi-select turns the grid
  // back on, which is why it belongs here too.
  const usesGrid = !(isSeries && seriesPresentation === 'list' && !selecting);
  const gridReady = gridMeasured || !usesGrid;

  // Both hooks are always called (hook order is fixed); exactly one is enabled.
  const booksQuery = useBooks({
    page,
    perPage,
    search,
    sort,
    readFilter,
    entityKind,
    entityId,
    view,
    showHidden: !hideLibraryControls && showHidden,
    enabled: !filterActive && gridReady,
  });
  // The read/unread control is an explicit user action, so it overrides the
  // read_status baked into the saved filter; sort is the library's own.
  const advParams: AdvancedSearchParams | null = filterActive && gridReady
    ? { ...defaultFilter, sort, ...(readFilter !== 'all' ? { read_status: readFilter } : {}) }
    : null;
  const advQuery = useAdvancedSearch(advParams, page, perPage);
  const { data, isLoading, isFetching, isPlaceholderData, error } =
    filterActive ? advQuery : booksQuery;

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
  // A disabled query is not "loading" as far as react-query is concerned, so the
  // pre-measurement render has to be treated as first load explicitly. Without
  // this it falls through to the empty state and flashes "No books here" before
  // the first request is even made.
  const isFirstLoad = (isLoading || !gridReady) && allBooks.length === 0;

  // The observer is a convenience, not the only way to reach another page:
  // this same guarded action also backs the keyboard/AT-visible Load more button.
  const loadMore = useCallback(() => {
    if (hasMore && !isFetching) setPage((p) => p + 1);
  }, [hasMore, isFetching]);

  const sentinelRef = useIntersectionObserver({
    onIntersect: loadMore,
    enabled: hasMore && !isFetching,
  });

  const heading = isView
    ? t(VIEW_OPTIONS[view!].label)
    : filtered
      ? entityFailed
        ? t('Could not load this page')
        : entityMissing
          ? t('Page not found')
          : (entityName ?? '…')
      : t('Your Library');
  const renameTriggerRef = useRef<HTMLButtonElement>(null);
  const closeTagRename = () => {
    setRenamingTag(false);
    setTagMergeTarget(null);
    requestAnimationFrame(() => renameTriggerRef.current?.focus());
  };
  const beginTagRename = () => {
    setTagNameDraft(entityName ?? '');
    setTagRenameError('');
    setTagMergeTarget(null);
    setRenamingTag(true);
  };
  const closeTagDelete = () => {
    setDeletingTag(false);
    setTagDeleteError('');
    requestAnimationFrame(() => renameTriggerRef.current?.focus());
  };
  const tagWriteError = (error: unknown, fallback: string) => {
    if (error instanceof ApiError) {
      const messages: Record<number, string> = {
        400: t('Enter a valid tag name'),
        401: t('You must be signed in'),
        403: t('You are not allowed to edit metadata'),
        404: t('Tag not found'),
      };
      return messages[error.status] ?? fallback;
    }
    return fallback;
  };

  const runTagRename = (next: string, merge?: boolean) => {
    renameTag.mutate({ name: next, merge }, {
      onSuccess: (result) => {
        closeTagRename();
        if (result.merged) {
          announce(t('Merged into {name}', { name: result.name }));
          // The tag this page was scoped to no longer exists — land on the
          // survivor rather than a 404 of our own making.
          navigate(`/tags/${result.id}`);
        } else {
          announce(t('Tag renamed to {name}', { name: result.name }));
        }
      },
      onError: (error) => {
        // A 409 that names the colliding tag is the de-dup case (#973), not a
        // dead end: offer to merge instead of just restating the error.
        const conflict = tagConflictOf(error);
        if (conflict && !merge) { setTagMergeTarget(conflict); setTagRenameError(''); return; }
        setTagMergeTarget(null);
        setTagRenameError(tagWriteError(error, t('Could not rename tag')));
      },
    });
  };

  const submitTagRename = (event: React.FormEvent) => {
    event.preventDefault();
    const next = tagNameDraft.trim();
    setTagMergeTarget(null);
    if (!next) { setTagRenameError(t('Tag name cannot be empty')); return; }
    runTagRename(next);
  };

  const confirmTagDelete = () => {
    deleteTag.mutate(undefined, {
      onSuccess: (result) => {
        setDeletingTag(false);
        announce(t('Deleted tag {name}', { name: result.name }));
        navigate('/tags');
      },
      onError: (error) => setTagDeleteError(tagWriteError(error, t('Could not delete tag'))),
    });
  };
  const countLabel = total > 0
    ? search && !filtered
      ? t('{count} results for "{query}"', { count: total, query: search })
      : t('{count} books', { count: total })
    : '';

  return (
    <main className={styles.container} data-testid="catalog-page">
      {filtered && (
        <Link href={`/${ENTITY_PLURAL[entityKind!]}`} className={styles.back}>
          <ChevronLeft size={16} />
          {t('Show all {items}', { items: t(KIND_PLURAL_OPTIONS[entityKind!].label) })}
        </Link>
      )}

      {/* Say why the library is a subset, and offer the way out. Without this a
          saved filter is indistinguishable from missing books (#928). */}
      {filterActive && (
        <div className={styles.defaultFilterNotice} data-testid="default-filter-notice">
          <SlidersHorizontal size={15} aria-hidden="true" focusable={false} />
          <span>{t('Showing your default library view.')}</span>
          <button type="button" className={styles.defaultFilterShowAll}
            data-testid="default-filter-show-all"
            onClick={() => { setShowingAll(true); setPage(1); }}>
            {t('Show all books')}
          </button>
          <Link href="/search" className={styles.defaultFilterEdit}>{t('Edit default view')}</Link>
        </div>
      )}

      <div className={styles.header}>
        {filtered && <span className={styles.kindLabel}>{t(KIND_OPTIONS[entityKind!].label)}</span>}
        <h1 className={renamingTag ? 'sr-only' : styles.title}>{heading}</h1>
        {renamingTag ? (
          <form className={styles.renameForm} onSubmit={submitTagRename}>
            <label className="sr-only" htmlFor="tag-name-input">{t('Tag name')}</label>
            <input id="tag-name-input" className={styles.renameInput} value={tagNameDraft} autoFocus
              aria-invalid={!!tagRenameError} aria-describedby={tagRenameError ? 'tag-rename-error' : undefined}
              onKeyDown={(event) => { if (event.key === 'Escape') closeTagRename(); }}
              onChange={(event) => setTagNameDraft(event.target.value)} />
            <button type="submit" className={styles.renameButton} disabled={renameTag.isPending}
              aria-label={t('Save tag name')}><Check size={18} aria-hidden="true" focusable={false} /></button>
            <button type="button" className={styles.renameButton} onClick={closeTagRename}
              aria-label={t('Cancel')}><X size={18} aria-hidden="true" focusable={false} /></button>
            {tagRenameError && <span id="tag-rename-error" className={styles.renameError} role="alert">{tagRenameError}</span>}
            {/* #973: renaming a tag onto its near-duplicate IS the de-dup the
                reporter wants. A bare "already exists" error is a dead end, so
                offer the merge the server will accept. */}
            {tagMergeTarget && (
              <div className={styles.mergePrompt} role="alert">
                <span>{tagMergeTarget.count === 1
                  ? t('“{name}” already exists on {count} book. Merge this tag into it?',
                    { name: tagMergeTarget.name, count: tagMergeTarget.count })
                  : t('“{name}” already exists on {count} books. Merge this tag into it?',
                    { name: tagMergeTarget.name, count: tagMergeTarget.count })}</span>
                <button type="button" className={styles.renameButton} disabled={renameTag.isPending}
                  onClick={() => runTagRename(tagMergeTarget.name, true)}
                  aria-label={t('Merge into {name}', { name: tagMergeTarget.name })}>
                  <Merge size={16} aria-hidden="true" focusable={false} />
                  <span className={styles.confirmLabel}>{t('Merge')}</span>
                </button>
                <button type="button" className={styles.renameButton} onClick={() => setTagMergeTarget(null)}
                  aria-label={t('Cancel')}><X size={16} aria-hidden="true" focusable={false} /></button>
              </div>
            )}
          </form>
        ) : deletingTag ? (
          <div className={styles.mergePrompt} role="alert">
            <span>{total === 1
              ? t('Delete “{name}”? It is removed from {count} book, which is kept.', { name: entityName ?? '', count: total })
              : t('Delete “{name}”? It is removed from {count} books, which are kept.', { name: entityName ?? '', count: total })}</span>
            <button type="button" className={styles.dangerButton} disabled={deleteTag.isPending}
              onClick={confirmTagDelete} aria-label={t('Confirm delete tag {name}', { name: entityName ?? '' })}>
              <Trash2 size={16} aria-hidden="true" focusable={false} />
              <span className={styles.confirmLabel}>{t('Delete')}</span>
            </button>
            <button type="button" className={styles.renameButton} onClick={closeTagDelete}
              aria-label={t('Cancel')}><X size={16} aria-hidden="true" focusable={false} /></button>
            {tagDeleteError && <span className={styles.renameError} role="alert">{tagDeleteError}</span>}
          </div>
        ) : (
          canRenameTag && entityName ? (
            <>
              <button ref={renameTriggerRef} type="button" className={styles.renameButton} onClick={beginTagRename}
                aria-label={t('Rename tag {name}', { name: entityName })}>
                <Pencil size={16} aria-hidden="true" focusable={false} />
              </button>
              {/* Two-step: this button only opens the confirm above (#973). */}
              <button type="button" className={styles.renameButton} onClick={() => { setTagDeleteError(''); setDeletingTag(true); }}
                aria-label={t('Delete tag {name}', { name: entityName })}>
                <Trash2 size={16} aria-hidden="true" focusable={false} />
              </button>
            </>
          ) : null
        )}
        {/* role=status so the result count is announced when filters/search
            change it and when load-more grows it (SC 4.1.3). */}
        {countLabel && <span className={styles.count} role="status" data-testid="catalog-count">{countLabel}</span>}
        {isSeries && (
          <div className={styles.viewToggle} role="group" aria-label={t('Series view')}>
            <button type="button" onClick={() => setSeriesPresentation('grid')}
              aria-pressed={seriesPresentation === 'grid'} aria-label={t('Grid view')}>
              <LayoutGrid size={17} aria-hidden="true" focusable={false} />
            </button>
            <button type="button" onClick={() => setSeriesPresentation('list')}
              aria-pressed={seriesPresentation === 'list'} aria-label={t('List view')}>
              <List size={17} aria-hidden="true" focusable={false} />
            </button>
          </div>
        )}
      </div>

      {/* Toolbar */}
      <div className={styles.toolbar}>
        {/* #1288: Upload is a library-wide ACTION, not one of the view-scoped
            controls hideLibraryControls exists to hide (search box, Advanced,
            read-status filter). Gating it there made it vanish on every entity
            and discovery view, leaving no upload affordance outside the plain
            Library route — classic keeps its navbar button on every page. */}
        {canUpload && (
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
              data-testid="catalog-view-settings"
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
              <div className={styles.settingsMenu} data-testid="catalog-view-settings-menu">
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
                {!me?.role?.anonymous && (
                  <label className={styles.settingsItem}>
                    <input
                      type="checkbox"
                      data-testid="show-hidden-books"
                      className={styles.settingsCheck}
                      checked={showHidden}
                      onChange={(e) => setShowHidden(e.target.checked)}
                    />
                    <span>{t('Show hidden books')}</span>
                  </label>
                )}
                <fieldset className={styles.densityField}>
                  <legend>{t('Book density')}</legend>
                  {DENSITY_OPTIONS.map((option) => (
                    <label key={option.value} className={styles.settingsItem}>
                      <input type="radio" name="book-density" value={option.value}
                        checked={density === option.value} onChange={() => setDensity(option.value)} />
                      <span>{t(option.label)}</span>
                    </label>
                  ))}
                </fieldset>
                <fieldset className={styles.densityField}>
                  <legend>{t('Rows per load')}</legend>
                  {(['1', '2', '3', '4', '5', '6'] as const).map((choice) => (
                    <label key={choice} className={styles.settingsItem}>
                      <input type="radio" name="catalog-rows" value={choice}
                        checked={rowsChoice === choice} onChange={() => setRowsChoice(choice)} />
                      <span>{choice}</span>
                    </label>
                  ))}
                </fieldset>
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
        // The loading state renders INSIDE the grid container rather than in
        // place of it. The column measurement reads gridTemplateColumns off this
        // element, and a CSS grid reports its tracks even with no cards in it —
        // so having it on the first paint is what lets the very first query use
        // the real column count instead of a guess (#1144).
        <div ref={setGridNode} className={`${styles.grid} ${styles[`density_${density}`]}`}>
          <div className={styles.gridLoading}>
            <SpinnerCentered size={36} />
          </div>
        </div>
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
          {isSeries && seriesPresentation === 'list' && !selecting ? (
            <ul className={styles.bookList} role="list">
              {allBooks.map((book) => (
                <li key={book.id}>
                  <Link href={`/book/${book.id}`} className={styles.bookListItem}
                    aria-label={t('Open details for {title}', { title: book.title })}>
                    <span className={styles.bookListCover}>
                      <BookCover coverUrl={book.cover_url} title={book.title} authors={book.authors} />
                    </span>
                    <span className={styles.bookListInfo}>
                      <strong>{book.title}</strong>
                      <span>{formatAuthors(book.authors)}</span>
                    </span>
                    {book.series_index != null && (
                      <span className={styles.bookListIndex}>
                        {t('Book {number}', { number: Number.isInteger(book.series_index) ? book.series_index : String(book.series_index) })}
                      </span>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
          <div ref={setGridNode} className={`${styles.grid} ${styles[`density_${density}`]}`}>
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
          )}

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
