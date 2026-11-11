import type { EntityList } from './api';

export function createEntityListQueryOptions<T>(plural: string, queryFn: () => Promise<T>) {
  return {
    queryKey: ['entities', plural] as const,
    queryFn,
    staleTime: 60000,
    // Catalog intentionally passes an empty plural when no entity filter is
    // active; nothing consumes this query then, so do not fetch the API root.
    enabled: plural !== '',
  };
}
export interface EntityListPage extends EntityList {
  total: number;
  unfiltered_total: number;
  next_offset: number | null;
}

export function createPagedEntityListQueryOptions(
  plural: string, q: string, minimum: number | null, userId: number | undefined,
  fetchPage: (url: string, signal: AbortSignal) => Promise<EntityListPage>, enabled = true,
) {
  const validMinimum = minimum === null || (Number.isSafeInteger(minimum) && minimum >= 1);
  return {
    queryKey: ['entities', plural, 'pages', userId, q, minimum] as const,
    queryFn: ({ pageParam, signal }: { pageParam: number; signal: AbortSignal }) => {
      const params = new URLSearchParams({ limit: '200', offset: String(pageParam) });
      if (q) params.set('q', q);
      if (minimum !== null) params.set('min_count', String(minimum));
      return fetchPage(`/api/v1/${plural}?${params}`, signal);
    },
    initialPageParam: 0,
    getNextPageParam: (lastPage: EntityListPage) => lastPage.next_offset ?? undefined,
    enabled: enabled && validMinimum && (plural === 'authors' || plural === 'series'),
    staleTime: 60000,
  };
}
