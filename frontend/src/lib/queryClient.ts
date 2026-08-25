import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';

export function createQueryClient(handlers: { onError: (err: unknown) => void }): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handlers.onError }),
    mutationCache: new MutationCache({ onError: handlers.onError }),
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
      },
    },
  });
}
