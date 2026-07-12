import { test, expect, type Page } from '@playwright/test';
import type { Book, BooksPage } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Infinite scrolling on the library grid uses a sentinel to auto-load the next
 * page. A persistent "Load more" button is its keyboard/AT fallback when an
 * observer is unavailable or never delivers an intersecting entry.
 *
 * CI only seeds one real book, so this regression test supplies both library
 * pages at the network boundary. Authentication and the rest of the SPA remain
 * real; only GET /api/v1/books is fulfilled here.
 */

const PER_PAGE = 24;
const TOTAL = 50;

function fakeBook(id: number): Book {
  return {
    id,
    title: `Mock pagination book ${id}`,
    authors: [`Mock author ${id}`],
    series: null,
    series_index: null,
    cover_url: null,
    formats: ['EPUB'],
    tags: [],
    read: false,
    archived: false,
  };
}

function booksPage(page: number): BooksPage {
  const firstId = page === 1 ? 1 : PER_PAGE + 1;
  const lastId = page === 1 ? PER_PAGE : TOTAL;
  return {
    items: Array.from({ length: lastId - firstId + 1 }, (_, index) => fakeBook(firstId + index)),
    page,
    per_page: PER_PAGE,
    total: TOTAL,
  };
}

function gridBookLinks(page: Page) {
  // Quick-edit links end in /edit; each card's primary link ends in /book/<id>.
  return page.locator('main a[href*="/book/"]:not([href$="/edit"])');
}

test.describe('library infinite scroll', () => {
  test.beforeEach(async ({ page }) => {
    // Keep the optional Discover links out of the book-grid count.
    await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
  });

  test('Load more fetches the next page when IntersectionObserver never fires (#704)', async ({ page }) => {
    await page.addInitScript(() => {
      class NeverIntersectingObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
        takeRecords() { return []; }
      }
      window.IntersectionObserver = NeverIntersectingObserver as unknown as typeof IntersectionObserver;
    });

    const requestedPages: number[] = [];
    await page.route('**/api/v1/books?**', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();

      const url = new URL(route.request().url());
      const pageNumber = Number(url.searchParams.get('page'));
      if (url.pathname !== '/api/v1/books' || (pageNumber !== 1 && pageNumber !== 2)) {
        return route.continue();
      }

      expect(url.searchParams.get('per_page')).toBe(String(PER_PAGE));
      expect(url.searchParams.get('sort')).toBe('new');
      requestedPages.push(pageNumber);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(booksPage(pageNumber)),
      });
    });

    const errors = collectPageErrors(page);
    await page.goto('/app');
    await expect(gridBookLinks(page)).toHaveCount(PER_PAGE);

    const loadMore = page.getByRole('button', { name: 'Load more' });
    await expect(loadMore).toBeVisible();
    await expect(loadMore).toBeEnabled();
    await loadMore.focus();
    await expect(loadMore).toBeFocused();

    // The observer stub never invokes its callback. Page 2 is therefore only
    // reachable through the product's manual fallback.
    await loadMore.click();
    await expect(gridBookLinks(page)).toHaveCount(TOTAL);
    await expect(loadMore).toHaveCount(0);

    const hrefs = await gridBookLinks(page).evaluateAll((links) =>
      links.map((link) => (link as HTMLAnchorElement).getAttribute('href')),
    );
    expect(new Set(hrefs).size, 'all mocked books render exactly once').toBe(TOTAL);
    expect(requestedPages, 'the button requests the second SPA library page').toEqual([1, 2]);
    assertNoPageErrors(errors);
  });
});
