/*
 * Component regression tests for fork #1702.
 *
 * The frontend intentionally carries no component-test framework.  Vite's SSR
 * loader (already a build dependency) loads the real TSX/CSS-module graph, and
 * ReactDOM renders the actual BookCard to static markup for Node's built-in
 * test runner.
 */
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { Router } from 'wouter';

import type { Book } from '../../src/lib/api.ts';

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
  BookCard: (props: { book: Book }) => ReturnType<typeof createElement>;
};

function renderCard(state: { read: boolean; in_progress: boolean }): string {
  const book = {
    id: 1702,
    title: 'The Test Book',
    authors: ['Test Author'],
    series: null,
    series_index: null,
    cover_url: null,
    formats: [],
    read: state.read,
    in_progress: state.in_progress,
  } as Book;
  return renderToStaticMarkup(
    createElement(
      Router,
      { hook: () => ['/', () => undefined] },
      createElement(BookCard, { book }),
    ),
  );
}

describe('BookCard read-state badges', () => {
  test('renders Reading, not Read, for an in-progress book', () => {
    const markup = renderCard({ read: false, in_progress: true });
    assert.match(markup, /data-testid="reading-badge"/);
    assert.match(markup, /aria-label="Reading"/);
    assert.doesNotMatch(markup, /data-testid="read-badge"/);
  });

  test('renders Read, not Reading, for a finished book', () => {
    const markup = renderCard({ read: true, in_progress: false });
    assert.match(markup, /data-testid="read-badge"/);
    assert.match(markup, /aria-label="Read"/);
    assert.doesNotMatch(markup, /data-testid="reading-badge"/);
  });

  test('renders neither read-state badge for an untouched book', () => {
    const markup = renderCard({ read: false, in_progress: false });
    assert.doesNotMatch(markup, /data-testid="read-badge"/);
    assert.doesNotMatch(markup, /data-testid="reading-badge"/);
  });
});
