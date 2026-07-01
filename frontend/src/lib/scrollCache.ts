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
