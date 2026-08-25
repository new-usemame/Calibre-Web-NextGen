/*
 * Regression coverage for the SPA-wide React Query configuration.
 *
 * Run the production configuration:
 *   NODE_OPTIONS=--experimental-strip-types node --test frontend/tests/unit/queryClient.test.ts
 *
 * Reproduce the pre-fix configuration (expected to fail):
 *   QUERY_CLIENT_TEST_PRE_FIX=1 NODE_OPTIONS=--experimental-strip-types \
 *     node --test frontend/tests/unit/queryClient.test.ts
 */
import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { QueryClient } from '@tanstack/react-query';

import { createQueryClient } from '../../src/lib/queryClient.ts';

describe('createQueryClient', () => {
  test('disables refetching cached queries when the window regains focus', () => {
    const client = process.env.QUERY_CLIENT_TEST_PRE_FIX === '1'
      ? new QueryClient({})
      : createQueryClient({ onError: () => undefined });

    assert.equal(client.getDefaultOptions().queries?.refetchOnWindowFocus, false);
  });

  test('routes query and mutation cache failures to the supplied handler', async () => {
    const queryError = new Error('query failed');
    const mutationError = new Error('mutation failed');
    const handled: unknown[] = [];
    const client = createQueryClient({ onError: (err) => handled.push(err) });

    await assert.rejects(
      client.fetchQuery({
        queryKey: ['query-client-test'],
        queryFn: async () => { throw queryError; },
      }),
      queryError,
    );

    const mutation = client.getMutationCache().build(client, {
      mutationFn: async () => { throw mutationError; },
    });
    await assert.rejects(mutation.execute(undefined), mutationError);

    assert.deepEqual(handled, [queryError, mutationError]);
  });
});
