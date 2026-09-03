import type { QueryClient } from '@tanstack/react-query';

import { clearCatalogCache } from './scrollCache.ts';

/**
 * Replace the cached session identity without ever pairing the incoming user
 * with data fetched for the outgoing user.
 *
 * Query keys throughout the SPA predate account switching and are intentionally
 * compact (`books`, `shelves`, `notices`, …), so the identity boundary is the
 * cache generation rather than a hand-maintained list of roots. Keep `me`
 * present as `null` while every other query is cancelled and removed: App then
 * renders the logged-out tree until the new generation is ready, and an old
 * in-flight response cannot repopulate the cache after the switch.
 */
export async function replaceCachedIdentity<T>(
  queryClient: QueryClient,
  nextIdentity: T,
): Promise<void> {
  queryClient.setQueryData(['me'], null);

  const isIdentityData = (query: { queryKey: readonly unknown[] }) =>
    query.queryKey[0] !== 'me';
  await queryClient.cancelQueries({ predicate: isIdentityData });
  queryClient.removeQueries({ predicate: isIdentityData });
  clearCatalogCache();

  queryClient.setQueryData(['me'], nextIdentity);
}
