/*
 * Search inside an open book.
 *
 * Neither reader has ever had this: the classic reader's search box, nav link
 * and results pane are all commented out in `read.html`, and there is no search
 * JavaScript in the tree at all — so there was nothing to port. epub.js provides
 * the per-section primitive; the work is doing it without melting the tab.
 *
 * WHY THIS IS NOT A ONE-LINER OVER `spine.each()`:
 *
 *  - **Memory.** Searching means loading each section's document. A novel has
 *    hundreds. Loading them all — which is what mapping over the spine does —
 *    holds every parsed DOM at once. Each section is unloaded immediately after
 *    it is searched, so peak cost is one section, not the book.
 *  - **Cancellation.** A reader types, and each keystroke would otherwise start
 *    a full-book scan that keeps running after its results are irrelevant. Every
 *    await checks the signal, so an abandoned search stops at the next section
 *    instead of competing with the one the reader actually wants.
 *  - **Bounding.** "the" in a long book matches thousands of times. Past a
 *    couple of hundred hits the list is not usable and the cost is real, so it
 *    stops and says it stopped rather than pretending it found everything.
 *
 * Dependencies are the book object's own shape rather than an epub.js import, so
 * this is unit-testable against a fake spine — the same reason `create_annotation`
 * takes its session and commit explicitly.
 */

/** One match: an EPUB CFI to navigate to, and the text around it. */
export interface SearchHit {
  cfi: string;
  excerpt: string;
  /** Spine href, so a result can name its chapter. */
  href: string;
}

export interface SearchOutcome {
  hits: SearchHit[];
  /** True when the cap stopped the scan early, so the UI can say so rather than
   *  implying these are all the matches in the book. */
  truncated: boolean;
  /** How many sections were actually searched — the honest denominator for a
   *  cancelled or truncated run. */
  sectionsSearched: number;
}

/** The slice of epub.js's Section this needs. Narrow on purpose: a fake in a
 *  test implements three members rather than a book. */
export interface SearchableSection {
  href: string;
  load: (request: unknown) => Promise<unknown>;
  unload: () => void;
  search?: (query: string, maxSeqEle?: number) => { cfi: string; excerpt: string }[];
  find?: (query: string) => { cfi: string; excerpt: string }[];
}

export interface SearchableBook {
  spine: { spineItems: SearchableSection[] };
  load: { bind: (book: unknown) => unknown };
}

export const MIN_QUERY_LENGTH = 2;
export const DEFAULT_HIT_CAP = 200;

/**
 * Search every section of `book` for `query`.
 *
 * Resolves with whatever was found before the cap, the signal, or the end of the
 * book — whichever comes first. Never rejects for an unreadable section: one
 * corrupt chapter should cost that chapter's results, not the whole search.
 */
export async function searchBook(
  book: SearchableBook,
  query: string,
  opts: { signal?: AbortSignal; cap?: number } = {},
): Promise<SearchOutcome> {
  const trimmed = query.trim();
  const cap = opts.cap ?? DEFAULT_HIT_CAP;
  const empty: SearchOutcome = { hits: [], truncated: false, sectionsSearched: 0 };

  // A one-character query matches essentially every section and is never what
  // the reader meant; treat it as "nothing typed yet" rather than as a search
  // that found the whole book.
  if (trimmed.length < MIN_QUERY_LENGTH) return empty;

  const sections = book?.spine?.spineItems ?? [];
  const hits: SearchHit[] = [];
  let sectionsSearched = 0;
  let truncated = false;

  for (const section of sections) {
    if (opts.signal?.aborted) break;
    if (hits.length >= cap) { truncated = true; break; }

    try {
      await section.load(book.load.bind(book));
      // `search` handles a phrase that spans element boundaries; `find` is the
      // exact-substring fallback for environments without a TreeWalker. Prefer
      // the former and degrade rather than throwing.
      const raw = section.search
        ? section.search(trimmed)
        : section.find
          ? section.find(trimmed)
          : [];
      for (const match of raw) {
        if (hits.length >= cap) { truncated = true; break; }
        hits.push({ cfi: match.cfi, excerpt: match.excerpt, href: section.href });
      }
      sectionsSearched += 1;
    } catch {
      // A section that will not parse costs its own results and nothing else.
      sectionsSearched += 1;
    } finally {
      // Unload on every exit from the block.
      //
      // Defensive rather than load-bearing as the code stands: the catch above
      // swallows the throw, so control reaches here either way, and no test can
      // currently distinguish `finally` from a plain statement after the block
      // (checked by mutation). It stays because the first `return` or rethrow
      // added inside that try would make it the difference between unloading and
      // leaking a parsed DOM per section -- and a leak is silent.
      try { section.unload(); } catch { /* already gone */ }
    }
  }

  return { hits, truncated, sectionsSearched };
}
