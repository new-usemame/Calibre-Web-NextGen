import { test, expect, type Page } from '@playwright/test';
import type { Book, BooksPage } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

/*
 * Infinite scrolling on the library grid uses a sentinel to auto-load the next
 * page. A persistent "Load more" button is its keyboard/AT fallback when an
 * observer is unavailable or never delivers an intersecting entry.
 *
 * CI only seeds a few real books, so the paging test supplies both library
 * pages at the network boundary. Authentication and the rest of the SPA remain
 * real; only GET /api/v1/books is fulfilled there.
 *
 * per_page is NOT a constant. The grid asks for rowsPerLoad × the number of
 * columns it actually measured, so it varies with viewport and density — this
 * spec used to pin it at 24 and failed on every run once the measured size
 * stopped coinciding with the guess (#1130, root-caused in #1144). The mock
 * reads the size off the request instead of dictating it, and serves exactly
 * two pages of whatever size was asked for.
 */

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

function booksPage(page: number, perPage: number): BooksPage {
  const total = perPage * 2;
  const firstId = (page - 1) * perPage + 1;
  const lastId = Math.min(page * perPage, total);
  return {
    items: Array.from({ length: lastId - firstId + 1 }, (_, index) => fakeBook(firstId + index)),
    page,
    per_page: perPage,
    total,
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
    const requestedSizes: number[] = [];
    await page.route('**/api/v1/books?**', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();

      const url = new URL(route.request().url());
      const pageNumber = Number(url.searchParams.get('page'));
      if (url.pathname !== '/api/v1/books' || (pageNumber !== 1 && pageNumber !== 2)) {
        return route.continue();
      }

      const perPage = Number(url.searchParams.get('per_page'));
      expect(perPage, 'the grid asks for a whole number of rows').toBeGreaterThan(0);
      expect(url.searchParams.get('sort')).toBe('new');
      requestedPages.push(pageNumber);
      requestedSizes.push(perPage);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(booksPage(pageNumber, perPage)),
      });
    });

    const errors = collectPageErrors(page);
    await page.goto('/app');

    await expect
      .poll(() => requestedSizes.length, { message: 'the library issues its first page request' })
      .toBeGreaterThan(0);
    const perPage = requestedSizes[0];

    await expect(gridBookLinks(page)).toHaveCount(perPage);

    const loadMore = page.getByRole('button', { name: 'Load more' });
    await expect(loadMore).toBeVisible();
    await expect(loadMore).toBeEnabled();
    await loadMore.focus();
    await expect(loadMore).toBeFocused();

    // The observer stub never invokes its callback. Page 2 is therefore only
    // reachable through the product's manual fallback.
    await loadMore.click();
    await expect(gridBookLinks(page)).toHaveCount(perPage * 2);
    await expect(loadMore).toHaveCount(0);

    const hrefs = await gridBookLinks(page).evaluateAll((links) =>
      links.map((link) => (link as HTMLAnchorElement).getAttribute('href')),
    );
    expect(new Set(hrefs).size, 'all mocked books render exactly once').toBe(perPage * 2);
    expect(requestedPages, 'the button requests the second SPA library page').toEqual([1, 2]);
    expect(new Set(requestedSizes).size, 'both pages are requested at one size').toBe(1);
    assertNoPageErrors(errors);
  });

  test('scrolling auto-loads the next page through the real sentinel (#1144)', async ({ page }) => {
    // The #704 test above stubs IntersectionObserver out to exercise the manual
    // fallback, so nothing covered the observer path itself. It broke without a
    // failing test: the sentinel only renders once results arrive, and the
    // observer hook used to bind whatever `sentinelRef.current` held when its
    // effect ran, never re-binding when the element mounted.
    const requestedPages: number[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (!url.pathname.endsWith('/api/v1/books')) return;
      const requested = Number(url.searchParams.get('page'));
      if (requested > 0) requestedPages.push(requested);
    });

    const errors = collectPageErrors(page);
    await page.goto('/app');

    const cards = gridBookLinks(page);
    await expect(cards.first()).toBeVisible();
    const firstPageCount = await cards.count();

    // Only meaningful when the seed has more books than one page holds.
    const loadMore = page.getByRole('button', { name: 'Load more' });
    test.skip(!(await loadMore.count()), 'seed fits in a single page');

    // Scroll the sentinel into view. No clicking — the button is the fallback,
    // and using it here would pass even with the observer dead.
    for (let i = 0; i < 8 && !requestedPages.includes(2); i++) {
      await page.mouse.wheel(0, 1400);
      await page.waitForTimeout(300);
    }

    expect(requestedPages, 'scrolling reaches page 2 without touching Load more').toContain(2);
    await expect(cards).not.toHaveCount(firstPageCount);
    assertNoPageErrors(errors);
  });

  test('page 1 is requested exactly once per library load (#1144)', async ({ page }) => {
    // Against the REAL backend: no routing, no fulfilment. This counts what the
    // app actually puts on the wire.
    //
    // The grid's column count starts as a guess derived from books_per_page and
    // is corrected by a ResizeObserver measurement. While the query fired on the
    // guess, every load issued page 1 twice — once at the guessed size, then
    // again at the measured one, with the first (expensive) response discarded.
    const firstPageSizes: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (!url.pathname.endsWith('/api/v1/books')) return;
      if (url.searchParams.get('page') !== '1') return;
      firstPageSizes.push(url.searchParams.get('per_page') ?? '');
    });

    const errors = collectPageErrors(page);
    await page.goto('/app');

    await expect
      .poll(() => firstPageSizes.length, { message: 'the library issues its first page request' })
      .toBeGreaterThan(0);

    // Give any late refetch (the one this pins against) room to land before
    // asserting the count — the redundant request followed the first within a
    // frame or two of the measurement.
    await page.waitForTimeout(2000);

    expect(
      firstPageSizes,
      'page 1 is fetched once, at the measured column count — not once per guess and once per measurement',
    ).toHaveLength(1);
    assertNoPageErrors(errors);
  });
});
