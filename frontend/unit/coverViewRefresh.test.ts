import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToString } from 'react-dom/server';
import { QueryClient, QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import type { useSetCover, useClearMyCover, useCcBooks } from '../src/lib/queries';
import type * as ScrollCache from '../src/lib/scrollCache';
import type { Book } from '../src/lib/api';

for (const action of ['replace library cover', 'clear personal cover', 'rejected cover replacement'] as const) {
  test(`${action} reconciles the cached custom-column page only after a successful write`, async () => {
    // #1126: return to a freshly cached page within its actual 60-second lifetime.
    // The real mutation and browse hooks run; only their HTTP transport is replaced.
    const directory = await mkdtemp(fileURLToPath(new URL('../.cover-query-test-', import.meta.url)));
    const originalFetch = globalThis.fetch;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    let unsubscribe = () => {};
    try {
      const outfile = `${directory}/queries.mjs`;
      await build({ entryPoints: [fileURLToPath(new URL('../src/lib/queries.ts', import.meta.url))], outfile, bundle: true, packages: 'external', platform: 'node', format: 'esm' });
      const hooks = await import(pathToFileURL(outfile).href) as {
        useSetCover: typeof useSetCover; useClearMyCover: typeof useClearMyCover; useCcBooks: typeof useCcBooks;
      };
      let replace!: ReturnType<typeof useSetCover>;
      let clear!: ReturnType<typeof useClearMyCover>;
      let cover = '/cover/5/sm?c=before';
      let reads = 0;
      const key = ['cc-books', '7', 'Art', 1];
      const page = () => ({ items: [{ id: 5, cover_url: cover }], total: 1 });
      client.setQueryData(key, page());
      client.setQueryData(['reader-settings'], { font: 'serif' });
      function Capture() {
        replace = hooks.useSetCover(5); clear = hooks.useClearMyCover(5);
        hooks.useCcBooks(7, 'Art', 1);
        return null;
      }
      renderToString(createElement(QueryClientProvider, { client }, createElement(Capture)));
      globalThis.fetch = async (input, init) => {
        const path = String(input);
        if (path.endsWith('/auth/csrf')) return Response.json({ csrf_token: 'fixture-token' });
        if (path.endsWith('/books/5/cover') || path.endsWith('/books/5/my-cover')) {
          assert.equal(init?.method, action === 'clear personal cover' ? 'DELETE' : 'POST');
          if (action === 'rejected cover replacement') {
            return Response.json({ error: { code: 'locked', message: 'Cover is locked' } }, { status: 409 });
          }
          cover = '/cover/5/sm?c=after';
          return Response.json({ ok: true, cover_url: cover });
        }
        if (path.includes('/columns/7/books?')) { reads++; return Response.json(page()); }
        throw new Error(`Unexpected HTTP request: ${path}`);
      };
      if (action === 'rejected cover replacement') {
        await assert.rejects(replace.mutateAsync({ url: 'https://example.test/cover.jpg' }), /locked/i);
      } else if (action === 'clear personal cover') {
        await clear.mutateAsync();
      } else {
        await replace.mutateAsync({ url: 'https://example.test/cover.jpg' });
      }
      // Reattach using the options created by the actual useCcBooks hook.
      const query = client.getQueryCache().find({ queryKey: key, exact: true });
      assert.ok(query);
      const observer = new QueryObserver(client, { ...query.options, queryKey: key });
      unsubscribe = observer.subscribe(() => {});
      await new Promise<void>((resolve) => setImmediate(resolve));
      const result = observer.getCurrentResult().data as ReturnType<typeof page>;
      assert.equal(result.items[0].cover_url, cover, 'returning to the cached column must show the committed cover version');
      assert.equal(reads, action === 'rejected cover replacement' ? 0 : 1, 'successful writes bypass the old fresh-cache lifetime; rejected writes do not');
      assert.deepEqual(client.getQueryData(['reader-settings']), { font: 'serif' });
      assert.equal(client.getQueryState(['reader-settings'])?.isInvalidated, false);
    } finally {
      unsubscribe(); client.clear(); globalThis.fetch = originalFetch;
      await rm(directory, { recursive: true, force: true });
    }
  });
}

for (const action of ['library with private override', 'clear private cover', 'detail read fails', 'account changes during read', 'Back during read', 'rejected write'] as const) {
  test(`${action}: reconcile earlier accumulated pages without moving cards or crossing accounts`, async () => {
    const directory = await mkdtemp(fileURLToPath(new URL('../.cover-query-test-', import.meta.url)));
    const originalFetch = globalThis.fetch;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    try {
      const outfile = `${directory}/cache.mjs`;
      // Both exports share the same bundled module-level snapshot cache.
      await build({ stdin: { contents: "export * from './src/lib/queries'; export * from './src/lib/scrollCache';", resolveDir: fileURLToPath(new URL('..', import.meta.url)) }, outfile, bundle: true, packages: 'external', platform: 'node', format: 'esm' });
      const hooks = await import(pathToFileURL(outfile).href) as typeof ScrollCache & {
        useSetCover: typeof useSetCover; useClearMyCover: typeof useClearMyCover;
      };
      const snapshot = (id: number): ScrollCache.CatalogSnapshot => ({
        resetKey: 'search|pubnew', page: 3, scrollY: 1420, search: 'Art', searchInput: 'Art',
        sort: 'pubnew', readFilter: 'unread', membershipFiltered: true,
        books: [{ id, title: 'Earlier page', cover_url: '/cover/5/sm?c=before' } as Book,
          { id: 90, title: 'Last page', cover_url: null } as Book],
      });
      const saved = snapshot(5);
      hooks.saveCatalog('affected', saved);
      const unrelated = snapshot(7);
      hooks.saveCatalog('unrelated', unrelated);
      let replace!: ReturnType<typeof useSetCover>;
      let clear!: ReturnType<typeof useClearMyCover>;
      function Capture() { replace = hooks.useSetCover(5); clear = hooks.useClearMyCover(5); return null; }
      renderToString(createElement(QueryClientProvider, { client }, createElement(Capture)));
      let reads = 0;
      let release!: () => void;
      let started!: () => void;
      const readStarted = new Promise<void>((resolve) => { started = resolve; });
      const gate = new Promise<void>((resolve) => { release = resolve; });
      const authoritative = action === 'clear private cover'
        ? '/cover/5/md?c=library-current'
        : '/api/v1/books/5/my-cover/image?c=private-current';
      globalThis.fetch = async (input, init) => {
        const path = String(input);
        if (path.endsWith('/auth/csrf')) return Response.json({ csrf_token: 'fixture-token' });
        if (path.endsWith('/books/5/cover') || path.endsWith('/books/5/my-cover')) {
          assert.equal(init?.method, action === 'clear private cover' ? 'DELETE' : 'POST');
          if (action === 'rejected write') return Response.json({ error: { code: 'locked', message: 'Cover is locked' } }, { status: 409 });
          // A shared response is deliberately unsuitable for a private viewer.
          return Response.json({ ok: true, cover_url: '/cover/5/md?c=shared-new' });
        }
        if (path.endsWith('/books/5')) {
          reads++; started();
          if (action === 'account changes during read' || action === 'Back during read') await gate;
          if (action === 'detail read fails') return Response.json({ error: { code: 'internal', message: 'Read failed' } }, { status: 500 });
          return Response.json({ id: 5, cover_url: authoritative });
        }
        throw new Error(`Unexpected HTTP request: ${path}`);
      };
      const mutation = action === 'clear private cover' ? clear.mutateAsync() : replace.mutateAsync({ url: 'https://example.test/cover.jpg' });
      let replacement: ScrollCache.CatalogSnapshot | undefined;
      if (action === 'account changes during read' || action === 'Back during read') {
        await Promise.race([readStarted, mutation.then(() => {
          assert.fail('a cached earlier page requires a fresh viewer read before mutation reconciliation completes');
        })]);
        if (action === 'account changes during read') hooks.clearCatalogCache();
        replacement = snapshot(5);
        hooks.saveCatalog('affected', replacement);
        release();
      }
      if (action === 'rejected write') await assert.rejects(mutation, /locked/i);
      else await mutation; // A failed detail read cannot turn a committed write into failure.
      if (action === 'detail read fails') {
        assert.equal(hooks.loadCatalog('affected'), undefined);
      } else if (action === 'account changes during read' || action === 'rejected write') {
        assert.equal(hooks.loadCatalog('affected')!.books[0].cover_url, '/cover/5/sm?c=before');
      } else {
        const current = hooks.loadCatalog('affected')!;
        assert.deepEqual(current, { ...(replacement ?? saved), books: [
          { ...(replacement ?? saved).books[0], cover_url: authoritative },
          (replacement ?? saved).books[1],
        ] });
        assert.equal(current.books[0].cover_url, authoritative);
        assert.equal(current.page, 3); assert.equal(current.scrollY, 1420);
      }
      if (action !== 'account changes during read') assert.strictEqual(hooks.loadCatalog('unrelated'), unrelated);
      assert.equal(reads, action === 'rejected write' ? 0 : 1);
    } finally {
      client.clear(); globalThis.fetch = originalFetch;
      await rm(directory, { recursive: true, force: true });
    }
  });
}
