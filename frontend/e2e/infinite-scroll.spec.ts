import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Infinite scrolling on the book lists (adopted from community PR #735 by
 * @kurtlieber). The old lists ended in a "Load more" button; now a sentinel at
 * the bottom auto-loads the next page as it scrolls into view.
 *
 * Driven against the Table view: unlike the Library grid (which floats a
 * Discover carousel of extra book links above the paginated grid), the Table is
 * a single paginated list, so every book link on the page is a real row — a
 * clean count. It's one of the five surfaces the shared hook was wired into.
 *
 * This asserts the user-visible contract end-to-end: no "Load more" button
 * remains, scrolling appends the next page(s) up to the full total, it never
 * duplicates a book, and the console stays clean.
 *
 * Needs more than one page of books. CI's E2E job seeds a single book, so this
 * skips there; it runs for real against any library with >24 books (the local
 * dev container is seeded past that). A CI paginated-seed is tracked as a
 * follow-up to the SPA test-harness gap.
 */

const PER_PAGE = 24;

async function totalBooks(page: Page): Promise<number> {
  const res = await page.request.get('/api/v1/books?per_page=1');
  if (!res.ok()) return 0;
  const body = await res.json();
  return body.total ?? 0;
}

function rowHrefs(page: Page): Promise<string[]> {
  return page.locator('table a[href*="/book/"]').evaluateAll((els) =>
    els.map((e) => (e as HTMLAnchorElement).getAttribute('href') || ''),
  );
}

test.describe('library infinite scroll', () => {
  test('scrolling appends pages up to the full total, no button, no dupes', async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto('/app/table');
    await expect(page.locator('table a[href*="/book/"]').first()).toBeVisible();

    const total = await totalBooks(page);
    test.skip(total <= PER_PAGE, `library has ${total} books (≤ one page) — nothing to paginate`);

    // The button-driven affordance is gone; loading is scroll-driven now.
    await expect(page.getByRole('button', { name: /load more/i })).toHaveCount(0);

    const firstPage = await rowHrefs(page);
    expect(firstPage.length, 'first render shows exactly one page of rows').toBe(PER_PAGE);

    // Scroll repeatedly; each pass pulls the next page as the sentinel enters
    // view. Stop when every book is loaded or we stop making progress.
    let count = firstPage.length;
    for (let i = 0; i < 8 && count < total; i++) {
      // Scroll the last row into view so the sentinel below it enters the
      // viewport, whichever ancestor actually scrolls.
      await page.locator('table a[href*="/book/"]').last().scrollIntoViewIfNeeded();
      await expect
        .poll(async () => (await rowHrefs(page)).length, { timeout: 8_000 })
        .toBeGreaterThan(count);
      count = (await rowHrefs(page)).length;
      // One page at a time — never overshoot the running total by more than a page.
      expect(count).toBeLessThanOrEqual(total);
    }

    const finalHrefs = await rowHrefs(page);
    expect(finalHrefs.length, 'every book loaded after scrolling').toBe(total);
    expect(new Set(finalHrefs).size, 'no book row is duplicated across pages').toBe(finalHrefs.length);

    assertNoPageErrors(errors);
  });
});
