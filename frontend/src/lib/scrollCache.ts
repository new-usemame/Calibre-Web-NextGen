/* In-memory catalog scroll/state cache.
 *
 * The library/browse grid is an accumulating "Load more" list scrolled on the
 * window. When you open a book and press Back, wouter remounts the catalog fresh
 * — losing the loaded pages and the scroll position (#578). Browsers can't
 * restore scroll here because the content is fetched client-side and isn't in the
 * DOM at restore time.
 *
 * So we stash the catalog's render-relevant state (loaded pages + filters +
 * scrollY) in a module-level Map keyed by route, and rehydrate it on remount.
 * It's in-memory (not sessionStorage): it survives client-side back/forward
 * within the SPA session — the exact case that broke — without serialization
 * cost or storage limits, and is intentionally dropped on a full page reload
 * (where a top-of-page start is expected). Bounded so a long session can't grow
 * it without limit.
 */
import type { Book } from './api';

export interface CatalogSnapshot {
  resetKey: string;      // filter/sort signature the pages were loaded under
  page: number;          // highest page loaded
  books: Book[];         // the accumulated grid
  scrollY: number;       // window scroll offset when the user left
  search: string;
  searchInput: string;
  sort: string;
  readFilter: string;
  /** True when only the server can decide what belongs in this listing — a
   *  search, an entity page, a discovery view, or the saved default filter.
   *  An edit can change membership there, so the snapshot is dropped rather
   *  than patched (see applyBookEditToCache). */
  membershipFiltered: boolean;
}

const _cache = new Map<string, CatalogSnapshot>();
const _MAX = 12;         // keep the dozen most-recent catalog views

export function saveCatalog(key: string, snap: CatalogSnapshot): void {
  // Refresh recency (Map preserves insertion order → re-insert to move to end).
  _cache.delete(key);
  _cache.set(key, snap);
  while (_cache.size > _MAX) {
    const oldest = _cache.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    _cache.delete(oldest);
  }
}

export function loadCatalog(key: string): CatalogSnapshot | undefined {
  return _cache.get(key);
}

/** Drop a book from every cached snapshot — call when a book is deleted so a
 *  later scroll-restore can't resurrect it as a ghost card that 404s on click
 *  (#578). A re-fetch would still contain it on pages we don't re-request, so we
 *  evict it from the snapshots directly. */
export function removeBookFromCache(id: number): void {
  for (const snap of _cache.values()) {
    const filtered = snap.books.filter((b) => b.id !== id);
    if (filtered.length !== snap.books.length) snap.books = filtered;
  }
}

/** Apply a metadata EDIT to the cached snapshots. An edit is not a delete: the
 *  book still exists, so removeBookFromCache is the wrong tool for it (#1169).
 *  Evicting an edited book leaves it to the refetch to reappear, and the grid's
 *  merge only upserts or appends — so a book that was first in the listing came
 *  back as the last card of everything loaded, which from the top of the grid
 *  reads as "it disappeared".
 *
 *  Where membership is fixed (the plain library), patch the book in place: the
 *  card shows the edit immediately and keeps its position, including on pages
 *  the catalog won't re-request. Where only the server can decide membership
 *  (search / entity / discovery / saved filter), the edit may genuinely have
 *  moved the book out of the listing and no client-side merge can tell — drop
 *  that snapshot so the view rebuilds from page 1 on the next visit. */
export function applyBookEditToCache(id: number, patch: Partial<Book>): void {
  for (const [key, snap] of [..._cache]) {
    if (snap.membershipFiltered) {
      _cache.delete(key);
      continue;
    }
    const current = snap.books.find((b) => b.id === id);
    if (!current) continue;
    // Patching in place keeps the card where the user left it, which is right
    // only while the edit can't have moved it. Sorting by a field the edit
    // changed does move it, and an upsert can't relocate a row — so drop the
    // snapshot and let the next visit rebuild in the server's order.
    if (reordersUnder(snap.sort, current, patch)) {
      _cache.delete(key);
      continue;
    }
    snap.books = snap.books.map((b) => (b.id === id ? { ...b, ...patch } : b));
  }
}

/** Which list-item field each sort is keyed on. Sorts that are absent here —
 *  date added (the default), and anything not derived from editable metadata —
 *  can't be perturbed by a metadata edit. */
const SORT_KEY_FIELDS: Record<string, readonly (keyof Book)[]> = {
  abc: ['title'], zyx: ['title'],
  authaz: ['authors'], authza: ['authors'],
  seriesasc: ['series', 'series_index'], seriesdesc: ['series', 'series_index'],
};

/** Sorts keyed on a field the list item doesn't carry, so "did it change?" is
 *  unanswerable here. Publication date is editable, so assume it moved. */
const OPAQUE_SORT_KEYS = new Set(['pubnew', 'pubold']);

function reordersUnder(sort: string, current: Book, patch: Partial<Book>): boolean {
  if (OPAQUE_SORT_KEYS.has(sort)) return true;
  return (SORT_KEY_FIELDS[sort] ?? []).some((field) => {
    if (!(field in patch)) return false;
    const before = current[field];
    const after = patch[field];
    if (Array.isArray(before) || Array.isArray(after)) {
      const a = Array.isArray(before) ? before : [];
      const b = Array.isArray(after) ? after : [];
      return a.length !== b.length || a.some((v, i) => v !== b[i]);
    }
    return before !== after;
  });
}
