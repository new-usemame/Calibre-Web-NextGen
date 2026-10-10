/*
 * Behavioral checks for the Global Library card's membership and read-action
 * presentation. Render the real BookCard with its real React Query context;
 * the permission predicate is also the one used by GlobalLibrary.
 */
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { Router } from 'wouter';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { Book, Me } from '../../src/lib/api.ts';
import { canReadBooks } from '../../src/lib/permissions.ts';
import { getPrimaryReadTarget } from '../../src/lib/readerTarget.ts';

const vite = await createServer({
  root: fileURLToPath(new URL('../..', import.meta.url)),
  logLevel: 'silent',
  appType: 'custom',
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const { BookCard } = await vite.ssrLoadModule('/src/components/BookCard.tsx') as {
  BookCard: (props: Record<string, unknown>) => ReturnType<typeof createElement>;
};

function renderCard({ owned, viewer }: { owned: boolean; viewer: boolean }): string {
  const me = {
    id: 17,
    name: 'Reader',
    locale: 'en',
    theme: 'light',
    role: { viewer, anonymous: false },
  } satisfies Me;
  const book = {
    id: 42,
    title: 'Permission Test Book',
    authors: ['Test Author'],
    series: null,
    series_index: null,
    cover_url: null,
    formats: ['EPUB'],
    in_my_library: owned,
    favorited: false,
  } as Book;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(['me'], me);
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(
        Router,
        { hook: () => ['/', () => undefined] },
        createElement(BookCard, {
          book,
          membership: owned ? 'owned' : 'unowned',
          canRead: owned && canReadBooks(me),
          detailsEnabled: true,
          onAddToLibrary: owned ? undefined : () => undefined,
        }),
      ),
    ),
  );
}

describe('Global Library card permissions', () => {
  test('an owned viewer book exposes its cover actions and a reader target', () => {
    const markup = renderCard({ owned: true, viewer: true });
    assert.match(markup, /aria-label="Actions for \{title\}"/);
    assert.match(markup, /aria-label="In your library"/);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], canReadBooks({ role: { viewer: true } })), '/read/42');
    assert.doesNotMatch(markup, /Add Permission Test Book to my library/);
  });

  test('an owned non-viewer has no reader target', () => {
    const markup = renderCard({ owned: true, viewer: false });
    // Personal status/favorite actions can still be available to an owner; the
    // reader destination is separately gated by the actual viewer role.
    assert.match(markup, /aria-label="Actions for \{title\}"/);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], canReadBooks({ role: { viewer: false } })), null);
  });

  test('an unowned book stays Add-only, even for a viewer', () => {
    const markup = renderCard({ owned: false, viewer: true });
    assert.match(markup, /aria-label="Add \{title\} to my library"/);
    assert.doesNotMatch(markup, /aria-label="Actions for \{title\}"/);
    assert.doesNotMatch(markup, /aria-label="In your library"/);
    assert.equal(getPrimaryReadTarget(42, ['EPUB'], false), null);
  });
});
