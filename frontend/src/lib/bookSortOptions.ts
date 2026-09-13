// "Recent" leads the menu and is what the Library opens on: the books you have
// been reading, newest activity first, then everything you have not read in the
// order it was added. Recency is per reader and resolved server-side across
// every device that reports progress — the web reader, a Kobo, KOReader — so it
// is a sort the client can only ask for, never compute (cps/sort_orders.py).
export const SORT_OPTIONS = [
  { label: 'Recent', value: 'recent' },
  { label: 'Newest', value: 'new' },
  { label: 'Oldest', value: 'old' },
  { label: 'Title A–Z', value: 'abc' },
  { label: 'Title Z–A', value: 'zyx' },
  { label: 'Author A–Z', value: 'authaz' },
  { label: 'Author Z–A', value: 'authza' },
  { label: 'Newest published', value: 'pubnew' },
  { label: 'Oldest published', value: 'pubold' },
] as const;

export type BookSortValue = (typeof SORT_OPTIONS)[number]['value'];

/** What the plain Library view opens on when the reader has expressed no choice. */
export const DEFAULT_LIBRARY_SORT = 'recent';

/** Where the Library's remembered sort lives (#640), per browser. */
export const LIBRARY_SORT_KEY = 'cwng:library-sort-v2';

// v1 held the same values but was written by an effect on every mount, not only
// when the reader used the menu — so it says "this is what the view was showing"
// rather than "this is what I chose", and every install that has ever opened the
// Library holds one. Reading it as a choice would mean this default reached
// nobody who already uses the app.
export const LIBRARY_SORT_KEY_LEGACY = 'cwng:library-sort-v1';

/** What v1 seeded itself with, and therefore the one value it cannot vouch for. */
const LEGACY_SELF_SEEDED_SORT = 'new';

/**
 * The reader's own sort choice, or `undefined` to use the contextual default.
 *
 * `stored` is this build's key and is always a real choice. `legacy` is v1, which
 * is only evidence of a choice where it differs from the value it wrote itself.
 * Anything no longer offered (a stale key, a series-only sort, disabled storage)
 * is discarded rather than passed to the API.
 */
export function resolveLibrarySort(
  stored: string | null | undefined,
  legacy: string | null | undefined,
  allowed: readonly string[],
): string | undefined {
  if (stored && allowed.includes(stored)) return stored;
  if (legacy && legacy !== LEGACY_SELF_SEEDED_SORT && allowed.includes(legacy)) return legacy;
  return undefined;
}
