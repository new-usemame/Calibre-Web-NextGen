import { test, expect, type Page, type Route } from '@playwright/test';
import type { Book, BooksPage, Me } from '../src/lib/api';

/*
 * #2266 — in selection mode, Shift+click selects (or deselects) the whole run of
 * books from the previous click, as the classic book table did. Requests are
 * stubbed so the shared seed library is untouched.
 */

const BOOKS: Book[] = [1, 2, 3, 4, 5].map((n) => ({
  id: 226600 + n, title: `Range book ${n}`, authors: ['Range Author'],
  series: null, series_index: null, cover_url: null, formats: ['EPUB'],
  tags: [], read: false, archived: false,
}));

const ADMIN: Me = {
  id: 2266,
  name: 'Range select admin',
  locale: 'en',
  theme: 'dark',
  role: { admin: true, edit: true, edit_shelfs: true, delete_books: true },
};

async function mockCatalog(page: Page) {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(ADMIN),
  }));
  await page.route('**/api/v1/shelves', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }),
  }));
  await page.route('**/api/v1/books?**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== '/api/v1/books') return route.continue();
    const body: BooksPage = {
      items: BOOKS,
      page: 1,
      per_page: Number(url.searchParams.get('per_page') || 12),
      total: BOOKS.length,
    };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

const card = (page: Page, n: number) =>
  page.getByRole('button', { name: new RegExp(`^(Select|Deselect) Range book ${n}$`) });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('cwng_discover_hidden_v1', '1'));
  await mockCatalog(page);
});

test('Shift+click selects the run from the previous click, and Shift+click on a selected book clears it', async ({ page }) => {
  await page.goto('/app');
  await page.getByRole('button', { name: 'Select', exact: true }).click();

  await card(page, 2).click({ position: { x: 12, y: 12 } });
  await card(page, 4).click({ position: { x: 12, y: 12 }, modifiers: ['Shift'] });
  await expect(page.getByRole('region', { name: '3 selected' })).toBeVisible();
  for (const n of [2, 3, 4]) await expect(card(page, n)).toHaveAttribute('aria-pressed', 'true');
  for (const n of [1, 5]) await expect(card(page, n)).toHaveAttribute('aria-pressed', 'false');
  // Shift+click must not leave a text selection smeared across the cards.
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? '')).toBe('');

  // The anchor is now book 4; Shift+click on selected book 3 deselects 3..4.
  await card(page, 3).click({ position: { x: 12, y: 12 }, modifiers: ['Shift'] });
  await expect(page.getByRole('region', { name: '1 selected' })).toBeVisible();
  await expect(card(page, 2)).toHaveAttribute('aria-pressed', 'true');

  // A plain click still toggles just one book.
  await card(page, 5).click({ position: { x: 12, y: 12 } });
  await expect(page.getByRole('region', { name: '2 selected' })).toBeVisible();
  await expect(card(page, 4)).toHaveAttribute('aria-pressed', 'false');
});
