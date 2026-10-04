import type { QueryClient } from '@tanstack/react-query';
import { apiGet, type BookDetail } from './api';
import { refreshCatalogCover } from './scrollCache';

const COVER_LISTS = new Set([
  'books', 'global-library', 'adv-search', 'shelf', 'magicshelf',
  'cc-books', 'discover-strip',
]);
const COVER_DETAILS = new Set(['book', 'book-meta', 'cover-state']);

/** A committed cover change must reach every view that stores its old URL.
 * Inactive pages become stale even within their normal cache lifetime; active
 * pages refetch. Use the server's versioned URLs rather than inventing a client
 * timestamp or changing reading-progress state. */
export async function refreshBookCoverViews(client: QueryClient, bookId: string | number): Promise<void> {
  await Promise.all([client.invalidateQueries({ predicate: (query) => {
    const kind = String(query.queryKey[0]);
    return COVER_LISTS.has(kind)
      || (COVER_DETAILS.has(kind) && String(query.queryKey[1]) === String(bookId));
  } }), refreshCatalogCover(Number(bookId), async () => {
    // Detail's md URL is valid for a card too; retaining the server URL avoids
    // rewriting personal-cover routes or manufacturing cache-version tokens.
    const book = await apiGet<BookDetail>(`/api/v1/books/${bookId}`);
    return book.cover_url;
  })]);
}
