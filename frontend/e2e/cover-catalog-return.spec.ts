import { test, expect, type Page } from '@playwright/test';
import type { Book, BookDetail } from '../src/lib/api';
import { collectPageErrors, assertNoPageErrors } from './utils';

// #1126: the target is on an earlier accumulated page. A highest-page
// refetch cannot repair it. All navigation is client-side; only transport
// and synthetic books are substituted, including the committed write.
const ID = 9001;
const TITLE = 'Cover refresh on an earlier page';
const BEFORE = `/cover/${ID}/sm?c=before`;
const AFTER = `/cover/${ID}/md?c=after`;
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC', 'base64');
const item = (id: number): Book => ({ id, title: id === ID ? TITLE : `Filler ${id}`,
  authors: ['Mock Author'], series: null, series_index: null, cover_url: BEFORE,
  formats: [], read: false, archived: false });
const detail = (cover: string): BookDetail => ({
  id: ID, title: TITLE, authors: [{ id: 1, name: 'Mock Author' }],
  series: null, series_index: '', rating: null, cover_url: cover,
  pubdate: null, date_added: null, last_modified: null, description_html: null,
  original_filename: null, tags: [], languages: [], publishers: [], identifiers: [],
  formats: [], read: false, archived: false, favorited: false, hidden: false,
  in_progress: false, kosync_progress: null, kosync_progress_timestamp: null,
  kosync_progress_created_at: null,
});
const grid = (page: Page) => page.getByTestId('catalog-grid').locator('a[aria-label^="Open details for"]');
const target = (page: Page) => page.getByTestId('catalog-grid').locator(`a[href$="/book/${ID}"]`);

