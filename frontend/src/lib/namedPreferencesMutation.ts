import type { QueryClient } from '@tanstack/react-query';
import type { Me } from './api.ts';
import { cachedIdentityGeneration } from './identityCache.ts';

export interface NamedPreferencesUpdate {
  preferences: Record<string, boolean>;
  ownerId: number | null;
  generation: number;
}
export function captureNamedPreferencesOwner(client: QueryClient) {
  return { ownerId: client.getQueryData<Me | null>(['me'])?.id ?? null,
    generation: cachedIdentityGeneration(client) };
}
export function namedPreferencesMutationOptions(
  client: QueryClient,
  post: (update: NamedPreferencesUpdate) => Promise<{ preferences: Record<string, boolean | null> }>,
) {
  const owns = (update: NamedPreferencesUpdate) => update.ownerId !== null
    && client.getQueryData<Me | null>(['me'])?.id === update.ownerId
    && cachedIdentityGeneration(client) === update.generation;
  return {
    scope: { id: 'named-user-preferences' },
    mutationFn: (update: NamedPreferencesUpdate) => {
      if (!owns(update)) throw new Error('Account changed before preferences could be saved');
      return post(update);
    },
    onMutate: async (update: NamedPreferencesUpdate) => {
      if (!owns(update)) throw new Error('Account changed before preferences could be saved');
      const previous = client.getQueryData<Me | null>(['me']);
      // Publish the controlled checkbox value before pending state can render.
      client.setQueryData<Me | null>(['me'], current => current && owns(update) ? {
        ...current, preferences: { ...(current.preferences ?? {}), ...update.preferences },
      } : current);
      await client.cancelQueries({ queryKey: ['me'] });
      return { previous };
    },
    onError: (_error: unknown, update: NamedPreferencesUpdate, context?: { previous: Me | null | undefined }) => {
      if (context && owns(update)) client.setQueryData(['me'], context.previous);
    },
    onSuccess: (data: { preferences: Record<string, boolean | null> }, update: NamedPreferencesUpdate) => {
      if (!owns(update)) return;
      client.setQueryData<Me | null>(['me'], current => current ? {
        ...current, preferences: { ...(current.preferences ?? {}), ...data.preferences },
      } : current);
      if ('share_book_ratings' in update.preferences) void client.invalidateQueries({ queryKey: ['book-rating'] });
    },
    onSettled: (_data: unknown, _error: unknown, update: NamedPreferencesUpdate) => {
      if (owns(update)) void client.invalidateQueries({ queryKey: ['me'] });
    },
  };
}
