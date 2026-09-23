import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #2235 regression — a shelf's sidebar count must follow the books the shelf
 * actually shows, without a reload.
 *
 * The server counts a shelf through the same per-user visibility filter the
 * shelf page uses, so archiving (or hiding) a shelved book lowers the count.
 * The SPA never asked for the new number: archive, hide and merge refreshed
 * only the catalog, so the sidebar kept counting a book the shelf no longer
 * listed until a full page load.
 *
 * Pre-fix, the badge stays at 1 after the archive and the first expectation
 * after the click times out.
 *
 * Runs in the serialized server-state lane: archiving hides the book from the
 * shared seed login's catalog for a moment, which would race the parallel
 * lanes' catalog specs.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

test('archiving and unarchiving a shelved book updates its sidebar count without a reload', async ({ page }) => {
  const headers = { 'X-CSRFToken': await csrfToken(page) };
  const books = (await (await page.request.get('/api/v1/books?per_page=1')).json()) as {
    items: Array<{ id: number }>;
  };
  test.skip((books.items ?? []).length < 1, 'no seeded book to shelve');
  const bookId = books.items[0].id;

  const shelfName = `e2e-2235-${Date.now()}`;
  const created = await page.request.post('/api/v1/shelves', { headers, data: { name: shelfName } });
  expect(created.ok(), 'shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;

  try {
    const add = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers });
    expect(add.ok(), 'adding the book to the shelf should succeed').toBeTruthy();

    const errors = collectPageErrors(page);
    await page.goto(`/app/book/${bookId}`);

    // The sidebar entry is the only link carrying the shelf name as its title.
    const count = page.locator(`a[href$="/shelf/${shelfId}"][title="${shelfName}"] span`).last();
    await expect(count).toHaveText('1');

    await page.getByTestId('book-actions-menu').click();
    await page.getByTestId('archive-book-toggle').click();
    await expect(count).toHaveText('0');

    // And back: unarchiving must restore the count the same way.
    await page.getByTestId('book-actions-menu').click();
    await page.getByTestId('archive-book-toggle').click();
    await expect(count).toHaveText('1');

    assertNoPageErrors(errors);
  } finally {
    const detail = await page.request.get(`/api/v1/books/${bookId}`);
    if (detail.ok() && ((await detail.json()) as { archived?: boolean }).archived) {
      await page.request.post(`/api/v1/books/${bookId}/archived`, { headers }).catch(() => {});
    }
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
