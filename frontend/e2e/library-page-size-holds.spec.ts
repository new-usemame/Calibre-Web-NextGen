import { test, expect, type Page } from '@playwright/test';
import type { Book } from '../src/lib/api';

/*
 * #2903 — the Library asks for rows x measured columns books at a time, and that
 * size is part of the list's identity. A column count that changed while pages
 * were still loading (a vertical scrollbar appearing narrows the grid by a
 * column) reset the list to page 1 at the new size; with 1 row per load that
 * could repeat forever. Resizing the window changes the column count just as
 * reliably as a scrollbar does, but without depending on one narrow width, so
 * it is used here: once the list is past page 1 its page size must hold.
 */

const TOTAL = 400;
const book = (id: number): Book => ({
  id, title: `Synthetic ${id}`, authors: ['Mock Author'], series: null, series_index: null,
  cover_url: null, formats: [], read: false, archived: false,
});

// Resolves once no new request has arrived for a short while.
async function settled(page: Page, seen: () => number) {
  let last = -1;
  await expect.poll(async () => {
    const now = seen();
    const stable = now === last;
    last = now;
    if (!stable) await page.waitForTimeout(400);
    return stable;
  }, { timeout: 15_000, intervals: [100] }).toBe(true);
}

test('#2903 the Library keeps its page size when the grid width changes mid-scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(() => {
    localStorage.setItem('cwng:catalog-density-v1', 'dense');
    localStorage.setItem('cwng:catalog-rows-v1', '1');
  });
  await page.route('**/api/v1/auth/me', async (route) => {
    const response = await route.fetch();
    const me = await response.json();
    me.preferences = { ...(me.preferences ?? {}), discover_hidden: true };
    await route.fulfill({ response, json: me });
  });
  const requests: Array<{ page: number; perPage: number }> = [];
  await page.route('**/api/v1/books?**', async (route) => {
    const url = new URL(route.request().url());
    const number = Number(url.searchParams.get('page') || 1);
    const count = Number(url.searchParams.get('per_page') || 24);
    requests.push({ page: number, perPage: count });
    const first = (number - 1) * count;
    const items = Array.from({ length: Math.max(0, Math.min(count, TOTAL - first)) },
      (_, i) => book(first + i + 1));
    await route.fulfill({ json: { items, page: number, per_page: count, total: TOTAL } });
  });

  await page.goto('/app');
  const grid = page.getByTestId('catalog-grid');
  await expect(grid).toBeVisible();
  // One row does not fill the window, so further pages follow on their own.
  await expect.poll(() => requests.length, { timeout: 15_000 }).toBeGreaterThan(1);
  await settled(page, () => requests.length);

  const size = requests[requests.length - 1].perPage;
  const columnsBefore = await grid.getAttribute('data-catalog-column-count');
  const handled = requests.length;
  const lastPage = Math.max(...requests.map((r) => r.page));
  expect(lastPage).toBeGreaterThan(1);

  // A much narrower window: fewer columns, so a live size would change.
  await page.setViewportSize({ width: 900, height: 800 });
  await expect.poll(() => grid.getAttribute('data-catalog-column-count')).not.toBe(columnsBefore);
  await settled(page, () => requests.length);
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await settled(page, () => requests.length);

  // The loaded list was kept: nothing went back to page 1, and every page since
  // continues the sequence at the size the list started with.
  for (const request of requests.slice(handled)) {
    expect(request.page).toBeGreaterThan(lastPage);
    expect(request.perPage).toBe(size);
  }
});
