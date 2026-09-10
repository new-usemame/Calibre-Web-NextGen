import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import type { useBulkActions } from '../src/lib/queries';

// Exercise real hooks and API parsing with only the HTTP transport replaced.
test('lost bulk responses refresh committed removals and preserve unknown IDs for retry', async () => {
  const directory = await mkdtemp(fileURLToPath(new URL('../.query-test-', import.meta.url)));
  const originalFetch = globalThis.fetch;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  let unsubscribe = () => {};
  try {
    const outfile = `${directory}/queries.mjs`;
    await build({ entryPoints: [fileURLToPath(new URL('../src/lib/queries.ts', import.meta.url))], outfile, bundle: true, packages: 'external', platform: 'node', format: 'esm' });
    const hooks = await import(pathToFileURL(outfile).href) as { useBulkActions: typeof useBulkActions };
    let actions!: ReturnType<typeof useBulkActions>;
    function Capture() { actions = hooks.useBulkActions(); return null; }
    renderToString(createElement(QueryClientProvider, { client }, createElement(Capture)));
    let serverBooks = [11, 12];
    let loseResponse = true;
    globalThis.fetch = async (input, init) => {
      const path = String(input);
      if (path.endsWith('/auth/csrf')) return Response.json({ csrf_token: 'test-token' });
      if (path.endsWith('/my-library/batch')) {
        assert.equal(init?.method, 'POST');
        assert.deepEqual(JSON.parse(String(init?.body)).book_ids, [11, 12]);
        serverBooks = [12]; // Book 11 commits; policy rejects book 12.
        if (loseResponse) throw new TypeError('response connection lost');
        return Response.json({ succeeded_ids: [11], failed_ids: [12], results: [
          { book_id: 11, status: 'succeeded' },
          { book_id: 12, status: 'failed', error: { code: 'library_membership_rejected', message: 'Keep the final book' } },
        ] });
      }
      throw new Error(`Unexpected HTTP request: ${path}`);
    };
    const key = ['books', 'library', 1];
    client.setQueryData(key, [11, 12]);
    const observer = new QueryObserver(client, { queryKey: key, queryFn: async () => [...serverBooks], staleTime: Infinity });
    unsubscribe = observer.subscribe(() => {});
    const unknown = await actions.removeFromMyLibrary.mutateAsync([11, 12]);
    assert.deepEqual(unknown.failedIds, [11, 12], 'unknown outcomes remain available for retry');
    assert.deepEqual(client.getQueryData(key), [12], 'committed removals must leave the visible query despite a lost response');
    loseResponse = false;
    const retry = await actions.removeFromMyLibrary.mutateAsync(unknown.failedIds);
    assert.deepEqual(retry.succeededIds, [11]);
    assert.deepEqual(retry.failedIds, [12], 'the rejected book remains available for another retry');
    assert.deepEqual(client.getQueryData(key), [12]);
  } finally {
    unsubscribe(); client.clear(); globalThis.fetch = originalFetch;
    await rm(directory, { recursive: true, force: true });
  }
});
