// Single source of truth for the customizable sidebar entries (#585 v2), shared
// by Sidebar.tsx (live render) and SidebarCustomize.tsx (the editor). The stable
// `key` matches the server's visibility + order keys (cps/api/serializers.py:
// SIDEBAR_VISIBILITY_BITS / ORDERABLE_SIDEBAR_KEYS). Library / Upload / Admin /
// Table / Smart-shelves / Duplicates / Tasks / About are pinned and NOT here.
import {
  Users, Layers, Tag, Building2, Languages, Star, FileType,
  Flame, Shuffle, Archive, BookCopy,
} from 'lucide-react';

export interface SidebarEntryDef {
  /** Stable key — matches server visibility/order keys. */
  key: string;
  href: string;
  /** English label; wrapped with t() at render time. */
  label: string;
  icon: typeof Users;
  /** exact-match highlight (discovery views) vs prefix-match (browse-by lists,
   *  so /authors/5 still highlights Authors). Preserves pre-#585 behavior. */
  exact?: boolean;
  /** true when the entry is the Shelves block (header + shelf list), which is
   *  always visible (only movable) — no visibility toggle. */
  isShelvesBlock?: boolean;
}

export const ORDERABLE_ENTRIES: SidebarEntryDef[] = [
  { key: 'author', href: '/authors', label: 'Authors', icon: Users },
  { key: 'series', href: '/series', label: 'Series', icon: Layers },
  { key: 'category', href: '/tags', label: 'Tags', icon: Tag },
  { key: 'publisher', href: '/publishers', label: 'Publishers', icon: Building2 },
  { key: 'language', href: '/languages', label: 'Languages', icon: Languages },
  { key: 'rating', href: '/ratings', label: 'Ratings', icon: Star },
  { key: 'format', href: '/formats', label: 'Formats', icon: FileType },
  { key: 'favorites', href: '/favorites', label: 'Favorites', icon: Star, exact: true },
  { key: 'hot', href: '/hot', label: 'Hot', icon: Flame, exact: true },
  { key: 'random', href: '/discover', label: 'Discover', icon: Shuffle, exact: true },
  { key: 'best_rated', href: '/rated', label: 'Top Rated', icon: Star, exact: true },
  { key: 'archived', href: '/archived', label: 'Archived', icon: Archive, exact: true },
  { key: 'shelves', href: '/shelves', label: 'Shelves', icon: BookCopy, exact: true, isShelvesBlock: true },
];

export const DEFAULT_SIDEBAR_ORDER: string[] = ORDERABLE_ENTRIES.map((e) => e.key);

const ENTRY_BY_KEY = new Map(ORDERABLE_ENTRIES.map((e) => [e.key, e]));

/** Resolve the effective ordered entry list from a saved order: keep known keys
 *  in saved order, then append any entries missing from the saved order in their
 *  natural position (so a newly-added nav entry never vanishes for a user who
 *  saved an order before it existed). Ignores unknown keys defensively. */
export function resolveSidebarOrder(saved: string[] | undefined | null): SidebarEntryDef[] {
  const savedList = Array.isArray(saved) ? saved : [];
  const seen = new Set<string>();
  const out: SidebarEntryDef[] = [];
  for (const key of savedList) {
    const e = ENTRY_BY_KEY.get(key);
    if (e && !seen.has(key)) {
      out.push(e);
      seen.add(key);
    }
  }
  for (const e of ORDERABLE_ENTRIES) {
    if (!seen.has(e.key)) out.push(e);
  }
  return out;
}