for (const scenario of ['completed read', 'Back while read pending', 'failed read after page-one return'] as const) {
  test(`${scenario}: the committed cover reaches the restored catalogue`, async ({ page }) => {
    let committed = false;
    let firstPageReads = 0;
    let release!: () => void;
    let readStarted!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { readStarted = resolve; });
    await page.addInitScript(() => {
      localStorage.setItem('cwng:catalog-density-v1', 'compact');
      localStorage.setItem('cwng:catalog-rows-v1', '2');
      class NeverIntersectingObserver {
        observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
      }
      window.IntersectionObserver = NeverIntersectingObserver as unknown as typeof IntersectionObserver;
    });
    await page.route('**/api/v1/auth/me', async (route) => {
      const response = await route.fetch(); const me = await response.json();
      // Match this fixture's measured grid so Back does not reset paging while
      // replacing an unrelated account's fallback column estimate.
      me.display = { ...(me.display ?? {}), books_per_page: (page.viewportSize()?.width ?? 1280) < 600 ? 4 : 14 };
      me.preferences = { ...(me.preferences ?? {}), discover_hidden: true };
      await route.fulfill({ response, json: me });
    });
    await page.route('**/api/v1/books?**', async (route) => {
      const url = new URL(route.request().url());
      const number = Number(url.searchParams.get('page') || 1);
      const count = Number(url.searchParams.get('per_page') || 24);
      // Book detail also fetches related author books. Count only this plain
      // library scope, whose earlier page must not mask snapshot repair.
      if (number === 1 && !url.searchParams.has('author')) firstPageReads++;
      const items = Array.from({ length: count }, (_, i) => {
        const book = item(number === 1 && i === 0 ? ID : 10_000 + (number - 1) * count + i);
        if (book.id === ID && committed) book.cover_url = AFTER;
        return book;
      });
      await route.fulfill({ json: { items, page: number, per_page: count, total: count * 2 } });
    });
    await page.route(`**/api/v1/books/${ID}`, async (route) => {
      if (committed && scenario !== 'completed read') {
        readStarted(); await pending;
        if (scenario === 'failed read after page-one return') {
          return route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'Synthetic follow-up read failure' } } });
        }
      }
      await route.fulfill({ json: detail(committed ? AFTER : BEFORE) });
    });
    await page.route(`**/api/v1/books/${ID}/review`, (route) => route.fulfill({ json: { review: null } }));
    await page.route('**/book/*/cover/state', (route) => route.fulfill({ json: {
      locked: false, ereader_enabled: false,
      ereader_defaults: { aspect: 'kobo_libra_color', fill_mode: 'edge_mirror', color: '' },
    } }));
    await page.route('**/book/*/cover/candidates*', (route) => route.fulfill({ json: { candidates: [], providers: [], query: '' } }));
    await page.route('**/book/*/cover/apply', async (route) => {
      committed = true;
      await route.fulfill({ json: { ok: true, cover_url: AFTER } });
    });
    await page.route(/\/cover\/\d+\/(sm|md|og)\?/, (route) => route.fulfill({ contentType: 'image/png', body: PNG }));
    const errors = collectPageErrors(page);
    try {
      await page.goto('/app');
      await expect(target(page)).toBeVisible();
      const count = await grid(page).count();
      if (scenario !== 'failed read after page-one return') {
        await page.getByRole('button', { name: 'Load more', exact: true }).click();
        await expect(grid(page)).toHaveCount(count * 2);
      }
      const pagesBefore = firstPageReads;
      if (scenario === 'completed read') await page.screenshot({ path: test.info().outputPath('catalogue-before.jpg'), type: 'jpeg', quality: 75 });
      await target(page).click();
      await page.getByTestId('book-actions-menu').click();
      await page.getByTestId('book-actions-menu-list').getByRole('menuitem', { name: 'Edit cover…', exact: true }).click();
      await page.getByRole('tab', { name: 'Upload', exact: true }).click();
      await page.getByLabel('Choose a cover image to upload').setInputFiles({ name: 'cover.png', mimeType: 'image/png', buffer: PNG });
      await page.getByRole('button', { name: 'Upload as cover', exact: true }).click();
      await expect(page.getByText('Cover updated.', { exact: true })).toBeVisible();
      if (scenario !== 'completed read') await started;
      else await expect.poll(() => page.getByRole('img', { name: TITLE, exact: true }).first().getAttribute('src')).toContain('c=after');
      await page.goBack(); await page.waitForURL(`**/book/${ID}`);
      await page.goBack(); await page.waitForURL(/\/app\/?$/);
      await expect(target(page)).toBeVisible();
      if (scenario !== 'completed read') {
        // Let the remounted page's own query finish before releasing the
        // separate detail read. Page-one failure must not empty fresh data.
        if (scenario === 'failed read after page-one return') {
          await expect.poll(() => firstPageReads).toBeGreaterThan(pagesBefore);
          await expect(target(page).locator('img')).toHaveAttribute('src', /c=after/);
        } else await expect(target(page).locator('img')).toHaveAttribute('src', /c=before/);
        if (scenario === 'failed read after page-one return') {
          const failedRead = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${ID}`) && response.status() === 500);
          release();
          await (await failedRead).finished();
          // Cross the rendered effects after the response, not merely the
          // already-correct list render that preceded the failure.
          await page.evaluate(() => new Promise<void>((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
          }));
        } else release();
      }
      await expect(target(page).locator('img')).toHaveAttribute('src', /c=after/);
      await expect(grid(page)).toHaveCount(count * (scenario === 'failed read after page-one return' ? 1 : 2));
      expect(await grid(page).first().getAttribute('href')).toMatch(new RegExp(`/book/${ID}$`));
      if (scenario !== 'failed read after page-one return') expect(firstPageReads).toBe(pagesBefore);
      await page.screenshot({ path: test.info().outputPath('catalogue-after.jpg'), type: 'jpeg', quality: 75 });
      if (scenario === 'failed read after page-one return') {
        // This cell intentionally returns HTTP 500. Retain its raw console
        // messages, while still rejecting an uncaught JavaScript exception.
        await test.info().attach('expected-read-failure-console', { body: JSON.stringify(errors), contentType: 'application/json' });
        expect(errors.filter((error) => error.startsWith('pageerror:'))).toEqual([]);
      } else assertNoPageErrors(errors);
    } finally { release(); }
  });
}
