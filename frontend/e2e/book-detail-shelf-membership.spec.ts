import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * #1254 regression — the book page must show which shelves the book is on.
 *
 * The classic detail page has always rendered a pill per shelf. The new UI
 * dropped that: it fetched /api/v1/books/<id>/shelves purely to draw checkmarks
 * INSIDE the "Add to shelf" popover, so the only way to answer "what shelves is
 * this book on?" was to open a menu and read it off the toggles. The reporter
 * (@lguerard) asked for the pills back.
 *
 * The fix joins that membership response against the shelf list (both endpoints
 * apply the same server-side visibility filter) and renders a Shelves row in the
 * metadata list, each shelf linking through to its own page.
 *
 * These specs fail on the pre-fix build: the row does not exist at all, so the
 * data-testid never resolves.
 *
 * Self-seeding via the REST API, with the created shelf removed in a finally —
 * same idiom as shelf-render.spec.ts, so the run stays green on any library.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  const body = (await res.json()) as { csrf_token: string };
  return body.csrf_token;
}

// Serial: a small seed can hold a single book, and every spec here shelves and
// unshelves that same one. Run in parallel they contend over its membership and
// fail on each other's state rather than on the product.
test.describe.configure({ mode: 'serial' });

test.describe('#1254 shelf membership on the book page', () => {
  test('a shelved book names its shelf and links to it', async ({ page }) => {
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const booksRes = await page.request.get('/api/v1/books?per_page=1');
    const books = (await booksRes.json()) as { total: number; items: Array<{ id: number }> };
    test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');
    const bookId = books.items[0].id;

    const shelfName = `e2e-1254-${Date.now()}`;
    const created = await page.request.post('/api/v1/shelves', {
      headers,
      data: { name: shelfName },
    });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    const shelfId = ((await created.json()) as { id: number }).id;

    try {
      const add = await page.request.post(`/api/v1/shelves/${shelfId}/books/${bookId}`, { headers });
      expect(add.ok(), 'adding the book to the shelf should succeed').toBeTruthy();

      const errors = collectPageErrors(page);
      await page.goto(`/app/book/${bookId}`);

      // The row itself — absent entirely before the fix.
      const row = page.getByTestId('book-shelves');
      await expect(row).toBeVisible();

      // It must name the shelf, not just exist.
      await expect(row).toContainText(shelfName);

      // And clicking through has to land on that shelf, which is the half that
      // makes it useful rather than decorative.
      // Suffix-matched, not equality: wouter prepends its router base, so the
      // rendered href is /app/shelf/<id> here and <prefix>/app/shelf/<id> under
      // the reverse-proxy subpath rig. Pinning the literal would fail both.
      const link = row.getByRole('link', { name: shelfName });
      await expect(link).toHaveAttribute('href', new RegExp(`/shelf/${shelfId}$`));

      assertNoPageErrors(errors);
    } finally {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });

  test('toggling the shelf in the popover updates the row without a reload', async ({ page }) => {
    // The row reads the same query key the popover mutation already invalidates.
    // That is the whole reason it needs no extra plumbing, so it is also the part
    // most likely to rot silently if someone changes the invalidation later.
    const headers = { 'X-CSRFToken': await csrfToken(page) };

    const booksRes = await page.request.get('/api/v1/books?per_page=1');
    const books = (await booksRes.json()) as { total: number; items: Array<{ id: number }> };
    test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');
    const bookId = books.items[0].id;

    const shelfName = `e2e-1254-live-${Date.now()}`;
    const created = await page.request.post('/api/v1/shelves', { headers, data: { name: shelfName } });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    const shelfId = ((await created.json()) as { id: number }).id;

    try {
      const errors = collectPageErrors(page);
      await page.goto(`/app/book/${bookId}`);

      // Asserted on the link, not the row: the book may be on no shelf at all,
      // in which case the row is absent and a not.toContainText on it would just
      // wait for an element that never arrives.
      await expect(page.getByRole('link', { name: shelfName })).toHaveCount(0);

      await page.getByRole('button', { name: 'Add to shelf' }).click();
      await page.getByRole('button', { name: shelfName }).click();

      // No navigation, no reload — the row has to pick the change up on its own.
      await expect(page.getByTestId('book-shelves')).toContainText(shelfName);

      assertNoPageErrors(errors);
    } finally {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
  });

  test('a book on no shelf renders no shelf row', async ({ page }) => {
    // Guards the empty case: the row is conditional, so an unshelved book must
    // not get a dangling label with nothing under it.
    const booksRes = await page.request.get('/api/v1/books?per_page=10');
    const books = (await booksRes.json()) as { items: Array<{ id: number }> };
    test.skip((books.items ?? []).length < 1, 'no seeded book');

    let unshelved: number | null = null;
    for (const b of books.items) {
      const res = await page.request.get(`/api/v1/books/${b.id}/shelves`);
      const body = (await res.json()) as { shelf_ids: number[] };
      if ((body.shelf_ids ?? []).length === 0) {
        unshelved = b.id;
        break;
      }
    }
    test.skip(unshelved === null, 'every seeded book is already on a shelf');

    await page.goto(`/app/book/${unshelved}`);
    // Wait for the page to actually be the book page before asserting absence,
    // so this can't pass merely because nothing has rendered yet.
    await expect(page.locator('h1').first()).toBeVisible();
    await expect(page.getByTestId('book-shelves')).toHaveCount(0);
  });
});
