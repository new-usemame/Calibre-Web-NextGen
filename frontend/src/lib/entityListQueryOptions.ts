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
