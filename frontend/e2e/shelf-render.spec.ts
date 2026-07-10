import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #784 regression — selecting a shelf (manual OR magic) in the new UI rendered a
 * blank screen on v4.1.8; a browser refresh could not recover it.
 *
 * Root cause was a React Rules-of-Hooks violation. The infinite-scroll
 * `useIntersectionObserver` hook (adopted in v4.1.8) was called AFTER the
 * loading/error early-returns in Shelf.tsx and MagicShelfView.tsx. On the first
 * (loading) render the guard returned early, before the hook ran; once the data
 * resolved the guard was skipped and the hook ran — so the hook count jumped
 * loading→loaded and React threw "Rendered more hooks than during the previous
 * render", crashing the page to blank. Catalog/Table wired the same hook in
 * BEFORE their returns and were unaffected, which matched the report (the main
 * book list worked, only shelves broke).
 *
 * The crash is content-independent — it fires as soon as the query resolves past
 * the guard — so a single-book library (CI's seed) is enough to exercise it.
 * Each test self-seeds via the REST API and cleans up the shelf it creates.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  const body = (await res.json()) as { csrf_token: string };
  return body.csrf_token;
}

test.describe('#784 shelves render (no Rules-of-Hooks crash)', () => {
  test('a manual shelf with a book renders instead of blanking', async ({ page }) => {
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const booksRes = await page.request.get('/api/v1/books?per_page=1');
    const books = (await booksRes.json()) as { total: number; items: Array<{ id: number }> };
    test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');
    const bookId = books.items[0].id;

    const created = await page.request.post('/api/v1/shelves', {
      headers,
      data: { name: `e2e-784-${Date.now()}` },
    });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    const shelfId = ((await created.json()) as { id: number }).id;

    try {
      const add = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers });
      expect(add.ok(), 'adding the book to the shelf should succeed').toBeTruthy();

      const errors = collectPageErrors(page);
      await page.goto(`/app/shelf/${shelfId}`);

      // The shelf must render its content (a book card) — not a blank screen.
      await expect(page.locator('a[href*="/book/"]').first()).toBeVisible();
      assertNoPageErrors(errors);
    } finally {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });

  test('a magic shelf renders instead of blanking', async ({ page }) => {
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const created = await page.request.post('/magicshelf', {
      headers,
      data: {
        name: `e2e-784-magic-${Date.now()}`,
        icon: '🪄',
        rules: { condition: 'OR', rules: [{ id: 'title', operator: 'contains', value: 'a' }] },
      },
    });
    expect(created.ok(), 'magic shelf create should succeed').toBeTruthy();
    const body = (await created.json()) as { success: boolean; shelf_id?: number };
    expect(body.success, 'magic shelf create should report success').toBeTruthy();
    const shelfId = body.shelf_id;
    expect(shelfId, 'magic shelf create should return an id').toBeTruthy();

    try {
      const errors = collectPageErrors(page);
      await page.goto(`/app/magic/${shelfId}`);

      // Past the crash point: the smart-shelf shell (its "Library" back link)
      // renders whether or not the rule matched any books.
      await expect(page.getByRole('link', { name: /library/i }).first()).toBeVisible();
      assertNoPageErrors(errors);
    } finally {
      await page.request.post(`/magicshelf/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });
});
